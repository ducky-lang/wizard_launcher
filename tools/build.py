#!/usr/bin/env python3
"""One command that turns this source tree into something a player can install.

    python tools/build.py --client-id <azure-guid>     full build + installer
    python tools/build.py --from-env                   read the id from the env
    python tools/build.py --no-package                 binary only, no installer
    python tools/build.py --no-flet-client             smaller build, see below

What it does, in order:

1. Checks the toolchain and refuses early with a readable message rather
   than half-building something.
2. Embeds the Azure client id, obfuscated, into the source tree - and always
   removes it again, even if the build fails or is interrupted. The plain
   value must never survive in a working tree.
3. Compiles with PyInstaller, using the platform-appropriate output format.
4. Packages: Inno Setup on Windows, a signed .app inside a DMG on macOS, an
   AppImage on Linux (via appimagetool).

Each platform's package can only be produced by running this script ON that
platform - PyInstaller does not cross-compile, so a Windows machine can only
ever build the .exe, never the .app or the AppImage.

The version comes from launcher_core/version.py and nowhere else; the Inno
Setup script is handed a generated include file so the two can never drift.

Why PyInstaller and not Nuitka: Nuitka compiles everything to C and back,
which is minutes of real compilation for a pure-Python app that gains little
from it - none of this launcher's secrets live in the binary (see
SECURITY_ARCHITECTURE.md; the Azure client id is a public OAuth id, and
Doppler tokens are never embedded at all). PyInstaller just freezes bytecode
and bundles the interpreter, which is a fraction of the time for the same
practical protection here. It also builds onedir instead of onefile, which
sidesteps the self-extracting-stub pattern that antivirus heuristics tend to
flag on first run.
"""

import argparse
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from launcher_core.version import (  # noqa: E402
    APP_NAME, EDITION, VERSION, APP_AUTHOR,
)

BUILD_DIR = os.path.join(ROOT, "build_pyinstaller")
DIST_DIR = os.path.join(ROOT, "build_installer")
ENTRY = os.path.join(ROOT, "launcher.py")

# No spaces: this is both the PyInstaller --name (which becomes the dist
# folder / .app stem) and the Windows exe filename the installer references.
APP_FILE_STEM = "WizardLauncher"
EXE_NAME = f"{APP_FILE_STEM}.exe"
APP_BUNDLE_NAME = f"{APP_FILE_STEM}.app"

IS_WINDOWS = os.name == "nt"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = not IS_WINDOWS and not IS_MACOS
DATA_SEP = ";" if IS_WINDOWS else ":"

# Flet ships a full web-server stack so the same app can be served in a
# browser. This launcher is desktop-only, and flet imports that stack lazily,
# inside the functions that serve a web app - so none of it is reachable here.
# Verified by dumping sys.modules after a real desktop startup: not one of
# these appears among the 879 modules that actually load.
#
# Excluding them isn't just tidiness: PyInstaller's import analysis still has
# to walk each one to decide it's unreachable, and pydantic/fastapi/sqlalchemy
# between them are a large share of that walk. It also comes straight off the
# size of the shipped bundle.
#
# httpx IS imported at startup, so its own runtime dependencies (anyio,
# httpcore, h11, sniffio) are deliberately kept even though they are heavy:
# they load lazily on the first request, and being wrong about that would
# mean an ImportError in front of a player rather than a slow build.
UNREACHABLE_PACKAGES = [
    "flet.fastapi",
    "fastapi",
    "starlette",
    "uvicorn",
    "pydantic",
    "pydantic_core",
    "sqlalchemy",
    "websockets",
    "tkinter",
    "unittest",
    "pydoc",
]

# psutil compiles one implementation module per operating system and picks at
# import time. The others cannot run here and are pure build cost.
PSUTIL_FOREIGN = {
    "nt": ("psutil._pslinux", "psutil._psosx", "psutil._psbsd", "psutil._pssunos"),
    "posix": ("psutil._pswindows",),
}


class BuildError(Exception):
    pass


def keep_build_off_system_drive():
    """Point TEMP/TMP at a folder next to the project instead of the system
    drive's default temp location.

    This machine's C: has been seen down to a few GB free, and PyInstaller,
    pip and any subprocess they spawn all default to the system temp dir.
    Respects the variables if the caller already set them.
    """
    temp_dir = os.path.join(BUILD_DIR, "tmp")

    if not os.environ.get("WIZARD_BUILD_KEEP_TEMP"):
        os.makedirs(temp_dir, exist_ok=True)
        for name in ("TEMP", "TMP"):
            os.environ[name] = temp_dir


