import glob
import json
import os
import re
import shutil
import subprocess
import threading
import time

try:
    import minecraft_launcher_lib
except ImportError:
    minecraft_launcher_lib = None

from .constants import REQUIRED_JAVA_MAJOR, MC_VERSION
from .exceptions import LauncherError
from .paths import get_data_root
from .platform_utils import (
    IS_MACOS, IS_WINDOWS, JAVA_EXE, MOJANG_OS_DIRS, no_window_kwargs,
)

CACHE_FILE = "java_cache.json"
CACHE_TTL = 14 * 86400   # re-scan a fortnight after the last full scan
PROBE_TIMEOUT = 8


class JavaManager:
    """Locate a usable JVM.

    Scanning is genuinely expensive: every candidate costs a ``java -version``
    subprocess, and a developer machine can easily have a dozen. The old code
    paid that on every Play press - twice, since the server needs its own
    older JVM. Results are now cached to disk and validated cheaply
    (path still exists, file not modified), which is most of what makes a
    warm launch fast.
    """

    def __init__(self, log_fn, mc_dir):
        self.log = log_fn
        self.mc_dir = mc_dir
        self.cache_path = os.path.join(get_data_root(), CACHE_FILE)
        self._lock = threading.Lock()
        self._scanned = None   # in-memory list of (major, path) for this run

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------
    def _load_cache(self):
        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return None

        if not isinstance(data, dict) or time.time() - data.get("scanned_at", 0) > CACHE_TTL:
            return None

        valid = []
        for entry in data.get("javas") or []:
            path = entry.get("path")
            major = entry.get("major")
            mtime = entry.get("mtime")
            if not path or not isinstance(major, int):
                continue
            try:
                # Cheap revalidation: an updated or removed JVM invalidates
                # its entry without us having to run the binary again.
                if not os.path.isfile(path) or abs(os.path.getmtime(path) - (mtime or 0)) > 1:
                    continue
            except OSError:
                continue
            valid.append((major, path))

        return valid or None

    def _save_cache(self, scored):
        payload = {"scanned_at": time.time(), "javas": []}
        for major, path in scored:
            try:
                payload["javas"].append(
                    {"path": path, "major": major, "mtime": os.path.getmtime(path)}
                )
            except OSError:
                continue
        try:
            tmp = self.cache_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.replace(tmp, self.cache_path)
        except Exception:
            pass

    def invalidate_cache(self):
        try:
            os.remove(self.cache_path)
        except OSError:
            pass
        self._scanned = None

    def prewarm(self):
        """Populate the cache in the background at startup so the first Play
        press does not pay for the scan."""
        with self._lock:
            if self._scanned is not None:
                return
        self._get_scored()

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    def _get_scored(self):
        with self._lock:
            if self._scanned is not None:
                return self._scanned

            cached = self._load_cache()
            if cached:
                self._scanned = cached
                return cached

            self.log("Looking for Java on this computer...")
            scored = []
            for path in self._enumerate_java_candidates():
                major = self._get_java_major(path)
                if major is not None:
                    scored.append((major, path))

            if scored:
                versions = sorted({m for m, _ in scored}, reverse=True)
                self.log(f"Found Java {', '.join(str(v) for v in versions)}.")
            self._save_cache(scored)
            self._scanned = scored
            return scored

    def find_java(self, min_major=REQUIRED_JAVA_MAJOR, max_major=None):
        scored = self._get_scored()

        in_range = [
            (m, p) for (m, p) in scored
            if m >= min_major and (max_major is None or m <= max_major)
        ]
        if in_range:
            # Highest version inside the allowed window.
            in_range.sort(key=lambda x: x[0], reverse=True)
            best_major, best_path = in_range[0]
            self.log(f"Using Java {best_major}.")
            return best_path

        if max_major is not None:
            # Bounded request: the 1.16.5 server refuses to boot on Java 17+.
            # Mojang publishes older runtimes for exactly this reason, so try
            # those before telling the player to go and install a JDK by hand
            # - on an Apple silicon Mac in particular, finding a working
            # Java 8-16 build is not a two-minute job.
            self.log(f"No Java {min_major}-{max_major} installed; fetching an official older runtime...")
            downloaded = self._download_bundled_jvm(("java-runtime-alpha", "jre-legacy"))
            if downloaded:
                major = self._get_java_major(downloaded)
                if major is not None and min_major <= major <= max_major:
                    self._forget_scan()
                    self.log(f"Using downloaded Java {major} for the world server.")
                    return downloaded
            raise LauncherError(
                f"No Java between {min_major} and {max_major} is available, and the "
                "automatic download did not work.\n\n"
                "How to fix:\n"
                f"1. Install Java {max_major} from https://adoptium.net, then press Play again,\n"
                "2. Or check your internet connection and firewall."
            )

        self.log("No suitable Java found; downloading the official runtime...")
        downloaded = self._download_bundled_jvm()
        if downloaded:
            self._forget_scan()
            return downloaded

        raise LauncherError(
            f"Java {min_major} or newer is required, and the automatic download did not work.\n\n"
            "How to fix:\n"
            f"1. Install Java {min_major} from https://adoptium.net (pick the .msi installer), or\n"
            "2. Check your internet connection and firewall, then press Play again."
        )

    def _enumerate_java_candidates(self):
        candidates = []

        path_java = shutil.which("java")
        if path_java:
            candidates.append(path_java)

        java_home = os.environ.get("JAVA_HOME")
        if java_home:
            candidates.append(os.path.join(java_home, "bin", "java.exe"))
            candidates.append(os.path.join(java_home, "bin", "java"))

        if IS_WINDOWS:
            candidates.extend(self._windows_candidates())
        elif IS_MACOS:
            candidates.extend(self._macos_candidates())
        else:
            candidates.extend(self._linux_candidates())

        # Every platform: JVMs the official Minecraft launcher downloaded, and
        # any this launcher downloaded itself. Reusing one saves the player a
        # ~200 MB download.
        for os_dir in MOJANG_OS_DIRS:
            candidates.extend(self._glob_safe(os.path.join(
                self.mc_dir, "runtime", "*", os_dir, "*", "bin", JAVA_EXE
            )))
            if IS_MACOS:
                # Inside a macOS runtime the binary sits under jre.bundle.
                candidates.extend(self._glob_safe(os.path.join(
                    self.mc_dir, "runtime", "*", os_dir, "*", "jre.bundle",
                    "Contents", "Home", "bin", JAVA_EXE
                )))

        seen = set()
        result = []
        for path in candidates:
            norm = os.path.normcase(os.path.abspath(path))
            if norm not in seen and os.path.isfile(path):
                seen.add(norm)
                result.append(path)
        return result

    def _windows_candidates(self):
        found = []
        program_dirs = {
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
            os.environ.get("ProgramW6432", r"C:\Program Files"),
        }
        vendor_subdirs = [
            "Java", "Eclipse Adoptium", "Eclipse Foundation", "Zulu",
            "Microsoft\\jdk-*", "BellSoft\\LibericaJDK-*", "Amazon Corretto",
            "Semeru", "AdoptOpenJDK",
        ]
        for pdir in filter(None, program_dirs):
            for vendor in vendor_subdirs:
                found.extend(self._glob_safe(os.path.join(pdir, vendor, "*", "bin", "java.exe")))
                found.extend(self._glob_safe(os.path.join(pdir, vendor, "bin", "java.exe")))

        appdata = os.environ.get("APPDATA")
        if appdata:
            found.extend(self._glob_safe(os.path.join(
                appdata, ".minecraft", "runtime", "*", "windows-x64", "*", "bin", "java.exe"
            )))
        localappdata = os.environ.get("LOCALAPPDATA")
        if localappdata:
            # The Microsoft-store Minecraft launcher keeps its JVMs here.
            found.extend(self._glob_safe(os.path.join(
                localappdata, "Packages", "Microsoft.4297127D64EC6_8wekyb3d8bbwe",
                "LocalCache", "Local", "runtime", "*", "windows-x64", "*", "bin", "java.exe"
            )))
        return found

    def _macos_candidates(self):
        """Every place a Mac realistically keeps a JVM.

        ``/usr/libexec/java_home -V`` is asked first because it is the system's
        own answer and it covers vendors we would never think to glob for. It
        prints to stderr, lists one JVM per line, and exits non-zero when none
        are installed - none of which is a reason to fail.
        """
        found = []
        try:
            result = subprocess.run(
                ["/usr/libexec/java_home", "-V"],
                capture_output=True, text=True, timeout=PROBE_TIMEOUT,
            )
            for line in (result.stderr or "").splitlines():
                marker = line.find("/Library/Java/JavaVirtualMachines/")
                if marker == -1:
                    marker = line.find("/")
                    if marker == -1:
                        continue
                home = line[marker:].strip()
                if home and os.path.isdir(home):
                    found.append(os.path.join(home, "bin", "java"))
        except Exception:
            pass

        patterns = [
            "/Library/Java/JavaVirtualMachines/*/Contents/Home/bin/java",
            os.path.expanduser("~/Library/Java/JavaVirtualMachines/*/Contents/Home/bin/java"),
            "/System/Library/Java/JavaVirtualMachines/*/Contents/Home/bin/java",
            # Homebrew, Apple silicon and Intel prefixes respectively.
            "/opt/homebrew/opt/openjdk*/bin/java",
            "/opt/homebrew/opt/openjdk*/libexec/openjdk.jdk/Contents/Home/bin/java",
            "/opt/homebrew/Cellar/openjdk*/*/bin/java",
            "/usr/local/opt/openjdk*/bin/java",
            "/usr/local/Cellar/openjdk*/*/bin/java",
            os.path.expanduser("~/.sdkman/candidates/java/*/bin/java"),
            # The official launcher's own runtimes.
            os.path.expanduser(
                "~/Library/Application Support/minecraft/runtime/*/*/*/bin/java"),
            os.path.expanduser(
                "~/Library/Application Support/minecraft/runtime/*/*/*/jre.bundle/Contents/Home/bin/java"),
        ]
        for pattern in patterns:
            found.extend(self._glob_safe(pattern))
        return found

    def _linux_candidates(self):
        patterns = [
            "/usr/lib/jvm/*/bin/java",
            "/usr/lib64/jvm/*/bin/java",
            "/opt/java/*/bin/java",
            os.path.expanduser("~/.sdkman/candidates/java/*/bin/java"),
            os.path.expanduser("~/.minecraft/runtime/*/*/*/bin/java"),
        ]
        found = []
        for pattern in patterns:
            found.extend(self._glob_safe(pattern))
        return found

    def _get_java_major(self, java_path):
        probe_path = java_path
        if os.path.basename(java_path).lower() == "javaw.exe":
            alt = os.path.join(os.path.dirname(java_path), "java.exe")
            if os.path.isfile(alt):
                probe_path = alt
        try:
            result = subprocess.run(
                [probe_path, "-version"],
                capture_output=True, text=True, timeout=PROBE_TIMEOUT,
                errors="replace",
                **no_window_kwargs(),
            )
        except Exception:
            return None

        return self._parse_major(result.stderr or result.stdout)

    @staticmethod
    def _parse_major(output):
        match = re.search(r'version "(\d+)(?:\.(\d+))?', output or "")
        if not match:
            return None
        major = int(match.group(1))
        # "1.8.0_401" style version strings report the real major second.
        if major == 1 and match.group(2):
            major = int(match.group(2))
        return major

    @staticmethod
    def _ensure_executable(path):
        """A downloaded JVM that is not marked executable fails with a bare
        PermissionError on macOS/Linux; restoring the bit is cheaper than
        explaining that to a player."""
        if IS_WINDOWS:
            return
        try:
            mode = os.stat(path).st_mode
            os.chmod(path, mode | 0o111)
        except OSError:
            pass

    @staticmethod
    def _glob_safe(pattern):
        try:
            return glob.glob(pattern)
        except Exception:
            return []

    def _forget_scan(self):
        """Drop both caches so the next lookup sees a newly installed JVM."""
        self.invalidate_cache()
        with self._lock:
            self._scanned = None

    def _download_bundled_jvm(self, jvm_versions=("java-runtime-gamma", "java-runtime-delta")):
        if minecraft_launcher_lib is None:
            return None
        try:
            os.makedirs(self.mc_dir, exist_ok=True)
            for jvm_version in jvm_versions:
                try:
                    self.log(f"Downloading Java runtime ({jvm_version})...")
                    minecraft_launcher_lib.runtime.install_jvm_runtime(jvm_version, self.mc_dir)
                    exe_path = minecraft_launcher_lib.runtime.get_executable_path(jvm_version, self.mc_dir)
                    if exe_path and os.path.isfile(exe_path):
                        self._ensure_executable(exe_path)
                        self.log("Java runtime installed.")
                        return exe_path
                except Exception as inner:
                    self.log(f"Could not install {jvm_version}: {inner}")
                    continue
        except Exception as e:
            self.log(f"Java runtime download failed: {e}")
        return None

    def check_java_version(self, java_path, required_major=REQUIRED_JAVA_MAJOR):
        for major, path in self._get_scored():
            if path == java_path:
                if major < required_major:
                    self.log(
                        f"Java {major} is older than the recommended {required_major} "
                        f"for Minecraft {MC_VERSION}.",
                    )
                return major
        return None
