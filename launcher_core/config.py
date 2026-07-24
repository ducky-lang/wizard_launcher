"""User-facing settings (non-sensitive) persisted as plain JSON.

Deliberately NOT encrypted: nothing here is a secret, and keeping it
readable means a user can hand-edit it - or paste it into a bug report -
without any tooling. Secrets live in :mod:`launcher_core.secure_store`.

Every value is validated on load, so a hand-edited or corrupted file can
never feed a bad JVM flag or a bogus bind address into a subprocess.
"""

import json
import os
import threading

from .paths import get_data_root

try:
    import psutil
except ImportError:
    psutil = None

CONFIG_FILE = "settings.json"

DEFAULTS = {
    # Memory, in megabytes. 0 means "decide automatically from system RAM".
    "server_ram_mb": 0,
    "client_ram_mb": 0,
    # Security: keep the local server bound to loopback unless the player
    # explicitly opts into letting friends on the same network join.
    "allow_lan": False,
    # Behaviour
    "close_launcher_on_play": False,
    "auto_restart_server": True,
    "check_updates": True,
    "keyboard_shortcuts": True,
    # Graphics. `animations` is the master switch for the drifting embers;
    # `spell_effects` covers the one-shot flourishes, which somebody may want
    # off on a laptop even while the ambient background stays on.
    "animations": True,
    "spell_effects": True,
    "intro_animation": True,
    # Safety net before the destructive maintenance buttons.
    "backup_before_reset": True,
    # First-run guide. Stored as the version that completed it, so a future
    # release with genuinely new setup steps can show the guide again without
    # re-running it for everybody on every update.
    "onboarding_completed_version": "",
    # Diagnostics
    "keep_log_days": 7,
    "verbose_logging": False,
}


def _total_ram_mb():
    if psutil is not None:
        try:
            return int(psutil.virtual_memory().total / (1024 * 1024))
        except Exception:
            pass
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", wintypes.DWORD),
                    ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return int(stat.ullTotalPhys / (1024 * 1024))
        except Exception:
            pass
    else:
        # POSIX: sysconf works on both macOS and Linux and needs no
        # subprocess. Getting this wrong means every Mac without psutil
        # silently plays with the 8 GB default heap below.
        try:
            total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
            if total > 0:
                return int(total / (1024 * 1024))
        except (AttributeError, ValueError, OSError):
            pass
    return 8192  # conservative assumption when detection fails


def auto_server_ram_mb():
    """Pick a server heap that fits the machine.

    The old hardcoded ``-Xms6G -Xmx6G`` combined with ``AlwaysPreTouch``
    reserved and touched six gigabytes before the world even loaded, which
    on an 8 GB laptop meant the client and the server fought each other for
    the rest of the session. A 1.16.5 adventure map with four players is
    comfortable well below that.
    """
    total = _total_ram_mb()
    if total <= 6144:
        return 1536
    if total <= 8192:
        return 2048
    if total <= 16384:
        return 3072
    return 4096


def auto_client_ram_mb():
    total = _total_ram_mb()
    if total <= 6144:
        return 1536
    if total <= 8192:
        return 2048
    if total <= 16384:
        return 3072
    return 4096


class Settings:
    """Thread-safe settings holder. Reads are lock-free after the first
    load; writes are serialised and flushed atomically."""

    def __init__(self, log=None):
        self.log = log or (lambda msg: None)
        self.path = os.path.join(get_data_root(), CONFIG_FILE)
        self._lock = threading.Lock()
        self._data = dict(DEFAULTS)
        self.load()

    # -- persistence ---------------------------------------------------
    def load(self):
        if not os.path.isfile(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as e:
            self.log(f"settings.json unreadable ({e}); using defaults.")
            return
        if not isinstance(raw, dict):
            return
        with self._lock:
            for key, default in DEFAULTS.items():
                self._data[key] = self._coerce(key, raw.get(key, default), default)

    def save(self):
        with self._lock:
            snapshot = dict(self._data)
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, indent=2)
            os.replace(tmp, self.path)
        except Exception as e:
            self.log(f"Could not save settings: {e}")

    # -- access --------------------------------------------------------
    def get(self, key):
        with self._lock:
            return self._data.get(key, DEFAULTS.get(key))

    def set(self, key, value):
        if key not in DEFAULTS:
            return
        with self._lock:
            self._data[key] = self._coerce(key, value, DEFAULTS[key])

    def update(self, **kwargs):
        for key, value in kwargs.items():
            self.set(key, value)
        self.save()

    @staticmethod
    def _coerce(key, value, default):
        """Force every value into the shape the rest of the app expects.
        A settings file is user-writable, so it is untrusted input."""
        if isinstance(default, bool):
            return bool(value)
        if isinstance(default, int):
            try:
                value = int(value)
            except (TypeError, ValueError):
                return default
            if key.endswith("_ram_mb"):
                # 0 = auto. Otherwise clamp to something a JVM will accept.
                return 0 if value <= 0 else max(512, min(value, 32768))
            if key == "keep_log_days":
                return max(1, min(value, 365))
            return value
        return value if isinstance(value, type(default)) else default

    # -- derived values ------------------------------------------------
    @property
    def server_ram_mb(self):
        return self.get("server_ram_mb") or auto_server_ram_mb()

    @property
    def client_ram_mb(self):
        return self.get("client_ram_mb") or auto_client_ram_mb()

    @property
    def bind_address(self):
        """Loopback unless the player explicitly enabled LAN play."""
        return "0.0.0.0" if self.get("allow_lan") else "127.0.0.1"
