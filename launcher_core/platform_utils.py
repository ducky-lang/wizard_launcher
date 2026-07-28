"""One place that knows what operating system we are on.

Before this module the platform checks were scattered: ``os.name == "nt"``
in eight files, ``os.uname().sysname`` in one (which raises on Windows),
``xdg-open`` hardcoded as the non-Windows file manager (wrong on macOS).
Adding macOS support by patching each site would have guaranteed that one
of them stayed wrong.

Everything platform-specific the launcher needs is therefore funnelled
through here: process creation flags, opening a folder, showing a native
error box, and the per-machine identifiers the crypto fallback uses.
"""

import contextlib
import os
import subprocess
import sys

IS_WINDOWS = os.name == "nt"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = not IS_WINDOWS and not IS_MACOS

# Suffix used by Mojang's runtime layout, needed to find the JVMs the
# official launcher already downloaded.
if IS_WINDOWS:
    MOJANG_OS_DIRS = ("windows-x64", "windows-x86", "windows-arm64")
elif IS_MACOS:
    MOJANG_OS_DIRS = ("mac-os-arm64", "mac-os")
else:
    MOJANG_OS_DIRS = ("linux", "linux-i386")

JAVA_EXE = "java.exe" if IS_WINDOWS else "java"


def platform_name():
    return "Windows" if IS_WINDOWS else ("macOS" if IS_MACOS else "Linux")


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------
def no_window_kwargs():
    """Keyword arguments that stop a console window flashing up.

    ``CREATE_NO_WINDOW`` does not exist outside Windows, so this must never
    be inlined as ``subprocess.CREATE_NO_WINDOW if ... else 0`` in code that
    is also type-checked or imported on another platform.
    """
    if IS_WINDOWS:
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


class _NoWindowSubprocess:
    """A stand-in for the :mod:`subprocess` module that hides child consoles.

    Everything is forwarded to the real module except ``run`` and ``Popen``,
    which get ``CREATE_NO_WINDOW`` added.
    """

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    @staticmethod
    def _hidden(kwargs):
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
        return kwargs

    def run(self, *args, **kwargs):
        return self._real.run(*args, **self._hidden(kwargs))

    def Popen(self, *args, **kwargs):  # noqa: N802 - mirrors subprocess.Popen
        return self._real.Popen(*args, **self._hidden(kwargs))


@contextlib.contextmanager
def hide_child_consoles(*modules):
    """Stop third-party modules from flashing a console window.

    minecraft_launcher_lib passes ``startupinfo`` to hide the window when it
    runs the Forge and Quilt installers, but not the Fabric one - so on a
    first run, in a build that deliberately has no console of its own, a black
    window appears for as long as the Fabric installer takes. There is nothing
    to configure and no callback to intercept, so the module's own reference
    to :mod:`subprocess` is swapped for a wrapper while the call runs and put
    back afterwards.

    Windows-only in effect; a no-op everywhere else.
    """
    if not IS_WINDOWS:
        yield
        return

    patched = []
    try:
        for module in modules:
            real = getattr(module, "subprocess", None)
            if real is None:
                continue
            patched.append((module, real))
            setattr(module, "subprocess", _NoWindowSubprocess(real))
        yield
    finally:
        for module, real in patched:
            setattr(module, "subprocess", real)


def detached_kwargs():
    """Arguments for a child that must outlive this process.

    On POSIX the child has to leave our process group, otherwise closing the
    launcher's terminal sends SIGHUP straight to the watchdog and the very
    thing meant to survive a launcher exit dies with it.
    """
    if IS_WINDOWS:
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        CREATE_NO_WINDOW = 0x08000000
        return {
            "creationflags": CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
            "close_fds": True,
        }
    return {"start_new_session": True, "close_fds": True}


