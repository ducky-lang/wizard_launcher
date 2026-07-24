"""User-facing copy and presentation data, loaded from ``data/*.json``.

Everything the player reads - the FAQ, the onboarding steps, the story, the
troubleshooting fixes, the palette, the changelog - used to be string
literals scattered through ``ui/main_window.py``. Moving it out buys three
things that matter once this stops being a weekend project:

* **Copy can be edited without touching code.** Fixing a typo in the FAQ is
  a text edit, not a diff against a 2,000-line UI module.
* **The same content has one home.** The launch step labels drive the
  progress bar *and* the error messages; the troubleshooting entries feed
  both the help dialog and the diagnostics report.
* **It can be validated.** A missing key degrades to a built-in default
  rather than raising ``KeyError`` in the middle of building a dialog.

Nothing here is ever fetched from the network. These are files that ship
with the application; the loader treats them as trusted-but-fallible, not
as untrusted input.
"""

import json
import os
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))

# Built-in last resort. Only ever used if the data file is missing or
# unreadable - a packaging mistake should degrade the wording, not stop
# somebody from playing.
_FALLBACK = {
    "content": {
        "story": {"title": "Witchcraft & Wizardry", "tagline": "", "paragraphs": [], "facts": []},
        "onboarding": {"steps": []},
        "account_modes": [],
        "getting_started": [],
        "faq": [],
        "troubleshooting": [],
        "idle_messages": ["Spells are humming in the halls..."],
        "incantations": ["Aperio castellum"],
        "launch_steps": [
            {"key": "java", "label": "Finding Java", "weight": 1},
            {"key": "resources", "label": "Checking game files", "weight": 2},
            {"key": "world", "label": "Preparing the castle", "weight": 3},
            {"key": "client_files", "label": "Preparing the client", "weight": 3},
            {"key": "server", "label": "Starting the server", "weight": 2},
            {"key": "proxy", "label": "Opening the portal", "weight": 1},
            {"key": "client", "label": "Launching Minecraft", "weight": 1},
        ],
    },
    "theme": {},
    "changelog": {"releases": []},
}

_CACHE = {}
_LOCK = threading.Lock()


def _candidate_dirs():
    """Where ``data/`` can be, in order of preference.

    Under Nuitka the package data is unpacked next to the compiled module,
    which ``__file__`` already points at. The extra candidates cover a build
    where the data was staged beside the executable instead, so a packaging
    slip shows up as "which folder?" rather than a launcher with no text.
    """
    dirs = [os.path.join(_HERE, "data")]
    try:
        from .paths import get_app_root
        app_root = get_app_root()
        dirs.append(os.path.join(app_root, "launcher_core", "data"))
        dirs.append(os.path.join(app_root, "data"))
    except Exception:
        pass
    return dirs


def _read(name):
    for directory in _candidate_dirs():
        path = os.path.join(directory, f"{name}.json")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    return None


def load(name):
    """Return the parsed data file ``name``. Cached; never raises."""
    with _LOCK:
        if name not in _CACHE:
            _CACHE[name] = _read(name) or dict(_FALLBACK.get(name, {}))
        return _CACHE[name]


def reload():
    """Drop the cache. Useful when hand-editing the data files."""
    with _LOCK:
        _CACHE.clear()


# ---------------------------------------------------------------------------
# Typed accessors
#
# Each one guarantees the shape its caller expects, so the UI can iterate
# without defensive checks at every site.
# ---------------------------------------------------------------------------
def _list_of_dicts(value, required_keys=()):
    if not isinstance(value, list):
        return []
    return [
        item for item in value
        if isinstance(item, dict) and all(item.get(key) for key in required_keys)
    ]


def _fallback_content(key):
    return _FALLBACK["content"].get(key)


def story():
    data = load("content").get("story")
    if not isinstance(data, dict):
        return _fallback_content("story")
    return {
        "title": str(data.get("title") or "Witchcraft & Wizardry"),
        "tagline": str(data.get("tagline") or ""),
        "paragraphs": [str(p) for p in data.get("paragraphs") or [] if str(p).strip()],
        "facts": _list_of_dicts(data.get("facts"), ("label", "value")),
    }


def onboarding_steps():
    data = load("content").get("onboarding")
    steps = _list_of_dicts((data or {}).get("steps"), ("id", "title")) if isinstance(data, dict) else []
    return steps


def account_modes():
    return _list_of_dicts(load("content").get("account_modes"), ("id", "title"))


def getting_started():
    return _list_of_dicts(load("content").get("getting_started"), ("title", "body"))


def faq():
    return _list_of_dicts(load("content").get("faq"), ("question", "answer"))


def troubleshooting():
    entries = _list_of_dicts(load("content").get("troubleshooting"), ("id", "symptom"))
    for entry in entries:
        entry.setdefault("cause", "")
        if not isinstance(entry.get("steps"), list):
            entry["steps"] = []
    return entries


def troubleshooting_by_id(entry_id):
    for entry in troubleshooting():
        if entry.get("id") == entry_id:
            return entry
    return None


def idle_messages():
    messages = [str(m) for m in load("content").get("idle_messages") or [] if str(m).strip()]
    return messages or _fallback_content("idle_messages")


def incantations():
    values = [str(m) for m in load("content").get("incantations") or [] if str(m).strip()]
    return values or _fallback_content("incantations")


def launch_steps():
    """``[(key, label, weight), ...]`` in launch order.

    The weights are what stop the bar from sitting still: "Preparing the
    castle" is a multi-minute download on a first run while "Opening the
    portal" is under a second, and equal slices made the bar look frozen
    through the long ones and jump through the short ones.
    """
    raw = load("content").get("launch_steps")
    steps = _list_of_dicts(raw, ("key", "label"))
    if not steps:
        steps = _fallback_content("launch_steps")

    result = []
    for step in steps:
        try:
            weight = float(step.get("weight", 1))
        except (TypeError, ValueError):
            weight = 1.0
        result.append((str(step["key"]), str(step["label"]), max(0.1, weight)))
    return result


def releases():
    data = load("changelog").get("releases")
    return _list_of_dicts(data, ("version",))


def release_notes_since(current_version, limit=3):
    """Changelog entries strictly newer than ``current_version``.

    Used by the update dialog so "an update is available" can say what is
    actually in it, rather than asking the player to take it on faith.
    """
    from .updater import is_newer

    newer = [r for r in releases() if is_newer(r.get("version"), current_version)]
    return newer[:limit]


def release_for(version):
    for entry in releases():
        if str(entry.get("version")) == str(version):
            return entry
    return None


def theme():
    return load("theme")