# ---------------------------------------------------------------------------
# Console
# ---------------------------------------------------------------------------
_step_number = 0


def step(message):
    global _step_number
    _step_number += 1
    print(f"\n\033[1m[{_step_number}] {message}\033[0m", flush=True)


def info(message):
    print(f"    {message}", flush=True)


def run(cmd, cwd=None, what=None):
    info("$ " + " ".join(str(c) for c in cmd))
    result = subprocess.run(cmd, cwd=cwd or ROOT)
    if result.returncode != 0:
        raise BuildError(f"{what or cmd[0]} failed with exit code {result.returncode}.")


# ---------------------------------------------------------------------------
# Toolchain
# ---------------------------------------------------------------------------
def check_toolchain(package):
    if sys.version_info < (3, 9):
        raise BuildError(f"Python 3.9+ is required to build; this is {platform.python_version()}.")

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        raise BuildError(
            "PyInstaller is not installed in this interpreter.\n"
            f"    Fix: {sys.executable} -m pip install pyinstaller"
        )

    try:
        import flet  # noqa: F401
    except ImportError:
        raise BuildError(
            "The launcher's own dependencies are not installed in this interpreter.\n"
            f"    Fix: {sys.executable} -m pip install -r requirements.txt"
        )

    if package and IS_WINDOWS and not find_iscc():
        raise BuildError(
            "Inno Setup (ISCC.exe) was not found, so the installer cannot be built.\n"
            "    Fix: install it from https://jrsoftware.org/isdl.php\n"
            "         or re-run with --no-package to build just the exe."
        )
    if package and IS_MACOS and not shutil.which("hdiutil"):
        raise BuildError("hdiutil is missing; a DMG cannot be created on this machine.")

    if package and IS_LINUX and not find_appimagetool():
        raise BuildError(
            "appimagetool was not found, so the AppImage cannot be built.\n"
            "    Fix: download it from "
            "https://github.com/AppImage/AppImageKit/releases and put it on "
            "PATH as 'appimagetool' (chmod +x it first),\n"
            "         or re-run with --no-package to build just the onedir folder."
        )


def find_appimagetool():
    override = (os.environ.get("APPIMAGETOOL_PATH") or "").strip()
    if override and os.path.isfile(override):
        return override
    return shutil.which("appimagetool")


def find_iscc():
    """Locate the Inno Setup compiler.

    Guessing at Program Files is not enough - Inno Setup is routinely
    installed to a second drive, which is exactly what happened on the
    machine this was written on. The registry entry the installer writes is
    the authoritative answer, so it is consulted before any guessing.
    """
    override = (os.environ.get("ISCC_PATH") or "").strip()
    if override and os.path.isfile(override):
        return override

    found = shutil.which("iscc") or shutil.which("ISCC")
    if found:
        return found

    for location in _registry_inno_locations():
        candidate = os.path.join(location, "ISCC.exe")
        if os.path.isfile(candidate):
            return candidate

    for base in (
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("LOCALAPPDATA", ""),
    ):
        if not base:
            continue
        for version in ("Inno Setup 6", "Inno Setup 5", os.path.join("Programs", "Inno Setup 6")):
            candidate = os.path.join(base, version, "ISCC.exe")
            if os.path.isfile(candidate):
                return candidate
    return None


