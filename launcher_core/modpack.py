"""Installing a Modrinth modpack (``.mrpack``) into the game folder.

The launcher used to ship its own hand-picked mod folder. That worked, and
it aged badly: every Sodium or Fabric API bump meant rebuilding and
re-releasing an installer, and the set drifted from what the wider modding
community actually tests together. This module replaces it with
`Fabulously Optimized <https://modrinth.com/modpack/fabulously-optimized>`_,
fetched from Modrinth at first launch.

An ``.mrpack`` is a zip containing ``modrinth.index.json`` - a list of files
with a path, a size, one or more download URLs and SHA-1/SHA-512 hashes -
plus an ``overrides/`` tree copied verbatim into the game folder.

What this module is careful about, and why:

* **Every file is hash-verified.** The index carries a SHA-512 per file and
  the pack archive itself is pinned in ``data/catalog.json``. A mod jar is
  code that will be executed by the player's JVM, so "it downloaded" is not
  the same as "it is the right file". A mismatch is discarded, not installed.
* **HTTPS only, host allow-listed, redirects re-checked.** Same rule as the
  map download (:mod:`launcher_core.resource_downloader`), with its own
  allow-list because modpack content comes from Modrinth's CDN rather than
  from Hugging Face.
* **Zip Slip is blocked** when unpacking ``overrides/``: a member that
  resolves outside the destination is refused rather than written.
* **Installation is transactional per file, and recorded.** A manifest of
  what this pack installed is written into the game folder, so the next
  launch can confirm the install in a directory listing instead of a
  network round trip - and so a pack *upgrade* knows which jars it owns and
  may delete.
* **Foreign jars are swept.** Anything in ``mods/`` that the manifest does
  not claim is removed. That is what migrates a player off the old bundled
  mod set without asking them to find the folder themselves, and it is why a
  stale ViaFabricPlus cannot survive to cause an "outdated client" kick.

Nothing here trusts the index: paths, URLs and hashes are all validated
before they are used.
"""

import hashlib
import json
import os
import posixpath
import shutil
import time
import zipfile
from urllib.parse import urlparse

import requests

from .constants import (
    DOWNLOAD_BACKOFF_MAX_SECONDS, DOWNLOAD_BACKOFF_SECONDS, DOWNLOAD_MAX_RETRIES,
    MODPACK, MODPACK_EXTRA_MODS, is_allowed_modpack_host,
)
from .exceptions import LauncherError
from .install_state import fingerprint
from .transfer import format_bytes, retry_delays
from .version import USER_AGENT

REQUEST_TIMEOUT = (15, 60)
CHUNK_SIZE = 1024 * 256

MANIFEST_NAME = ".wizard-modpack.json"

# A pack index is small; anything this large is not one.
MAX_INDEX_BYTES = 4 * 1024 * 1024
MAX_PACK_BYTES = 64 * 1024 * 1024
# No single mod jar in a client pack is anywhere near this.
MAX_FILE_BYTES = 256 * 1024 * 1024

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}

# Only these subtrees of the game folder may be written by a pack. The index
# format permits an arbitrary relative path, and "arbitrary path chosen by a
# remote file" is not something to hand a writer without a fence.
ALLOWED_ROOTS = ("mods", "config", "resourcepacks", "shaderpacks")


