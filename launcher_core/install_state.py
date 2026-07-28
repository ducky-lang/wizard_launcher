"""A record of what the launcher has already finished, so it stops redoing it.

Most of the provisioning work is idempotent, which is not the same as free.
Copying a 350 MB resource pack into the game folder, rewriting ``options.txt``
and re-walking the mod list produced the same result on the two-hundredth
launch as on the first - it just took twenty seconds to prove it. This module
is how a launch says "I already did that, with exactly these inputs" and
skips straight past.

The design is deliberately dumb:

* **One flat ``{key: fingerprint}`` map**, persisted as JSON next to the
  settings. No schema, no migrations - an unreadable or unrecognised file is
  discarded and every step simply runs again, which is the safe direction.
* **A fingerprint is an opaque string derived from the inputs.** Change the
  modpack version, the pack URL or the destination folder and the fingerprint
  changes, so the step re-runs without anybody having to remember to
  invalidate anything.
* **Never the only check.** Every caller pairs :meth:`InstallState.matches`
  with a cheap existence test on what the step was supposed to produce. State
  says "I did this"; the filesystem says "and it is still there". A player who
  deletes a folder by hand gets it rebuilt, rather than a launcher that
  insists the work is done.

That last rule is why this cache cannot strand somebody in a broken install:
the worst a stale entry can do is be ignored.
"""

import hashlib
import json
import os
import threading

from .paths import get_data_root

STATE_FILE = "install-state.json"


def fingerprint(*parts):
    """A short, stable digest of whatever identifies a piece of work.

    Values are stringified and joined with a separator that cannot appear in
    a path, so ``("a/b", "c")`` and ``("a", "b/c")`` do not collide.
    """
    joined = "\x1f".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:32]


class InstallState:
    """Thread-safe, crash-safe map of completed provisioning steps."""

    def __init__(self, log=None, path=None):
        self.log = log or (lambda message: None)
        self.path = path or os.path.join(get_data_root(), STATE_FILE)
        self._lock = threading.Lock()
        self._data = {}
        self.load()

    # ------------------------------------------------------------------
    def load(self):
        if not os.path.isfile(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as e:
            # Corrupt or hand-edited: forget everything and re-verify. The
            # cost is one slow launch, which beats trusting a garbled file.
            self.log(f"install-state.json unreadable ({e}); every step will re-run.")
            return
        if not isinstance(raw, dict):
            return
        with self._lock:
            self._data = {
                str(key): str(value)
                for key, value in raw.items()
                if isinstance(key, str) and isinstance(value, str)
            }

    def save(self):
        with self._lock:
            snapshot = dict(self._data)
        tmp = self.path + ".tmp"
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
        except Exception as e:
            # A step that cannot be recorded is a step that runs again next
            # time. Slower, never wrong - so this is not worth failing over.
            self.log(f"Could not save install-state.json: {e}")

    # ------------------------------------------------------------------
    def get(self, key, default=""):
        """The recorded value for `key`.

        Most callers want :meth:`matches`, but a step sometimes needs to
        remember *what* it produced rather than only that it ran - the
        installed Fabric version id, for instance, which is otherwise found
        by walking the versions folder on every launch.
        """
        with self._lock:
            return self._data.get(key, default)

    def matches(self, key, value):
        """True when `key` was last completed with exactly this fingerprint."""
        if not value:
            return False
        with self._lock:
            return self._data.get(key) == value

    def mark(self, key, value):
        with self._lock:
            if self._data.get(key) == value:
                return
            self._data[key] = value
        self.save()

    def clear(self, *keys):
        """Forget specific steps, or everything when called with no arguments."""
        with self._lock:
            if not keys:
                if not self._data:
                    return
                self._data = {}
            else:
                removed = [key for key in keys if self._data.pop(key, None) is not None]
                if not removed:
                    return
        self.save()