def _registry_inno_locations():
    """InstallLocation values for every installed Inno Setup, newest first."""
    if not IS_WINDOWS:
        return []
    try:
        import winreg
    except ImportError:
        return []

    locations = []
    roots = [
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, path in roots:
        try:
            with winreg.OpenKey(hive, path) as key:
                for index in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        name = winreg.EnumKey(key, index)
                        if "inno setup" not in name.lower():
                            continue
                        with winreg.OpenKey(key, name) as sub:
                            value = winreg.QueryValueEx(sub, "InstallLocation")[0]
                            if value:
                                locations.append(value.rstrip("\\/"))
                    except OSError:
                        continue
        except OSError:
            continue
    return sorted(set(locations), reverse=True)


def flet_bin_dir():
    """The folder holding the bundled Flet desktop client.

    Without it the compiled launcher downloads a ~30 MB UI runtime from
    GitHub the first time a player opens it - on a machine that may be
    offline, behind a proxy, or in front of someone who just wants to play.
    """
    try:
        import flet
        candidate = os.path.join(os.path.dirname(flet.__file__), "bin")
        return candidate if os.path.isdir(candidate) else ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Client id embedding
# ---------------------------------------------------------------------------
def read_dotenv_client_id():
    """Read the developer .env in the project root. Never echoed to the
    console - the whole point of the embed/clean dance is that the plain
    value stays out of anything that gets shared, and build logs get shared."""
    path = os.path.join(ROOT, ".env")
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line.startswith("MC_LAUNCHER_CLIENT_ID="):
                    value = line.split("=", 1)[1].strip().strip('"').strip("'")
                    return "" if value.startswith("0000") else value
    except OSError:
        return ""
    return ""


class EmbeddedClientId:
    """Context manager: embed on entry, always clear on exit.

    A try/finally rather than two shell commands, because the failure mode of
    forgetting the second one is a credential committed to git.
    """

    def __init__(self, client_id):
        self.client_id = client_id
        self.applied = False

    def __enter__(self):
        if not self.client_id:
            info("No client id supplied - Microsoft sign-in will be unavailable in this build.")
            return self
        run([sys.executable, os.path.join(ROOT, "tools", "embed_secret.py"),
             "--client-id", self.client_id], what="embed_secret")
        self.applied = True
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.applied:
            subprocess.run(
                [sys.executable, os.path.join(ROOT, "tools", "embed_secret.py"), "--clean"],
                cwd=ROOT,
            )
            info("Cleared the embedded client id from the working tree.")
        return False


# ---------------------------------------------------------------------------
# Windows version resource
# ---------------------------------------------------------------------------
def _version_tuple():
    # VERSION may carry build metadata ("1.0.1+2") or a pre-release suffix; the
    # Windows resource needs four integers. Take the dotted numeric core as the
    # first fields and a trailing "+N" build number as the fourth.
    core, _, build = VERSION.partition("+")
    core = core.split("-", 1)[0]
    parts = []
    for p in core.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            break
    try:
        build_no = int(build)
    except ValueError:
        build_no = 0
    parts = (parts + [0, 0, 0])[:3] + [build_no]
    return tuple(parts[:4])


def write_windows_version_file():
    """PyInstaller's --version-file wants a Python-literal describing the
    exe's Windows version resource - Nuitka used to generate this from plain
    flags, PyInstaller needs it spelled out."""
    vers = _version_tuple()
    os.makedirs(BUILD_DIR, exist_ok=True)
    path = os.path.join(BUILD_DIR, "version_info.txt")
    content = f'''# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={vers!r},
    prodvers={vers!r},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
      StringTable(
        u"040904B0",
        [StringStruct(u"CompanyName", u"{APP_AUTHOR}"),
        StringStruct(u"FileDescription", u"{APP_NAME} - {EDITION}"),
        StringStruct(u"FileVersion", u"{VERSION}"),
        StringStruct(u"InternalName", u"{APP_FILE_STEM}"),
        StringStruct(u"OriginalFilename", u"{EXE_NAME}"),
        StringStruct(u"ProductName", u"{APP_NAME}"),
        StringStruct(u"ProductVersion", u"{VERSION}")])
      ]),
    VarFileInfo([VarStruct(u"Translation", [1033, 1200])])
  ]
)
'''
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


# ---------------------------------------------------------------------------
# Compile
# ---------------------------------------------------------------------------
def common_pyinstaller_args(bundle_flet_client):
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--windowed",
        "--name", APP_FILE_STEM,
        f"--distpath={BUILD_DIR}",
        f"--workpath={os.path.join(BUILD_DIR, 'work')}",
        f"--specpath={BUILD_DIR}",
        # data/*.json (catalog, changelog, content, theme) are not code, so
        # import analysis alone does not carry them - same reason Nuitka
        # needed --include-package-data before. Missing this compiles cleanly
        # and ships a launcher with no FAQ, no onboarding text and no map URL,
        # because every accessor silently falls back to its built-in default.
        "--collect-data=launcher_core",
        # Force in every submodule, even one only ever reached through a lazy
        # `from .x import y` inside a function body (secrets and settings load
        # like that on purpose) that import analysis might not walk.
        "--collect-submodules=launcher_core",
        "--collect-submodules=ui",
    ]

    excluded = list(UNREACHABLE_PACKAGES)
    excluded += PSUTIL_FOREIGN["nt" if IS_WINDOWS else "posix"]
    for name in excluded:
        args += ["--exclude-module", name]

    if bundle_flet_client:
        bin_dir = flet_bin_dir()
        if bin_dir:
            args += ["--add-binary", f"{bin_dir}{DATA_SEP}flet/bin"]
        else:
            info("WARNING: the Flet client binary was not found; the build will "
                 "download it on first run.")
    return args


