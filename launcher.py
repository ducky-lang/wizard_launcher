"""Wizard Launcher entry point.

Responsibilities kept deliberately small: fail loudly and legibly if the app
cannot start, make sure only one copy runs at a time, and hand off to the UI.

Everything here has to work before the UI exists, and on Windows, macOS and
Linux alike - so the platform differences are pushed down into
launcher_core.platform_utils rather than branched on in place.
"""

import os
import sys
import traceback


def _show_fatal_error(message, title="Wizard Launcher"):
    """Last-resort user-visible error.

    The shipped build has no console, so printing to stderr reaches nobody.
    A native message box is the only thing guaranteed to be visible when the
    UI toolkit itself is what failed.
    """
    try:
        from launcher_core.platform_utils import show_message_box
        show_message_box(message, title)
        return
    except Exception:
        pass
    # platform_utils itself failed to import - the install is broken beyond
    # what a nice dialog can explain, so take whatever output we can get.
    try:
        sys.stderr.write(message + "\n")
    except Exception:
        pass


def _write_crash_log(text):
    try:
        from launcher_core.app_log import get_logger
        return get_logger().write_crash(text)
    except Exception:
        # The logger itself may be what broke; fall back to a bare file.
        try:
            from launcher_core.paths import get_data_root
            path = os.path.join(get_data_root(), "crash.log")
            with open(path, "a", encoding="utf-8") as f:
                f.write(text + "\n")
            return path
        except Exception:
            return ""


def _run_watchdog_mode(args):
    """Re-invocation entry point used by ProcessManager.spawn_watchdog().

    A packaged build has no separate watchdog.py to run, so the same
    executable is relaunched with --watchdog and lands here instead of the GUI.
    """
    import time
    from launcher_core.watchdog import pid_alive, kill_tree

    if len(args) < 2:
        return
    try:
        client_pid = int(args[0])
        kill_pids = [int(p) for p in args[1:]]
    except ValueError:
        return

    while pid_alive(client_pid):
        time.sleep(2)
    for pid in kill_pids:
        kill_tree(pid)


