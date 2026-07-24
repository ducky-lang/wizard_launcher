"""Where the launcher's code and data live, on every supported platform.

Two directories matter and they are deliberately different things:

``get_app_root()``
    Where the program itself is. Read-only in practice - inside an .app
    bundle on macOS, in Program Files or LocalAppData on Windows.

``get_data_root()``
    Where the player's world, downloaded game files, logs and encrypted
    session state go. Always writable, always survives an app update.

Keeping them apart is what lets the installer put the executable wherever
the user likes without breaking the data lookup.

On every platform the data root is now the OS's designated *application
data* location - LocalAppData, Application Support, ``$XDG_DATA_HOME``.
Windows used to use ``Documents\\WizardLauncher``; installs from that era
are relocated once, transparently, by :mod:`launcher_core.migration`.
"""

import os
import sys
import threading

from .platform_utils import IS_MACOS, IS_WINDOWS

APP_DIR_NAME = "WizardLauncher"

# Resolving the data root involves a shell API call, a possible one-time
# folder move and a makedirs. It is asked for from every module and on every
# log line, so the answer is memoised per override value - the tests flip
# WIZARD_LAUNCHER_DATA between cases and must still see it honoured.
_ROOT_CACHE = {}
_ROOT_LOCK = threading.Lock()
_STARTUP_NOTES = []


def is_frozen():
    """True when running from a packaged build rather than a source checkout.

    Covers Nuitka onefile (``NUITKA_ORIGINAL_ARGV0``), Nuitka standalone
    (the ``__compiled__`` attribute the compiler injects) and PyInstaller
    (``sys.frozen``), so a build made with any of them behaves the same.
    """
    if "NUITKA_ORIGINAL_ARGV0" in os.environ:
        return True
    if getattr(sys, "frozen", False):
        return True
    if "__compiled__" in globals():
        return True
    # Nuitka --standalone (the macOS .app layout) sets neither of the above,
    # and the interpreter is the application itself rather than python.exe.
    # Without this the launcher would look for watchdog.py next to a compiled
    # module, not find it, and silently skip spawning the watchdog.
    executable = os.path.basename(sys.executable or "").lower()
    return not executable.startswith("python")


def get_app_root():
    """The folder that contains the running program.

    Under Nuitka onefile the process is unpacked into a fresh temp directory
    on every launch, so ``__file__`` points somewhere useless.
    NUITKA_ORIGINAL_ARGV0 (set by the onefile bootstrap) gives the real
    executable path instead, which is what bundled resources sit next to.
    """
    onefile_exe = os.environ.get("NUITKA_ORIGINAL_ARGV0")
    if onefile_exe:
        return os.path.dirname(os.path.abspath(onefile_exe))
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    # this file lives in launcher_core/, so go up one level to the project root
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_bundled_resources_dir():
    """The read-only ``resources/`` folder shipped with the application, or
    "" when there is none.

    On macOS an .app bundle puts data files in ``Contents/Resources`` while
    the executable is in ``Contents/MacOS``, so the app root is one level too
    deep; both layouts are checked.
    """
    candidates = [os.path.join(get_app_root(), "resources")]
    if IS_MACOS:
        # .../WizardLauncher.app/Contents/MacOS/  ->  .../Contents/Resources/
        contents = os.path.dirname(get_app_root())
        candidates.insert(0, os.path.join(contents, "Resources", "resources"))
        candidates.insert(1, os.path.join(contents, "Resources"))
    for path in candidates:
        if os.path.isdir(path):
            return path
    return ""


def _windows_documents_dir():
    """Resolve Documents via the Win32 shell API (CSIDL_PERSONAL), the same
    folder Inno Setup's {userdocs} constant resolves to - so files the
    installer places there and files this app looks for there always agree,
    even when Documents has been redirected (OneDrive, a domain profile)."""
    try:
        import ctypes
        from ctypes import wintypes

        CSIDL_PERSONAL = 5
        SHGFP_TYPE_CURRENT = 0
        buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        ctypes.windll.shell32.SHGetFolderPathW(
            None, CSIDL_PERSONAL, None, SHGFP_TYPE_CURRENT, buf
        )
        if buf.value:
            return buf.value
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), "Documents")


def _windows_local_appdata_dir():
    """LocalAppData, the correct home for per-machine application state.

    Read from the environment first because that is what every other tool on
    the system agrees on, with a profile-relative fallback for the rare
    process that inherited a stripped environment.
    """
    local = (os.environ.get("LOCALAPPDATA") or "").strip()
    if local:
        return local
    return os.path.join(os.path.expanduser("~"), "AppData", "Local")


def get_legacy_data_root():
    """The pre-1.3 location, or "" on platforms that never used one.

    Kept as a public function because more than one caller needs it: the
    migration, and the diagnostics report that says where data used to live
    when a move could not be completed.
    """
    if IS_WINDOWS:
        return os.path.join(_windows_documents_dir(), APP_DIR_NAME)
    return ""


def _default_data_root():
    if IS_WINDOWS:
        return os.path.join(_windows_local_appdata_dir(), APP_DIR_NAME)
    if IS_MACOS:
        # The platform-correct home for application data. Documents is for
        # things the user made; a 2 GB world and a JVM cache are not that,
        # and putting them there also means they get synced to iCloud.
        return os.path.join(os.path.expanduser("~"), "Library",
                            "Application Support", APP_DIR_NAME)
    xdg = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share"
    )
    return os.path.join(xdg, APP_DIR_NAME)


def _resolve_data_root(override):
    if override:
        return os.path.abspath(os.path.expanduser(override))

    default = _default_data_root()
    legacy = get_legacy_data_root()
    if legacy:
        # Imported here rather than at module scope: migration imports
        # nothing from paths, and keeping the dependency lazy means a broken
        # migration module cannot stop the app from booting.
        from .migration import migrate_data_root
        try:
            return migrate_data_root(legacy, default, log=_STARTUP_NOTES.append)
        except Exception as e:  # never let housekeeping block startup
            _STARTUP_NOTES.append(f"Data folder migration skipped: {e}")
    return default


def get_data_root():
    """The writable folder holding resources, logs, settings and caches.

    ``WIZARD_LAUNCHER_DATA`` overrides it, which is what makes it possible to
    test a clean first-run install without touching the real one.
    """
    override = (os.environ.get("WIZARD_LAUNCHER_DATA") or "").strip()

    with _ROOT_LOCK:
        cached = _ROOT_CACHE.get(override)
        if cached is None:
            cached = _ROOT_CACHE[override] = _resolve_data_root(override)
    os.makedirs(cached, exist_ok=True)
    return cached


def drain_startup_notes():
    """Anything the data-root resolution wanted to say, once a log exists.

    The folder move necessarily happens before there is anywhere to write a
    log line - the log lives in the folder being moved. The messages are
    buffered here and replayed into the session header instead of being lost.
    """
    with _ROOT_LOCK:
        notes = list(_STARTUP_NOTES)
        _STARTUP_NOTES.clear()
    return notes


def reset_data_root_cache():
    """Forget the memoised root. Only tests need this."""
    with _ROOT_LOCK:
        _ROOT_CACHE.clear()
        _STARTUP_NOTES.clear()