def build_windows(bundle_flet_client):
    icon = os.path.join(ROOT, "installer", "app.ico")
    version_file = write_windows_version_file()
    args = common_pyinstaller_args(bundle_flet_client) + [
        f"--version-file={version_file}",
    ]
    if os.path.isfile(icon):
        args.append(f"--icon={icon}")
    args.append(ENTRY)
    run(args, what="PyInstaller")

    produced_dir = os.path.join(BUILD_DIR, APP_FILE_STEM)
    produced_exe = os.path.join(produced_dir, EXE_NAME)
    if not os.path.isfile(produced_exe):
        raise BuildError(f"PyInstaller reported success but {produced_exe} does not exist.")
    return produced_dir


def build_macos(bundle_flet_client):
    icon = os.path.join(ROOT, "installer", "macos", "app.icns")
    args = common_pyinstaller_args(bundle_flet_client) + [
        "--osx-bundle-identifier=net.foxy.wizardlauncher",
    ]
    if os.path.isfile(icon):
        args.append(f"--icon={icon}")
    args.append(ENTRY)
    run(args, what="PyInstaller")

    produced = os.path.join(BUILD_DIR, APP_BUNDLE_NAME)
    if not os.path.isdir(produced):
        raise BuildError("PyInstaller reported success but no .app bundle was produced.")

    stage_macos_resources(produced)
    codesign_adhoc(produced)
    return produced


def stage_macos_resources(app_bundle):
    """Copy the server / proxy / mod jars into the bundle.

    A .app is the unit of installation on macOS - there is no separate
    installer step that could place files elsewhere - so everything the
    launcher needs has to ride inside it. launcher_core/bootstrap.py copies
    them out to Application Support on first run, because the bundle itself
    must stay read-only for the code signature to hold.
    """
    source = os.path.join(ROOT, "resources")
    if not os.path.isdir(source):
        raise BuildError(f"resources/ not found at {source}; nothing to bundle.")

    destination = os.path.join(app_bundle, "Contents", "Resources", "resources")
    if os.path.isdir(destination):
        shutil.rmtree(destination)

    def ignore(directory, names):
        # The player's world and any downloaded copies are not shippable.
        return {n for n in names if n.lower() in {"world", "logs", "copy", "minecraft"}}

    shutil.copytree(source, destination, ignore=ignore)
    info(f"Bundled game files into {os.path.relpath(destination, ROOT)}")


def build_linux(bundle_flet_client):
    args = common_pyinstaller_args(bundle_flet_client)
    icon = os.path.join(ROOT, "installer", "linux", "app.png")
    if os.path.isfile(icon):
        args.append(f"--icon={icon}")
    args.append(ENTRY)
    run(args, what="PyInstaller")

    produced = os.path.join(BUILD_DIR, APP_FILE_STEM)
    produced_bin = os.path.join(produced, APP_FILE_STEM)
    if not os.path.isfile(produced_bin):
        raise BuildError(f"PyInstaller reported success but {produced_bin} does not exist.")

    stage_linux_resources(produced)
    return produced


def stage_linux_resources(app_dir):
    """Copy the server / proxy / mod jars next to the onedir executable.

    There is no separate install step on Linux the way Inno Setup provides
    one on Windows, so - same reasoning as stage_macos_resources - the game
    files have to ride inside the thing that gets packaged. get_bundled_resources_dir()
    (launcher_core/paths.py) already looks next to the executable by default,
    which is exactly this layout.
    """
    source = os.path.join(ROOT, "resources")
    if not os.path.isdir(source):
        raise BuildError(f"resources/ not found at {source}; nothing to bundle.")

    destination = os.path.join(app_dir, "resources")
    if os.path.isdir(destination):
        shutil.rmtree(destination)

    def ignore(directory, names):
        return {n for n in names if n.lower() in {"world", "logs", "copy", "minecraft"}}

    shutil.copytree(source, destination, ignore=ignore)
    info(f"Bundled game files into {os.path.relpath(destination, ROOT)}")