class _Transient(Exception):
    """Internal: worth another attempt. Never reaches the UI."""


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
def _safe_relative_path(raw):
    """Normalise an index path, or return ``None`` if it is not safe to use.

    Refuses absolute paths, drive letters, backslashes, ``..`` segments and
    anything outside :data:`ALLOWED_ROOTS`.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    candidate = raw.strip().replace("\\", "/")
    if candidate.startswith("/") or ":" in candidate:
        return None
    normalised = posixpath.normpath(candidate)
    if normalised.startswith("../") or normalised in ("..", "."):
        return None
    root = normalised.split("/", 1)[0]
    if root not in ALLOWED_ROOTS:
        return None
    return normalised


def _within(root, path):
    root = os.path.realpath(root)
    target = os.path.realpath(path)
    return target == root or target.startswith(root + os.sep)


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------
def _check_host(url, what="modpack download"):
    parsed = urlparse(url or "")
    if parsed.scheme != "https":
        raise LauncherError(
            f"Refusing an insecure {what} URL (must be https):\n{url}\n\n"
            "How to fix:\n"
            "1. This usually means the launcher is out of date - reinstall the latest version."
        )
    host = (parsed.hostname or "").lower()
    if not is_allowed_modpack_host(host):
        raise LauncherError(
            f"Refusing a {what} from an unexpected server: {host}\n\n"
            "The launcher only downloads mods from Modrinth. If you did not change "
            "any settings, this could mean your connection is being intercepted.\n\n"
            "How to fix:\n"
            "1. Try again on a different network (mobile hotspot), or\n"
            "2. Temporarily disable any 'HTTPS scanning' feature in your antivirus."
        )


def _sha512_of(path):
    digest = hashlib.sha512()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _attempt_fetch(url, dest, expected_sha512, max_bytes):
    _check_host(url)
    digest = hashlib.sha512()
    written = 0
    try:
        with requests.get(url, stream=True, timeout=REQUEST_TIMEOUT,
                          headers={"User-Agent": USER_AGENT},
                          allow_redirects=True) as resp:
            for hop in list(resp.history) + [resp]:
                _check_host(hop.url, "redirect")
            if resp.status_code in RETRYABLE_STATUS:
                raise _Transient(f"server returned {resp.status_code}")
            resp.raise_for_status()

            # Written to a sibling temp file and renamed at the end, so an
            # interrupted download can never be mistaken for an installed mod.
            tmp = dest + ".part"
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(tmp, "wb") as f:
                for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > max_bytes:
                        raise LauncherError(
                            f"{os.path.basename(dest)} is unexpectedly large; aborting."
                        )
                    f.write(chunk)
                    digest.update(chunk)
    except (LauncherError, _Transient):
        raise
    except requests.exceptions.SSLError as e:
        raise LauncherError(
            f"The secure connection to Modrinth could not be verified.\n\nDetails: {e}\n\n"
            "How to fix:\n"
            "1. Check that your computer's date and time are correct,\n"
            "2. Temporarily disable 'HTTPS scanning' in your antivirus, or\n"
            "3. Try a different network."
        )
    except (requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.ChunkedEncodingError) as e:
        raise _Transient(str(e) or type(e).__name__)
    except requests.exceptions.HTTPError as e:
        raise LauncherError(
            f"Modrinth refused to send {os.path.basename(dest)}.\n\nDetails: {e}\n\n"
            "How to fix:\n"
            "1. Press Play again in a few minutes, or\n"
            "2. Check for a launcher update - the mod list may have moved."
        )
    except requests.RequestException as e:
        raise _Transient(str(e) or type(e).__name__)
    except OSError as e:
        raise LauncherError(
            f"Could not write {os.path.basename(dest)} to disk.\n\nDetails: {e}\n\n"
            "How to fix:\n"
            "1. Free up disk space and press Play again, or\n"
            "2. Check that your antivirus is not blocking the launcher's folder."
        )

    actual = digest.hexdigest()
    if expected_sha512 and actual.lower() != expected_sha512.lower():
        # A wrong file here is a jar that would be executed. Never keep it,
        # and retry - a truncated proxy response is the usual explanation.
        try:
            os.remove(dest + ".part")
        except OSError:
            pass
        raise _Transient("the downloaded file did not match its fingerprint")

    os.replace(dest + ".part", dest)
    return written


def _fetch(url, dest, expected_sha512, log, max_bytes=MAX_FILE_BYTES):
    """Download one file, retrying transient failures, verifying the hash."""
    delays = retry_delays(DOWNLOAD_MAX_RETRIES, DOWNLOAD_BACKOFF_SECONDS,
                          DOWNLOAD_BACKOFF_MAX_SECONDS)
    last_reason = ""
    for attempt in range(len(delays) + 1):
        try:
            return _attempt_fetch(url, dest, expected_sha512, max_bytes)
        except _Transient as e:
            last_reason = str(e)
            if attempt >= len(delays):
                break
            delay = delays[attempt]
            log(f"{os.path.basename(dest)}: {last_reason}. Retrying in {delay:.0f}s...")
            time.sleep(delay)

    raise LauncherError(
        f"Could not download {os.path.basename(dest)} from Modrinth.\n\n"
        f"Last problem: {last_reason}\n\n"
        "How to fix:\n"
        "1. Check your internet connection and press Play again,\n"
        "2. Try a different network, such as a phone hotspot, or\n"
        "3. Temporarily disable 'HTTPS scanning' in your antivirus."
    )


# ---------------------------------------------------------------------------
# Index parsing
# ---------------------------------------------------------------------------
def _parse_index(raw):
    """Validate ``modrinth.index.json`` into ``[{path, url, sha512, size}]``.

    Anything malformed is dropped rather than guessed at. An index that
    yields no usable files raises, because installing nothing and reporting
    success would leave the player with a client that cannot join.
    """
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise LauncherError(
            f"The modpack index could not be read.\n\nDetails: {e}\n\n"
            "How to fix:\n"
            "1. Use \"Clear Cache\" and let the launcher download it again."
        )
    if not isinstance(data, dict):
        raise LauncherError("The modpack index has an unexpected shape.")

    files = []
    for entry in data.get("files") or []:
        if not isinstance(entry, dict):
            continue
        path = _safe_relative_path(entry.get("path"))
        if not path:
            continue

        # A pack may mark a file as server-only; this launcher installs a
        # client, so those are skipped rather than downloaded and ignored.
        env = entry.get("env")
        if isinstance(env, dict) and str(env.get("client", "")).lower() == "unsupported":
            continue

        downloads = [
            url for url in entry.get("downloads") or []
            if isinstance(url, str) and url.startswith("https://")
        ]
        # Prefer a mirror we are allowed to talk to; a pack listing GitHub
        # first would otherwise fail the host check on a file we can get
        # perfectly well from Modrinth's own CDN.
        allowed = [url for url in downloads if is_allowed_modpack_host(urlparse(url).hostname)]
        if not allowed:
            continue

        hashes = entry.get("hashes")
        sha512 = ""
        if isinstance(hashes, dict) and isinstance(hashes.get("sha512"), str):
            sha512 = hashes["sha512"].strip().lower()

        try:
            size = int(entry.get("fileSize", 0) or 0)
        except (TypeError, ValueError):
            size = 0

        files.append({"path": path, "url": allowed[0], "sha512": sha512, "size": size})

    if not files:
        raise LauncherError(
            "The modpack index listed no installable files.\n\n"
            "How to fix:\n"
            "1. Use \"Clear Cache\" and press Play again, or\n"
            "2. Check for a launcher update."
        )

    dependencies = data.get("dependencies")
    return files, (dependencies if isinstance(dependencies, dict) else {})


# ---------------------------------------------------------------------------
# Installer
# ---------------------------------------------------------------------------
class ModpackInstaller:
    """Install and maintain the client modpack inside the game folder."""

    def __init__(self, log_fn, mc_dir, cache_dir, progress_fn=None, state=None):
        self.log = log_fn
        self.mc_dir = mc_dir
        self.cache_dir = cache_dir
        self.progress = progress_fn or (lambda fraction, message=None: None)
        self.state = state

    # ------------------------------------------------------------------
    @property
    def manifest_path(self):
        return os.path.join(self.mc_dir, MANIFEST_NAME)

    def _desired_fingerprint(self):
        """What "already installed" means for the pack we currently want."""
        return fingerprint(
            MODPACK.get("id"), MODPACK.get("version"), MODPACK.get("url"),
            MODPACK.get("sha512"),
            *[f"{m.get('path')}|{m.get('sha512')}" for m in MODPACK_EXTRA_MODS],
        )

    def _read_manifest(self):
        try:
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    def _write_manifest(self, paths, wanted):
        data = {
            "id": MODPACK.get("id"),
            "name": MODPACK.get("name"),
            "version": MODPACK.get("version"),
            "fingerprint": wanted,
            "installed_at": int(time.time()),
            "files": sorted(paths),
        }
        tmp = self.manifest_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self.manifest_path)
        except Exception as e:
            self.log(f"Could not record the installed mod list: {e}")

    # ------------------------------------------------------------------
    def is_installed(self):
        """Cheap, offline check: right pack, and its files are still there.

        Deliberately spot-checks rather than hashing every jar. A full
        verification of fifty files on every launch is exactly the cost this
        is meant to avoid, and a truncated jar surfaces immediately as a
        Fabric crash with a far clearer message than anything produced here.
        """
        wanted = self._desired_fingerprint()
        manifest = self._read_manifest()
        if not manifest or manifest.get("fingerprint") != wanted:
            return False
        files = manifest.get("files")
        if not isinstance(files, list) or not files:
            return False
        for relative in files:
            if not isinstance(relative, str):
                return False
            if not os.path.isfile(os.path.join(self.mc_dir, relative)):
                return False
        return True

    # ------------------------------------------------------------------
    def ensure(self):
        """Install the pack if it is not already installed. Returns a summary."""
        if not MODPACK.get("url"):
            raise LauncherError(
                "No modpack is configured in this build of the launcher.\n\n"
                "How to fix:\n"
                "1. Reinstall Wizard Launcher from the official installer."
            )

        wanted = self._desired_fingerprint()
        if self.is_installed():
            name = MODPACK.get("name") or "Modpack"
            self.log(f"{name} {MODPACK.get('version', '')} already installed.")
            if self.state:
                self.state.mark("modpack", wanted)
            return {"installed": False, "files": 0}

        return self._install(wanted)

    def _install(self, wanted):
        name = MODPACK.get("name") or "Modpack"
        version = MODPACK.get("version") or ""
        self.log(f"Installing {name} {version} (first run only)...")
        self.progress(0.0, f"Fetching {name}...")

        # Read before we overwrite it: this records which jars a past install
        # of ours put in mods/, so the sweep can tell a stale bundled mod from
        # one the player added themselves.
        previous = self._read_manifest()

        os.makedirs(self.mc_dir, exist_ok=True)
        os.makedirs(self.cache_dir, exist_ok=True)

        pack_path = os.path.join(self.cache_dir, f"{MODPACK.get('id', 'modpack')}-{version}.mrpack")
        expected = str(MODPACK.get("sha512") or "").strip().lower()

        # A cached archive is reused only when it still hashes correctly;
        # otherwise it is thrown away rather than trusted.
        if os.path.isfile(pack_path) and expected:
            try:
                if _sha512_of(pack_path).lower() != expected:
                    os.remove(pack_path)
            except OSError:
                pass
        if not os.path.isfile(pack_path):
            _fetch(MODPACK["url"], pack_path, expected, self.log, max_bytes=MAX_PACK_BYTES)

        try:
            with zipfile.ZipFile(pack_path) as pack:
                try:
                    info = pack.getinfo("modrinth.index.json")
                except KeyError:
                    raise LauncherError(
                        "The modpack archive is missing its index and was rejected.\n\n"
                        "How to fix:\n"
                        "1. Use \"Clear Cache\" and press Play again."
                    )
                if info.file_size > MAX_INDEX_BYTES:
                    raise LauncherError("The modpack index is implausibly large; aborting.")
                files, dependencies = _parse_index(pack.read("modrinth.index.json"))
                loader = dependencies.get("fabric-loader")
                if loader:
                    self.log(f"{name} targets Fabric Loader {loader} or newer.")

                installed = self._download_files(files, name)
                installed += self._apply_overrides(pack)
        except zipfile.BadZipFile:
            try:
                os.remove(pack_path)
            except OSError:
                pass
            raise LauncherError(
                f"The downloaded {name} file is damaged.\n\n"
                "How to fix:\n"
                "1. Press Play again to download it fresh - the damaged copy has been deleted."
            )

        installed += self._download_extras()
        self._sweep_foreign_mods(installed, previous)
        self._write_manifest(installed, wanted)
        if self.state:
            self.state.mark("modpack", wanted)

        self.log(f"{name} {version} ready - {len(installed)} files.")
        self.progress(1.0, f"{name} ready")
        return {"installed": True, "files": len(installed)}

    # ------------------------------------------------------------------
    def _download_files(self, files, pack_name):
        """Fetch every index entry, skipping ones already present and correct."""
        installed = []
        total = len(files)
        total_bytes = sum(entry["size"] for entry in files) or 1
        done_bytes = 0

        for index, entry in enumerate(files, start=1):
            dest = os.path.join(self.mc_dir, entry["path"].replace("/", os.sep))
            if not _within(self.mc_dir, dest):
                self.log(f"Skipping a modpack entry with an unsafe path: {entry['path']}")
                continue

            reusable = False
            if os.path.isfile(dest) and entry["sha512"]:
                try:
                    reusable = _sha512_of(dest).lower() == entry["sha512"]
                except OSError:
                    reusable = False

            if not reusable:
                self.progress(
                    done_bytes / total_bytes,
                    f"{pack_name}  ·  {index} of {total}  ·  {os.path.basename(entry['path'])}",
                )
                _fetch(entry["url"], dest, entry["sha512"], self.log)

            installed.append(entry["path"])
            done_bytes += entry["size"]
            self.progress(min(1.0, done_bytes / total_bytes),
                          f"{pack_name}  ·  {index} of {total}  ·  "
                          f"{format_bytes(done_bytes)} of {format_bytes(total_bytes)}")
        return installed

    def _apply_overrides(self, pack):
        """Copy the pack's ``overrides/`` tree into the game folder.

        Only run on a fresh install or a version change, because these are
        config defaults - re-applying them on every launch would quietly undo
        whatever the player changed in-game.
        """
        installed = []
        prefixes = ("overrides/", "client-overrides/")
        for info in pack.infolist():
            for prefix in prefixes:
                if not info.filename.startswith(prefix):
                    continue
                relative = info.filename[len(prefix):]
                if not relative or info.is_dir():
                    continue
                safe = _safe_relative_path(relative)
                if not safe:
                    continue
                dest = os.path.join(self.mc_dir, safe.replace("/", os.sep))
                if not _within(self.mc_dir, dest):
                    self.log(f"Skipping an override with an unsafe path: {relative}")
                    continue
                try:
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with pack.open(info) as src, open(dest, "wb") as out:
                        shutil.copyfileobj(src, out, CHUNK_SIZE)
                    installed.append(safe)
                except OSError as e:
                    self.log(f"Could not write {safe}: {e}")
                break
        if installed:
            self.log(f"Applied {len(installed)} modpack config file(s).")
        return installed

    def _download_extras(self):
        """Mods this launcher adds on top of the pack.

        Currently just the skin loader: the world server runs in offline
        mode, so it never sends a skin texture and every player renders as
        the default Steve. Fixing that client-side keeps the server vanilla.
        """
        installed = []
        for extra in MODPACK_EXTRA_MODS:
            path = _safe_relative_path(extra.get("path"))
            url = str(extra.get("url") or "")
            if not path or not url.startswith("https://"):
                continue
            dest = os.path.join(self.mc_dir, path.replace("/", os.sep))
            sha512 = str(extra.get("sha512") or "").strip().lower()

            if os.path.isfile(dest) and sha512:
                try:
                    if _sha512_of(dest).lower() == sha512:
                        installed.append(path)
                        continue
                except OSError:
                    pass

            label = extra.get("name") or os.path.basename(path)
            self.log(f"Installing {label}...")
            self.progress(None, f"Installing {label}...")
            _fetch(url, dest, sha512, self.log)
            installed.append(path)
        return installed

    def _sweep_foreign_mods(self, installed, previous=None):
        """Delete stale launcher-owned jars in ``mods/``, keeping the player's.

        This stops a jar from a previous pack version surviving into a new one
        - two copies of Sodium is an instant crash on launch - and migrates a
        player off the old bundled mod set.

        The rule turns on whether the launcher already owns this folder:

        * **A previous manifest exists.** Then a jar the player dropped in was
          never one of ours, so it is not in the previous file list and is left
          untouched. Only jars a past install of ours put here, and no longer
          wants, are removed. This is what lets players keep their own mods
          across a modpack upgrade instead of losing them on the next launch.
        * **No previous manifest.** This is the first migration off the old
          bundled set, where every jar in the folder was shipped by the
          launcher. Then anything not in the new install is swept, as before.
        """
        mods_dir = os.path.join(self.mc_dir, "mods")
        if not os.path.isdir(mods_dir):
            return
        keep = {os.path.normcase(p.replace("/", os.sep)) for p in installed}

        owned = None
        if previous and isinstance(previous.get("files"), list):
            owned = {
                os.path.normcase(p.replace("/", os.sep))
                for p in previous["files"] if isinstance(p, str)
            }

        removed = 0
        for filename in os.listdir(mods_dir):
            if not filename.lower().endswith((".jar", ".jar.disabled")):
                continue
            relative = os.path.normcase(os.path.join("mods", filename))
            if relative in keep:
                continue
            # Once the launcher owns the folder, a jar it never installed is the
            # player's - leave it in place.
            if owned is not None and relative not in owned:
                continue
            try:
                os.remove(os.path.join(mods_dir, filename))
                removed += 1
            except OSError as e:
                self.log(f"Could not remove the old mod {filename}: {e}")
        if removed:
            self.log(f"Removed {removed} mod(s) that are not part of the modpack.")
