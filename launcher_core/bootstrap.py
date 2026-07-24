"""Seed the writable data folder from the resources shipped with the app.

On Windows the Inno Setup installer ships ``resources/`` next to the
executable and this is what puts them where the launcher reads them, so it
doubles as a safety net: if a jar goes missing - antivirus quarantine, a
half-finished uninstall, a user tidying up - the launcher repairs itself on
the next start instead of failing at step four with "server.jar is missing".

On macOS it is not a safety net but the actual install mechanism. A .app
bundle is a read-only, relocatable, code-signed directory; the server and
proxy jars ride along inside ``Contents/Resources`` and are copied out to
Application Support the first time the launcher runs.

The copy rules are deliberately asymmetric:

* **Jars are refreshed** when the bundled copy is newer, so an app update
  actually updates ViaProxy and the server.
* **Everything else is copied only when absent**, because those are files
  the player or the server owns - ``server.properties`` carries their
  settings, and plugin configs carry their world's state. Overwriting them
  on every launch would quietly undo the user's changes.
* **The world is never touched.**
"""

import os
import shutil

# Directories under resources/ that belong to the player at runtime and must
# never be overwritten from the bundle.
_SKIP_DIRS = {"world", "logs", "copy", "minecraft", "crash-reports", "cache"}

_REFRESHABLE_SUFFIXES = (".jar",)


def _iter_bundled_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.lower() not in _SKIP_DIRS]
        for filename in filenames:
            full = os.path.join(dirpath, filename)
            yield full, os.path.relpath(full, root)


def _should_copy(src, dst):
    if not os.path.exists(dst):
        return True
    if not src.lower().endswith(_REFRESHABLE_SUFFIXES):
        return False
    try:
        # 2s tolerance: FAT/exFAT timestamps have 2-second granularity, and a
        # jar copied onto such a volume would otherwise look "newer" forever.
        return os.path.getmtime(src) > os.path.getmtime(dst) + 2
    except OSError:
        return False


def ensure_data_resources(bundled_dir, data_resources_dir, log=None):
    """Copy missing (or outdated) bundled resources into the data folder.

    Returns the number of files written. Never raises: a launcher that
    cannot self-repair should still start and show a precise error later,
    rather than dying here with a traceback.
    """
    log = log or (lambda message: None)
    if not bundled_dir or not os.path.isdir(bundled_dir):
        return 0
    if os.path.abspath(bundled_dir) == os.path.abspath(data_resources_dir):
        return 0  # running from a source checkout: they are the same folder

    copied = 0
    failed = 0
    for src, relative in _iter_bundled_files(bundled_dir):
        dst = os.path.join(data_resources_dir, relative)
        if not _should_copy(src, dst):
            continue
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            copied += 1
        except Exception as e:
            failed += 1
            log(f"Could not install bundled file {relative}: {e}")

    if copied:
        log(f"Installed {copied} bundled game file(s).")
    if failed:
        log(f"{failed} bundled file(s) could not be installed.")
    return copied
