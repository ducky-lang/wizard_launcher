"""Downloading and unpacking the map / resource pack.

Security notes, because this module fetches remote content and writes it to
the user's disk:

* **HTTPS only, host allow-listed.** Both the initial URL and every redirect
  hop are checked against :data:`constants.ALLOWED_DOWNLOAD_DOMAINS`. A
  redirect off to an unexpected host aborts the download.
* **Zip Slip is blocked.** An earlier version called
  ``ZipFile.extractall()`` directly. A crafted archive containing an entry
  named ``../../../../Users/x/AppData/Roaming/...`` would have been happily
  written outside the destination folder. Every member is now resolved and
  rejected unless it lands inside the target directory.
* **Optional SHA-256 pinning.** When a digest is configured in
  ``data/catalog.json`` the archive is verified before anything is extracted.
* **Nothing is trusted until it is complete.** Extraction happens in a
  staging folder that is only swapped into place at the end, so an
  interrupted download can never leave a half-installed world that looks
  valid on the next run.

Reliability notes, because this is a 1.4 GB download and the player is
watching it:

* **It resumes.** The partial file is kept and continued with a Range
  request. Losing Wi-Fi at 90% used to mean starting over.
* **It retries itself.** Transient failures - timeouts, dropped
  connections, a CDN 503 - are retried with exponential backoff before the
  player is ever shown an error.
* **It says how long is left.** Speed and ETA come from
  :mod:`launcher_core.transfer`.
"""

import hashlib
import os
import shutil
import time
import zipfile
from urllib.parse import urlparse

import requests

from .constants import (
    DOWNLOAD_BACKOFF_MAX_SECONDS, DOWNLOAD_BACKOFF_SECONDS,
    DOWNLOAD_MAX_RETRIES, RESOURCE_SHA256, is_allowed_download_host,
)
from .exceptions import LauncherError
from .transfer import ProgressReporter, format_bytes, retry_delays
from .version import USER_AGENT

REQUEST_TIMEOUT = (15, 60)   # (connect, read)
CHUNK_SIZE = 1024 * 512
MAX_ARCHIVE_BYTES = 4 * 1024 * 1024 * 1024   # 4 GB sanity ceiling
MAX_EXPANDED_RATIO = 200                     # zip-bomb guard

# Server responses worth trying again. Everything else (403, 404, a bad
# digest) is a real answer and retrying only wastes the player's time.
RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


class _TransientDownloadError(Exception):
    """Internal: worth another attempt. Never reaches the UI."""


def _check_host(url, what="download"):
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise LauncherError(
            f"Refusing an insecure {what} URL (must be https):\n{url}\n\n"
            "How to fix:\n"
            "1. This usually means the launcher is out of date - reinstall the latest version."
        )
    host = (parsed.hostname or "").lower()
    if not is_allowed_download_host(host):
        raise LauncherError(
            f"Refusing a {what} from an unexpected server: {host}\n\n"
            "The launcher only downloads game files from its official host. "
            "If you did not change any settings, this could mean your connection "
            "is being intercepted.\n\n"
            "How to fix:\n"
            "1. Try again on a different network (mobile hotspot), or\n"
            "2. Temporarily disable any 'HTTPS scanning' feature in your antivirus."
        )


def _safe_extract(zip_path, dest_dir, log_fn):
    """Extract every member, refusing anything that would escape dest_dir."""
    dest_root = os.path.realpath(dest_dir)
    os.makedirs(dest_root, exist_ok=True)

    with zipfile.ZipFile(zip_path) as zf:
        infos = zf.infolist()

        compressed_total = sum(i.compress_size for i in infos) or 1
        expanded_total = sum(i.file_size for i in infos)
        if expanded_total / compressed_total > MAX_EXPANDED_RATIO:
            raise LauncherError(
                "The downloaded archive expands to an implausible size and was rejected.\n\n"
                "How to fix:\n"
                "1. Use \"Clear Cache\" and let the launcher download it again."
            )

        for info in infos:
            name = info.filename
            # Absolute paths, drive letters and .. traversal all get caught
            # by resolving the final path and confirming it stays inside.
            target = os.path.realpath(os.path.join(dest_root, name))
            if target != dest_root and not target.startswith(dest_root + os.sep):
                raise LauncherError(
                    f"The downloaded archive tried to write outside its folder "
                    f"({name!r}) and was rejected for safety.\n\n"
                    "How to fix:\n"
                    "1. Use \"Clear Cache\" and download again. If it keeps happening, "
                    "report it - the file on the server may have been tampered with."
                )
            # Symlink entries can point anywhere once followed; we never need
            # them for a Minecraft world.
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                log_fn(f"Skipping link entry in archive: {name}")
                continue

            if info.is_dir():
                os.makedirs(target, exist_ok=True)
                continue

            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst, CHUNK_SIZE)