class SingleInstance:
    """Prevent a second launcher from starting.

    Two launchers means two servers fighting over port 25565, two copies of
    the world being written by different processes, and a confusing "port in
    use" error for the user.

    Windows gets a named mutex - the cheapest reliable guard there. Everywhere
    else uses an advisory lock (``flock``) on a file in the data folder: the
    kernel releases it automatically if the process is killed, so unlike the
    old "write my PID and check it later" approach there is no stale-lock
    state to reason about after a crash.
    """

    def __init__(self, name="WizardLauncher"):
        self.name = name
        self._handle = None
        self._lock_file = None
        self._lock_path = None

    def acquire(self):
        from launcher_core.platform_utils import IS_WINDOWS

        if IS_WINDOWS:
            try:
                import ctypes
                ERROR_ALREADY_EXISTS = 183
                self._handle = ctypes.windll.kernel32.CreateMutexW(
                    None, False, f"Global\\{self.name}"
                )
                if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
                    return False
                return True
            except Exception:
                return True  # never block startup over a failed guard

        try:
            import fcntl
            from launcher_core.paths import get_data_root

            self._lock_path = os.path.join(get_data_root(), f"{self.name}.lock")
            self._lock_file = open(self._lock_path, "w")
            try:
                fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                self._lock_file.close()
                self._lock_file = None
                return False
            self._lock_file.write(str(os.getpid()))
            self._lock_file.flush()
            return True
        except Exception:
            return True

    def release(self):
        if self._lock_file is not None:
            try:
                import fcntl
                fcntl.flock(self._lock_file, fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                self._lock_file.close()
            except Exception:
                pass
            self._lock_file = None
        if self._lock_path and os.path.isfile(self._lock_path):
            try:
                os.remove(self._lock_path)
            except OSError:
                pass


def _focus_existing_window():
    """Bring the already-running launcher forward, so a second double-click
    feels like "it's already open" rather than "nothing happened"."""
    from launcher_core.platform_utils import IS_MACOS, IS_WINDOWS

    if IS_WINDOWS:
        try:
            import ctypes
            hwnd = ctypes.windll.user32.FindWindowW(None, "Wizard Launcher")
            if hwnd:
                SW_RESTORE = 9
                ctypes.windll.user32.ShowWindow(hwnd, SW_RESTORE)
                ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
    elif IS_MACOS:
        try:
            import subprocess
            subprocess.run(
                ["osascript", "-e",
                 'tell application "Wizard Launcher" to activate'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
            )
        except Exception:
            pass


def _bootstrap_resources():
    """Copy any bundled game files into the data folder before the UI starts.

    On macOS this is how the server and proxy jars get out of the read-only
    .app bundle in the first place; on Windows it repairs an install whose
    resources went missing.
    """
    try:
        from launcher_core.bootstrap import ensure_data_resources
        from launcher_core.paths import get_bundled_resources_dir, get_data_root

        bundled = get_bundled_resources_dir()
        if not bundled:
            return
        ensure_data_resources(
            bundled,
            os.path.join(get_data_root(), "resources"),
            log=_bootstrap_log,
        )
    except Exception:
        # A failure here is not fatal: the launcher will report a precise
        # "server.jar is missing" later, which is a better error than a
        # traceback during startup.
        try:
            from launcher_core.app_log import get_logger
            get_logger().write_crash(traceback.format_exc())
        except Exception:
            pass


def _bootstrap_log(message):
    try:
        from launcher_core.app_log import get_logger
        get_logger().write(message)
    except Exception:
        pass


def _initialize_doppler():
    """Initialize Doppler if configured.

    The project is set via environment variable DOPPLER_PROJECT (config via
    DOPPLER_CONFIG, token via DOPPLER_TOKEN). If none of those are set,
    Doppler initialization is skipped gracefully - a launch started with
    `doppler run` already has its secrets in the environment either way.
    """
    import os
    project = os.getenv("DOPPLER_PROJECT")
    token = os.getenv("DOPPLER_TOKEN")
    if project or token:
        try:
            from launcher_core.doppler_secrets import initialize_doppler
            initialize_doppler(project=project, config=os.getenv("DOPPLER_CONFIG"), token=token)
            if _logger:
                _logger.write(f"Doppler initialized: project={project or '(from token)'}")
        except Exception as e:
            if _logger:
                _logger.write(f"Failed to initialize Doppler: {e}", level="WARNING")
            # Don't fail startup if Doppler initialization fails


def main(page):
    from ui.main_window import LauncherApp
    LauncherApp(page)


def _install_thread_excepthook(logger):
    """Route exceptions from background threads into the crash log.

    Without this, a thread that dies takes its traceback with it and the user
    sees a launcher that simply stops responding to the Play button.
    """
    import threading

    def _hook(args):
        if issubclass(args.exc_type, SystemExit):
            return
        text = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
        logger.write(f"Unhandled exception in thread {args.thread.name}", level="ERROR")
        logger.write_crash(text)

    threading.excepthook = _hook


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--watchdog":
        _run_watchdog_mode(sys.argv[2:])
        sys.exit(0)

    instance = SingleInstance()
    if not instance.acquire():
        _focus_existing_window()
        _show_fatal_error(
            "Wizard Launcher is already running.\n\n"
            "Check your taskbar - the window may be minimised.",
            title="Wizard Launcher",
        )
        sys.exit(0)

    try:
        import flet as ft
    except ImportError:
        _show_fatal_error(
            "This installation of Wizard Launcher is incomplete (the UI library is missing).\n\n"
            "How to fix:\n"
            "1. Reinstall Wizard Launcher from the official installer."
        )
        instance.release()
        sys.exit(1)

    try:
        from launcher_core.app_log import get_logger
        _logger = get_logger()
        _install_thread_excepthook(_logger)
    except Exception:
        _logger = None

    _bootstrap_resources()

    _initialize_doppler()

    try:
        ft.app(main)
    except Exception:
        tb = traceback.format_exc()
        crash_log_path = _write_crash_log(tb)
        hint = (
            f"\n\nA report was saved to:\n{crash_log_path}\n\n"
            "Please send that file to the launcher author."
            if crash_log_path else f"\n\n{tb}"
        )
        _show_fatal_error(f"Wizard Launcher could not start.{hint}")
        instance.release()
        sys.exit(1)
    finally:
        instance.release()

# Created by Foxy(.phungminh)