def codesign_adhoc(app_bundle):
    """Ad-hoc sign so macOS will run the bundle at all.

    An unsigned bundle downloaded from the internet is refused outright on
    Apple silicon. Ad-hoc signing is not notarisation - Gatekeeper still
    shows the "unidentified developer" prompt - but it is the difference
    between "right-click, Open" working and the app being unlaunchable.
    Pass a real Developer ID here when you have one.
    """
    identity = os.environ.get("MACOS_SIGN_IDENTITY", "-")
    try:
        run(["codesign", "--force", "--deep", "--sign", identity, app_bundle],
            what="codesign")
    except BuildError as e:
        info(f"WARNING: {e} The app may need a right-click > Open on first launch.")


# ---------------------------------------------------------------------------
# Package
# ---------------------------------------------------------------------------
def write_iss_version_include():
    """Single source of truth: version.py feeds the installer script."""
    path = os.path.join(ROOT, "installer", "version.iss")
    # VersionInfoVersion in the .iss must be numeric X.X.X.X; VERSION may carry
    # build metadata ("1.0.1+2"), so the installer's file-version resource uses
    # this derived numeric form while the visible AppVersion keeps the full one.
    numeric = ".".join(str(p) for p in _version_tuple())
    content = (
        "; Generated by tools/build.py - do not edit.\n"
        f'#define MyAppVersion "{VERSION}"\n'
        f'#define MyAppVersionNumeric "{numeric}"\n'
        f'#define MyAppEdition "{EDITION}"\n'
        f'#define MyAppName "{APP_NAME}"\n'
        f'#define MyAppPublisher "{APP_AUTHOR}"\n'
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def package_windows():
    write_iss_version_include()
    os.makedirs(DIST_DIR, exist_ok=True)
    iscc = find_iscc()
    run([iscc, os.path.join(ROOT, "installer", "WizardLauncher.iss")], what="Inno Setup")
    expected = os.path.join(DIST_DIR, f"WizardLauncher-Windows-Setup-{VERSION}.exe")
    if not os.path.isfile(expected):
        raise BuildError(f"Inno Setup finished but {expected} is missing.")
    return expected


def package_macos(app_bundle):
    os.makedirs(DIST_DIR, exist_ok=True)
    dmg = os.path.join(DIST_DIR, f"WizardLauncher-macOS-{VERSION}.dmg")
    if os.path.exists(dmg):
        os.remove(dmg)

    staging = os.path.join(BUILD_DIR, "dmg_staging")
    if os.path.isdir(staging):
        shutil.rmtree(staging)
    os.makedirs(staging)
    shutil.copytree(app_bundle, os.path.join(staging, os.path.basename(app_bundle)),
                    symlinks=True)
    # The Applications symlink is what makes the window a drag-to-install
    # target instead of a folder the user has to understand.
    os.symlink("/Applications", os.path.join(staging, "Applications"))

    run([
        "hdiutil", "create",
        "-volname", f"{APP_NAME} {VERSION}",
        "-srcfolder", staging,
        "-ov", "-format", "UDZO",
        dmg,
    ], what="hdiutil")
    shutil.rmtree(staging, ignore_errors=True)
    return dmg


def package_linux(app_dir):
    """Wrap the onedir build in an AppDir and hand it to appimagetool.

    AppImage rather than a .deb/.rpm because it needs no package manager, no
    root, and runs on distros this was never built for - the same per-user,
    no-privileges philosophy as the Windows installer (PrivilegesRequired=lowest)
    and the macOS drag-to-Applications DMG.
    """
    os.makedirs(DIST_DIR, exist_ok=True)
    appimage = os.path.join(DIST_DIR, f"WizardLauncher-Linux-{VERSION}.AppImage")
    if os.path.exists(appimage):
        os.remove(appimage)

    staging = os.path.join(BUILD_DIR, "AppDir")
    if os.path.isdir(staging):
        shutil.rmtree(staging)
    os.makedirs(staging)

    shutil.copytree(app_dir, os.path.join(staging, APP_FILE_STEM), symlinks=True)

    apprun = os.path.join(staging, "AppRun")
    with open(apprun, "w", encoding="utf-8", newline="\n") as f:
        f.write(
            "#!/bin/sh\n"
            'HERE="$(dirname "$(readlink -f "$0")")"\n'
            f'exec "$HERE/{APP_FILE_STEM}/{APP_FILE_STEM}" "$@"\n'
        )
    os.chmod(apprun, 0o755)

    desktop_src = os.path.join(ROOT, "installer", "linux", "wizardlauncher.desktop")
    desktop_dst = os.path.join(staging, f"{APP_FILE_STEM}.desktop")
    if os.path.isfile(desktop_src):
        shutil.copy2(desktop_src, desktop_dst)
    else:
        with open(desktop_dst, "w", encoding="utf-8", newline="\n") as f:
            f.write(
                "[Desktop Entry]\n"
                "Type=Application\n"
                f"Name={APP_NAME}\n"
                "Exec=AppRun\n"
                "Icon=wizardlauncher\n"
                "Categories=Game;\n"
                "Terminal=false\n"
            )

    icon_src = os.path.join(ROOT, "installer", "linux", "app.png")
    if os.path.isfile(icon_src):
        shutil.copy2(icon_src, os.path.join(staging, "wizardlauncher.png"))

    appimagetool = find_appimagetool()
    run([appimagetool, staging, appimage], what="appimagetool")
    shutil.rmtree(staging, ignore_errors=True)
    return appimage


def write_checksums():
    """SHA256SUMS.txt covering every installer currently in build_installer/.

    Regenerated from whatever is actually present, so building on one
    platform doesn't need the other two's artifacts to be there too - a CI
    matrix that gathers all three into one place before a release just gets
    an up-to-date file for free on its last job.
    """
    lines = []
    for name in sorted(os.listdir(DIST_DIR)):
        if name == "SHA256SUMS.txt":
            continue
        path = os.path.join(DIST_DIR, name)
        if not os.path.isfile(path):
            continue
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                digest.update(chunk)
        lines.append(f"{digest.hexdigest()}  {name}")

    path = os.path.join(DIST_DIR, "SHA256SUMS.txt")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))
    return path