def _partial_size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _discard(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _digest_of(path, upto=None):
    """SHA-256 of the bytes already on disk.

    Resuming means the hash cannot simply be accumulated as chunks arrive, so
    the existing part is re-read once per attempt. At ~1 GB that is a couple
    of seconds against a download measured in minutes - a good trade for not
    throwing the part away.
    """
    digest = hashlib.sha256()
    remaining = upto
    with open(path, "rb") as f:
        while True:
            size = CHUNK_SIZE if remaining is None else min(CHUNK_SIZE, remaining)
            if size <= 0:
                break
            chunk = f.read(size)
            if not chunk:
                break
            digest.update(chunk)
            if remaining is not None:
                remaining -= len(chunk)
    return digest


def _attempt_download(url, zip_path, name, log_fn, progress_fn, resume_from):
    """One try. Returns the hex digest of the completed file.

    Raises :class:`_TransientDownloadError` for anything worth retrying and
    :class:`LauncherError` for anything that is not.
    """
    headers = {"User-Agent": USER_AGENT}
    if resume_from:
        headers["Range"] = f"bytes={resume_from}-"

    try:
        with requests.get(
            url, stream=True, timeout=REQUEST_TIMEOUT,
            headers=headers, allow_redirects=True,
        ) as resp:
            # requests follows redirects for us; re-check where we actually
            # landed so an off-host redirect cannot smuggle content in.
            for hop in list(resp.history) + [resp]:
                _check_host(hop.url, "redirect")

            if resp.status_code == 416:
                # "Range Not Satisfiable": our .part is at least as long as
                # the file on the server, so it is stale or from a different
                # build. Throw it away and let the retry start clean.
                log_fn(f"{name}: the saved partial download is out of date; starting over.")
                _discard(zip_path)
                raise _TransientDownloadError("stale partial download discarded")
            if resp.status_code in RETRYABLE_STATUS:
                raise _TransientDownloadError(f"server returned {resp.status_code}")
            resp.raise_for_status()

            # A server that ignores Range answers 200 with the whole file.
            # Honour that rather than appending a second copy to the part.
            resuming = resume_from > 0 and resp.status_code == 206
            if resume_from and not resuming:
                log_fn(f"{name}: the server sent the whole file; starting over.")
                resume_from = 0

            body_length = int(resp.headers.get("content-length", 0) or 0)
            total = (resume_from + body_length) if body_length else 0
            if total > MAX_ARCHIVE_BYTES:
                raise LauncherError(
                    f"{name} is unexpectedly large ({total // (1024 * 1024)} MB); aborting."
                )

            if resuming:
                log_fn(f"Resuming {name} from {format_bytes(resume_from)}...")
                digest = _digest_of(zip_path, upto=resume_from)
                mode = "r+b"
            else:
                digest = hashlib.sha256()
                mode = "wb"

            reporter = ProgressReporter(name, total, resume_from, progress_fn)
            reporter.emit()

            with open(zip_path, mode) as f:
                if resuming:
                    f.seek(resume_from)
                    f.truncate()
                downloaded = resume_from
                for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                    if not chunk:
                        continue
                    f.write(chunk)
                    digest.update(chunk)
                    downloaded += len(chunk)
                    if downloaded > MAX_ARCHIVE_BYTES:
                        raise LauncherError(
                            f"{name} exceeded the maximum allowed size; aborting."
                        )
                    reporter.advance(len(chunk))

            if total and downloaded < total:
                raise _TransientDownloadError(
                    f"connection closed after {format_bytes(downloaded)} of {format_bytes(total)}"
                )

            reporter.emit(f"Downloaded {name}  ·  {format_bytes(downloaded)}")

    except (LauncherError, _TransientDownloadError):
        raise
    except requests.exceptions.SSLError as e:
        # Not retried: a failing certificate check will fail identically in
        # two seconds, and the fix is on the player's machine.
        raise LauncherError(
            f"The secure connection to the download server could not be verified.\n\nDetails: {e}\n\n"
            "How to fix:\n"
            "1. Check that your computer's date and time are correct,\n"
            "2. Temporarily disable 'HTTPS scanning' in your antivirus, or\n"
            "3. Try a different network."
        )
    except requests.exceptions.HTTPError as e:
        raise LauncherError(
            f"The download server refused to send {name}.\n\nDetails: {e}\n\n"
            "How to fix:\n"
            "1. Press Play again in a few minutes, or\n"
            "2. Check for a launcher update - the file may have moved."
        )
    except (requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.ChunkedEncodingError) as e:
        raise _TransientDownloadError(str(e) or type(e).__name__)
    except requests.RequestException as e:
        raise LauncherError(
            f"Could not download {name}.\n\nDetails: {e}\n\n"
            "How to fix:\n"
            "1. Check your internet connection,\n"
            "2. Allow Wizard Launcher through your firewall, then press Play again."
        )
    except OSError as e:
        raise LauncherError(
            f"Could not write {name} to disk.\n\nDetails: {e}\n\n"
            "How to fix:\n"
            "1. Free up disk space and press Play again, or\n"
            "2. Check that your antivirus is not blocking the launcher's folder."
        )

    return digest.hexdigest()


def _download(url, zip_path, name, log_fn, progress_fn):
    """Fetch `url` into `zip_path`, resuming and retrying as needed."""
    _check_host(url)

    delays = retry_delays(DOWNLOAD_MAX_RETRIES, DOWNLOAD_BACKOFF_SECONDS,
                          DOWNLOAD_BACKOFF_MAX_SECONDS)
    attempts = len(delays) + 1
    last_reason = ""

    for attempt in range(attempts):
        resume_from = _partial_size(zip_path)
        try:
            return _attempt_download(url, zip_path, name, log_fn, progress_fn, resume_from)
        except _TransientDownloadError as e:
            last_reason = str(e)
            if attempt >= len(delays):
                break
            delay = delays[attempt]
            log_fn(
                f"{name}: {last_reason}. Retrying in {delay:.0f}s "
                f"(attempt {attempt + 2} of {attempts})..."
            )
            progress_fn(None, f"Connection lost - retrying in {delay:.0f}s...")
            time.sleep(delay)

    # Out of attempts. The partial file is deliberately left in place: the
    # next Play press picks up where this left off instead of re-downloading
    # the gigabyte that did arrive.
    kept = _partial_size(zip_path)
    keep_note = (
        f"\n\n{format_bytes(kept)} was already downloaded and has been kept - "
        "pressing Play again continues from there rather than starting over."
        if kept else ""
    )
    raise LauncherError(
        f"The download of {name} kept failing.\n\nLast problem: {last_reason}\n\n"
        "How to fix:\n"
        "1. Check your internet connection and press Play again,\n"
        "2. Try a different network, such as a phone hotspot, or\n"
        "3. Temporarily disable 'HTTPS scanning' in your antivirus."
        + keep_note
    )


def _verify_digest(name, actual):
    expected = (RESOURCE_SHA256.get(name) or "").strip().lower()
    if not expected:
        return
    if actual.lower() != expected:
        raise LauncherError(
            f"The downloaded {name} does not match its expected fingerprint and was discarded.\n\n"
            f"Expected: {expected[:16]}...\n"
            f"Received: {actual[:16]}...\n\n"
            "This means the file was corrupted in transit or modified. "
            "Nothing was installed.\n\n"
            "How to fix:\n"
            "1. Press Play again to retry the download, or\n"
            "2. Try a different network."
        )


def ensure_resource(name, url, dest_dir, log_fn, progress_fn=None):
    """Make sure `dest_dir` exists and has content, downloading it if not."""
    progress_fn = progress_fn or (lambda fraction, message=None: None)

    if os.path.isdir(dest_dir) and os.listdir(dest_dir):
        return

    parent_dir = os.path.dirname(dest_dir)
    os.makedirs(parent_dir, exist_ok=True)

    zip_path = dest_dir + ".part"
    staging = dest_dir + "_staging"
    # A download that fails transiently keeps its .part; one that produced a
    # file we know is wrong must not, or every later attempt resumes a
    # corrupt archive.
    discard_partial = False

    try:
        resumable = _partial_size(zip_path)
        if resumable:
            log_fn(f"Continuing the earlier download of {name} "
                   f"({format_bytes(resumable)} already here)...")
        else:
            log_fn(f"Downloading {name} (first run only)...")

        actual_digest = _download(url, zip_path, name, log_fn, progress_fn)
        try:
            _verify_digest(name, actual_digest)
        except LauncherError:
            discard_partial = True
            raise

        log_fn(f"Unpacking {name}...")
        progress_fn(None, f"Unpacking {name}...")
        if os.path.isdir(staging):
            shutil.rmtree(staging, ignore_errors=True)

        try:
            _safe_extract(zip_path, staging, log_fn)
        except zipfile.BadZipFile:
            discard_partial = True
            raise LauncherError(
                f"The downloaded {name} file is damaged.\n\n"
                "How to fix:\n"
                "1. Press Play again to download it fresh - the damaged copy has been deleted."
            )
        except LauncherError:
            discard_partial = True
            raise

        # A zip that contains a single top-level folder gets flattened, so
        # the world ends up at dest_dir rather than dest_dir/World Name.
        entries = os.listdir(staging)
        if len(entries) == 1 and os.path.isdir(os.path.join(staging, entries[0])):
            unwrapped = os.path.join(staging, entries[0])
        else:
            unwrapped = staging

        if os.path.isdir(dest_dir):
            shutil.rmtree(dest_dir, ignore_errors=True)
        # Swap in only now that everything extracted successfully.
        os.replace(unwrapped, dest_dir)
        discard_partial = True   # installed; the archive has done its job

        log_fn(f"{name} ready.")
    finally:
        if os.path.isdir(staging):
            shutil.rmtree(staging, ignore_errors=True)
        if discard_partial:
            _discard(zip_path)
