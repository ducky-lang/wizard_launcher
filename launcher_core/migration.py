"""One-time relocation of the player's data folder.

Wizard Launcher used to keep everything - the world, the downloaded client,
logs, the encrypted session - in ``Documents\\WizardLauncher``. That was the
wrong place and it showed:

* Documents is for files the *user* created. A multi-gigabyte Minecraft
  world, a JVM cache and a rotating log file are application state.
* OneDrive and every corporate profile-roaming policy sync Documents. A
  2 GB world folder being uploaded in the background while the server
  writes to it is a corruption bug waiting to happen, and the user is the
  one who pays for the bandwidth.
* Documents can be redirected to a network share, where file locking is
  unreliable and the server's ``level.dat`` write can fail halfway.

The data now lives in ``%LOCALAPPDATA%\\WizardLauncher``, which is exactly
what LocalAppData is for: per-user, per-machine, never roamed.

This module exists so that an existing install does not lose its castle in
the move. The rules are deliberately cautious:

* The move is attempted **once**, and only when the new location does not
  already hold data.
* A same-volume move is a rename - instant, atomic, no risk of a half-copied
  world. Documents and LocalAppData are both under the user profile, so this
  is what actually happens on virtually every machine.
* If the rename fails for any reason (a locked file, Documents redirected to
  another volume, permissions), **the old folder keeps being used**. A
  launcher that cannot move your world must never behave as though you never
  had one.
"""

import os
import shutil

MOVED_NOTE_NAME = "WizardLauncher - moved.txt"

_MOVED_NOTE = """\
Wizard Launcher no longer stores game data in your Documents folder.

Everything - your world, your progress, the downloaded game files and the
logs - was moved to:

    {new_root}

Nothing was deleted. You can safely delete this note.

Why the move: Documents is synced by OneDrive on many PCs, and syncing a
multi-gigabyte Minecraft world while the server is writing to it can damage
the world. Application data belongs in the AppData folder instead.
"""


def _has_content(path):
    if not os.path.isdir(path):
        return False
    try:
        with os.scandir(path) as entries:
            return any(True for _ in entries)
    except OSError:
        return False


def _leave_note(legacy_root, new_root, log):
    """Explain where the folder went, for the player who goes looking."""
    try:
        note_path = os.path.join(os.path.dirname(legacy_root), MOVED_NOTE_NAME)
        with open(note_path, "w", encoding="utf-8") as f:
            f.write(_MOVED_NOTE.format(new_root=new_root))
    except OSError as e:
        log(f"Could not write the relocation note: {e}")


def migrate_data_root(legacy_root, new_root, log=None):
    """Move ``legacy_root`` to ``new_root`` if that is both needed and safe.

    Returns the path that should actually be used. Never raises: every
    failure path falls back to a location that exists and is writable.
    """
    log = log or (lambda message: None)

    if not legacy_root or not new_root:
        return new_root
    if os.path.abspath(legacy_root) == os.path.abspath(new_root):
        return new_root

    # Already migrated, or a fresh install that started life in the new place.
    if _has_content(new_root):
        return new_root
    if not _has_content(legacy_root):
        return new_root

    log(f"Moving game data from {legacy_root} to {new_root}...")
    try:
        os.makedirs(os.path.dirname(new_root), exist_ok=True)
        # An empty directory may already have been created by an earlier
        # get_data_root() call; os.replace refuses a non-empty target and
        # os.rename refuses any existing directory on Windows, so clear it.
        if os.path.isdir(new_root):
            os.rmdir(new_root)
        os.rename(legacy_root, new_root)
    except OSError as rename_error:
        # Cross-volume (Documents on D:, profile on C:) is the one case worth
        # the slower path: a copy is still far better than starting over.
        if getattr(rename_error, "errno", None) == 18 or "different disk" in str(rename_error).lower():
            try:
                shutil.copytree(legacy_root, new_root, dirs_exist_ok=True)
                log("Game data copied to the new folder; the old copy was left in place.")
                _leave_note(legacy_root, new_root, log)
                return new_root
            except Exception as copy_error:
                log(f"Could not copy the game data: {copy_error}")
        else:
            log(f"Could not move the game data: {rename_error}")

        # Staying put is the safe answer. The player keeps their world and the
        # launcher keeps working; we just try again next start.
        log("Continuing to use the existing folder in Documents.")
        os.makedirs(legacy_root, exist_ok=True)
        return legacy_root

    log("Game data moved.")
    _leave_note(legacy_root, new_root, log)
    return new_root