# ---------------------------------------------------------------------------
def human_size(path):
    try:
        if os.path.isdir(path):
            total = sum(
                os.path.getsize(os.path.join(dirpath, name))
                for dirpath, _dirs, names in os.walk(path)
                for name in names
            )
        else:
            total = os.path.getsize(path)
        return f"{total / (1024 * 1024):.1f} MB"
    except OSError:
        return "?"


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--client-id", help="Azure AD application (client) id to embed")
    parser.add_argument("--from-env", action="store_true",
                        help="take the client id from MC_LAUNCHER_CLIENT_ID")
    parser.add_argument("--from-dotenv", action="store_true",
                        help="take the client id from the developer .env in the project root")
    parser.add_argument("--no-package", action="store_true",
                        help="stop after the executable / app bundle")
    parser.add_argument("--no-flet-client", action="store_true",
                        help="do not bundle the UI runtime (smaller build, but the "
                             "first launch downloads it)")
    parser.add_argument("--clean", action="store_true",
                        help="remove previous build output first")
    args = parser.parse_args()

    client_id = args.client_id
    if args.from_env:
        client_id = os.environ.get("MC_LAUNCHER_CLIENT_ID", "").strip()
    elif args.from_dotenv:
        client_id = read_dotenv_client_id()
        if not client_id:
            print("No MC_LAUNCHER_CLIENT_ID found in .env", file=sys.stderr)
            return 1

    started = time.time()
    package = not args.no_package

    try:
        step("Checking the build toolchain")
        check_toolchain(package)
        info(f"{APP_NAME} {VERSION} ({EDITION}) on {platform.platform()}")
        keep_build_off_system_drive()

        if args.clean:
            step("Cleaning previous build output")
            for path in (BUILD_DIR, DIST_DIR):
                if os.path.isdir(path):
                    shutil.rmtree(path, ignore_errors=True)
                    info(f"removed {os.path.relpath(path, ROOT)}")

        with EmbeddedClientId(client_id):
            step("Compiling with PyInstaller")
            if IS_WINDOWS:
                built = build_windows(not args.no_flet_client)
            elif IS_MACOS:
                built = build_macos(not args.no_flet_client)
            else:
                built = build_linux(not args.no_flet_client)
            info(f"Built {os.path.relpath(built, ROOT)} ({human_size(built)})")

        artifact = built
        if package:
            step("Packaging the installer")
            if IS_WINDOWS:
                artifact = package_windows()
            elif IS_MACOS:
                artifact = package_macos(built)
            else:
                artifact = package_linux(built)

            step("Writing checksums")
            checksums = write_checksums()
            info(os.path.relpath(checksums, ROOT))

        elapsed = time.time() - started
        step("Done")
        print(f"\n    {os.path.relpath(artifact, ROOT)}  ({human_size(artifact)})")
        print(f"    finished in {elapsed / 60:.1f} min\n")
        return 0

    except BuildError as e:
        print(f"\n\033[31mBuild failed:\033[0m {e}\n", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nBuild cancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
