"""File logging for the launcher.

Three jobs:

1. **Never lose an error.** Everything the UI shows, plus the raw stdout of
   the server / proxy / client, lands in one rotating file the user can
   attach to a bug report.
2. **Never leak a secret into that file.** Access tokens, refresh tokens and
   the client id are redacted before a line is written. A log the user is
   encouraged to send to a stranger must not contain a session token.
3. **Never grow without bound.** The file rotates at a size cap and old
   sessions are pruned, so a long-running install does not quietly fill the
   player's disk.
"""

import os
import re
import threading
import time
from datetime import datetime

from .paths import drain_startup_notes, get_data_root
from .platform_utils import open_path
from .version import APP_NAME, VERSION_STRING

LOG_DIR_NAME = "logs"
LOG_FILE_NAME = "launcher.log"
CRASH_FILE_NAME = "crash.log"

MAX_BYTES = 2 * 1024 * 1024   # rotate at 2 MB
BACKUP_COUNT = 3              # launcher.log + .1 .2 .3

# Patterns whose *value* must never reach the log file. Each regex keeps the
# label so the line still reads sensibly, and replaces only the secret.
_REDACTIONS = [
    (re.compile(r'("?(?:access_token|refresh_token|id_token|mc_access_token|ms_refresh_token)"?\s*[:=]\s*"?)([^\s",}]+)', re.I), r"\1<redacted>"),
    (re.compile(r"(Bearer\s+)([A-Za-z0-9._\-]+)", re.I), r"\1<redacted>"),
    (re.compile(r"(--accessToken\s+)(\S+)", re.I), r"\1<redacted>"),
    (re.compile(r"(--uuid\s+)(\S+)", re.I), r"\1<redacted>"),
    (re.compile(r"(client_id=)([^\s&\"']+)", re.I), r"\1<redacted>"),
    (re.compile(r"(device_code=)([^\s&\"']+)", re.I), r"\1<redacted>"),
    # Bare JWTs that slipped through a format we did not anticipate.
    (re.compile(r"\beyJ[A-Za-z0-9._\-]{20,}"), "<redacted-jwt>"),
]


def redact(text: str) -> str:
    """Strip credentials out of a log line. Cheap enough to run on every
    line, and it is the last line of defence before bytes hit the disk."""
    if not text:
        return text
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


class LauncherLogger:
    """Rotating, redacting, thread-safe log writer.

    Intentionally not built on :mod:`logging` - the launcher needs the same
    call to fan out to both the on-screen activity panel and the file, and a
    plain writer keeps that path obvious.
    """

    def __init__(self, keep_days=7):
        self.dir = os.path.join(get_data_root(), LOG_DIR_NAME)
        os.makedirs(self.dir, exist_ok=True)
        self.path = os.path.join(self.dir, LOG_FILE_NAME)
        self.crash_path = os.path.join(self.dir, CRASH_FILE_NAME)
        self._lock = threading.Lock()
        self._prune_old(keep_days)
        self._write_header()

    # -- writing -------------------------------------------------------
    def write(self, message: str, level="INFO"):
        line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [{level}] {redact(str(message))}"
        with self._lock:
            try:
                self._rotate_if_needed()
                with open(self.path, "a", encoding="utf-8", errors="replace") as f:
                    f.write(line + "\n")
            except Exception:
                # Logging must never take the app down with it.
                pass

    def write_raw(self, message: str):
        """For subprocess output that already carries its own timestamp."""
        with self._lock:
            try:
                self._rotate_if_needed()
                with open(self.path, "a", encoding="utf-8", errors="replace") as f:
                    f.write(redact(str(message)) + "\n")
            except Exception:
                pass

    def write_crash(self, traceback_text: str) -> str:
        """Append a crash report to its own file and return that path, so the
        error dialog can point the user straight at it."""
        try:
            with open(self.crash_path, "a", encoding="utf-8", errors="replace") as f:
                f.write("\n" + "=" * 70 + "\n")
                f.write(f"{APP_NAME} {VERSION_STRING} - {datetime.now().isoformat(timespec='seconds')}\n")
                f.write("=" * 70 + "\n")
                f.write(redact(traceback_text) + "\n")
        except Exception:
            return ""
        self.write(f"Crash report written to {self.crash_path}", level="ERROR")
        return self.crash_path

    # -- housekeeping --------------------------------------------------
    def _write_header(self):
        import platform
        self.write("=" * 60)
        self.write(f"{APP_NAME} {VERSION_STRING}")
        self.write(f"Platform: {platform.platform()} | Python {platform.python_version()}")
        self.write(f"Data root: {get_data_root()}")
        # The data folder may have been relocated moments ago, before any log
        # file existed to record it. Replay whatever happened.
        for note in drain_startup_notes():
            self.write(note)
        self.write("=" * 60)

    def _rotate_if_needed(self):
        try:
            if not os.path.isfile(self.path) or os.path.getsize(self.path) < MAX_BYTES:
                return
        except OSError:
            return

        oldest = f"{self.path}.{BACKUP_COUNT}"
        if os.path.isfile(oldest):
            try:
                os.remove(oldest)
            except OSError:
                return
        for index in range(BACKUP_COUNT - 1, 0, -1):
            src, dst = f"{self.path}.{index}", f"{self.path}.{index + 1}"
            if os.path.isfile(src):
                try:
                    os.replace(src, dst)
                except OSError:
                    pass
        try:
            os.replace(self.path, f"{self.path}.1")
        except OSError:
            pass

    def _prune_old(self, keep_days):
        """Delete leftovers from the old per-session naming scheme
        (``launcher_2026-07-22_15-45-01.log``) plus anything older than the
        retention window."""
        cutoff = time.time() - keep_days * 86400
        try:
            entries = os.listdir(self.dir)
        except OSError:
            return
        for name in entries:
            if not name.endswith(".log") and ".log." not in name:
                continue
            full = os.path.join(self.dir, name)
            try:
                if os.path.getmtime(full) < cutoff:
                    os.remove(full)
            except OSError:
                pass

    def open_folder(self):
        """Reveal the log folder in the OS file manager."""
        return open_path(self.dir)


_INSTANCE = None
_INSTANCE_LOCK = threading.Lock()


def get_logger(keep_days=7):
    """Process-wide logger.

    Shared deliberately: the crash handler in launcher.py and the UI both
    need one, and constructing two would write two session headers, prune the
    directory twice, and let two file handles race on rotation.
    """
    global _INSTANCE
    with _INSTANCE_LOCK:
        if _INSTANCE is None:
            _INSTANCE = LauncherLogger(keep_days=keep_days)
        return _INSTANCE