# ---------------------------------------------------------------------------
# Shell integration
# ---------------------------------------------------------------------------
def open_path(path):
    """Reveal a file or folder in the OS file manager. Returns True on success."""
    try:
        if IS_WINDOWS:
            os.startfile(path)  # noqa: S606 - user-initiated, app-owned path
            return True
        opener = "open" if IS_MACOS else "xdg-open"
        subprocess.Popen(
            [opener, path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


def show_message_box(message, title="Wizard Launcher"):
    """Last-resort user-visible error, for failures that happen before (or
    instead of) the UI. A GUI build has no console, so writing to stderr
    reaches nobody."""
    try:
        sys.stderr.write(message + "\n")
    except Exception:
        pass

    if IS_WINDOWS:
        try:
            import ctypes
            MB_ICONERROR = 0x10
            ctypes.windll.user32.MessageBoxW(0, message, title, MB_ICONERROR)
            return True
        except Exception:
            return False

    if IS_MACOS:
        try:
            # AppleScript needs its own quoting; a message containing a double
            # quote would otherwise truncate the dialog text.
            escaped = message.replace("\\", "\\\\").replace('"', '\\"')
            escaped_title = title.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.run(
                ["osascript", "-e",
                 f'display dialog "{escaped}" with title "{escaped_title}" '
                 f'buttons {{"OK"}} default button 1 with icon stop'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=120,
            )
            return True
        except Exception:
            return False

    for tool, args in (("zenity", ["--error", "--text", message, "--title", title]),
                       ("kdialog", ["--error", message, "--title", title])):
        try:
            subprocess.run([tool] + args, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=120)
            return True
        except Exception:
            continue
    return False


# ---------------------------------------------------------------------------
# Machine identity (used only by the portable crypto fallback)
# ---------------------------------------------------------------------------
def set_window_icon(window_title, ico_path, attempts=40, interval=0.25):
    """Give the launcher window our own icon instead of Flet's.

    Only meaningful on Windows, and only when running unpackaged. A build
    made by ``tools/build.py`` gets its icon from the executable itself
    (PyInstaller ``--icon``), which Windows uses for the window and the
    taskbar automatically. From a source checkout there is no such
    executable - the host process is Flet's own ``flet.exe``, so the window
    wears Flet's icon. This replaces it at runtime.

    Flet 0.24 does expose ``page.window.icon``, but it routes the value
    through the asset resolver before handing it to ``window_manager``, so an
    absolute path outside the assets folder does not survive. Talking to
    Win32 directly avoids guessing at that.

    The window does not exist yet when the app starts, so this polls for it.
    Returns True once the icon is set, False if the window never appeared -
    never raises, because a wrong icon is not worth failing a launch over.
    """
    if not IS_WINDOWS or not ico_path or not os.path.isfile(ico_path):
        return False

    try:
        import ctypes
        import time
        from ctypes import wintypes
    except Exception:
        return False

    WM_SETICON = 0x0080
    ICON_SMALL, ICON_BIG = 0, 1
    IMAGE_ICON = 1
    LR_LOADFROMFILE, LR_DEFAULTSIZE = 0x0010, 0x0040

    try:
        user32 = ctypes.windll.user32
        user32.FindWindowW.restype = wintypes.HWND
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                        wintypes.WPARAM, wintypes.LPARAM]

        for _ in range(max(1, attempts)):
            hwnd = user32.FindWindowW(None, window_title)
            if hwnd:
                break
            time.sleep(interval)
        else:
            return False

        # Small (title bar) and big (taskbar, Alt-Tab) are separate slots and
        # are loaded separately so each gets the size it wants out of the .ico
        # rather than a scaled copy of the other.
        for size, which in ((16, ICON_SMALL), (32, ICON_BIG)):
            handle = user32.LoadImageW(None, ico_path, IMAGE_ICON, size, size,
                                       LR_LOADFROMFILE | LR_DEFAULTSIZE)
            if handle:
                user32.SendMessageW(hwnd, WM_SETICON, which, handle)
        return True
    except Exception:
        return False


def machine_identifiers():
    """Stable per-machine strings, best effort, never fatal.

    Used as key material for the non-DPAPI encryption path so that copying
    ``session.dat`` to another computer produces a file that will not
    decrypt. Each platform gets an id that survives reboots but not a move
    to different hardware.
    """
    parts = []
    if IS_WINDOWS:
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography",
                0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
            ) as key:
                parts.append(winreg.QueryValueEx(key, "MachineGuid")[0])
        except Exception:
            pass
    elif IS_MACOS:
        try:
            out = subprocess.run(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                capture_output=True, text=True, timeout=10,
            ).stdout
            for line in out.splitlines():
                if "IOPlatformUUID" in line:
                    parts.append(line.split('"')[-2])
                    break
        except Exception:
            pass
    else:
        for candidate in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    parts.append(f.read().strip())
                break
            except Exception:
                continue
    return parts
