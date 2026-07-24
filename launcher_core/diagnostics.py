"""Everything a bug report needs, in one file the player can attach.

"Send me your log" fails in practice. The log lives in a folder people
cannot find, it is one of four files, and the interesting part is usually in
whichever one they did not send. This module builds a single zip containing
the logs, the settings, the system check and the environment, and returns
its path so the UI can offer "Show me the file".

Three rules govern what goes in:

* **Nothing is sent anywhere.** The report is written to disk and the folder
  is opened. Uploading a user's machine details without asking is not a
  feature, and a launcher that silently phones home is a launcher nobody
  should trust with a Microsoft token.
* **Everything is redacted.** Log lines already pass through
  :func:`app_log.redact`; so does everything assembled here, as a second
  pass over content that was not written by the logger.
* **No account identifiers.** The username, UUID and any token are omitted
  entirely rather than redacted, because a report is meant to be posted in
  a public issue tracker.
"""

import json
import os
import platform
import sys
import time
import zipfile

from .app_log import redact
from .paths import get_data_root, get_legacy_data_root
from .platform_utils import IS_WINDOWS, platform_name
from .version import APP_NAME, EDITION, VERSION, VERSION_STRING

REPORT_PREFIX = "wizard-report"
MAX_KEEP = 5

# Log files worth including, in the order somebody reading the report wants
# them. Rotated backups come along because the interesting crash is often in
# the previous session.
_LOG_MEMBERS = ("crash.log", "launcher.log", "launcher.log.1", "launcher.log.2")


def _environment_lines():
    lines = [
        f"{APP_NAME} {VERSION_STRING}",
        f"Version: {VERSION} ({EDITION})",
        f"Platform: {platform_name()} - {platform.platform()}",
        f"Machine: {platform.machine()}",
        f"Python: {platform.python_version()}",
        f"Frozen: {getattr(sys, 'frozen', False) or 'NUITKA_ORIGINAL_ARGV0' in os.environ}",
        f"Data root: {get_data_root()}",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    legacy = get_legacy_data_root()
    if legacy and os.path.isdir(legacy):
        # If this still exists, the move out of Documents did not complete -
        # which is exactly the sort of thing a report should surface.
        lines.append(f"Legacy data root still present: {legacy}")
    if IS_WINDOWS:
        lines.append(f"Windows release: {platform.win32_ver()[0]} {platform.win32_ver()[1]}")
    return lines


def _settings_snapshot(settings):
    """Settings as JSON. Every value here is non-sensitive by construction -
    settings.json is the file that deliberately holds nothing secret."""
    if settings is None:
        return "{}"
    try:
        from .config import DEFAULTS
        data = {key: settings.get(key) for key in DEFAULTS}
        data["_derived"] = {
            "server_ram_mb": settings.server_ram_mb,
            "client_ram_mb": settings.client_ram_mb,
            "bind_address": settings.bind_address,
        }
        return json.dumps(data, indent=2)
    except Exception as e:
        return json.dumps({"error": f"could not read settings: {e}"}, indent=2)


def _system_check_text(java_manager):
    try:
        from . import system_check
        return system_check.as_text(
            system_check.run_all(java_manager=java_manager, include_network=False)
        )
    except Exception as e:
        return f"System check failed: {e}"


def _file_inventory(root, limit=400):
    """What is actually installed, and how big.

    Answers most "did the download finish?" and "is server.jar missing?"
    questions without another round trip to the player.
    """
    lines = []
    skip = {"logs", "minecraft"}   # huge, and their own files are attached
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.lower() not in skip]
        depth = dirpath[len(root):].count(os.sep)
        if depth > 4:
            dirnames[:] = []
            continue
        for filename in sorted(filenames):
            full = os.path.join(dirpath, filename)
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            lines.append(f"{os.path.relpath(full, root)}\t{size}")
            if len(lines) >= limit:
                lines.append(f"... truncated at {limit} entries")
                return lines
    return lines


def _prune_old_reports(log_dir):
    try:
        reports = sorted(
            (f for f in os.listdir(log_dir)
             if f.startswith(REPORT_PREFIX) and f.endswith(".zip")),
        )
    except OSError:
        return
    for stale in reports[:-MAX_KEEP] if len(reports) > MAX_KEEP else []:
        try:
            os.remove(os.path.join(log_dir, stale))
        except OSError:
            pass


def build_report(logger, settings=None, java_manager=None, extra=None):
    """Write a diagnostic zip and return its path, or "" on failure.

    ``extra`` is an optional ``{filename: text}`` mapping - the error dialog
    passes the traceback and the step that failed, so the report explains
    itself without the player having to.
    """
    log_dir = logger.dir
    data_root = get_data_root()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = os.path.join(log_dir, f"{REPORT_PREFIX}-{VERSION}-{stamp}.zip")

    sections = {
        "environment.txt": "\n".join(_environment_lines()),
        "system-check.txt": _system_check_text(java_manager),
        "settings.json": _settings_snapshot(settings),
        "installed-files.txt": "\n".join(_file_inventory(data_root)),
        "README.txt": (
            "This report was generated by Wizard Launcher for troubleshooting.\n\n"
            "It contains log files, your settings and a list of installed game files.\n"
            "Access tokens, passwords and your account name are NOT included - log\n"
            "lines are scrubbed of credentials before they are ever written.\n\n"
            "Attach this file to a GitHub issue. Nothing was uploaded automatically.\n"
        ),
    }
    for name, text in (extra or {}).items():
        sections[str(name)] = str(text)

    try:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, text in sections.items():
                zf.writestr(name, redact(text))
            for member in _LOG_MEMBERS:
                source = os.path.join(log_dir, member)
                if os.path.isfile(source):
                    try:
                        with open(source, "r", encoding="utf-8", errors="replace") as f:
                            zf.writestr(f"logs/{member}", redact(f.read()))
                    except OSError:
                        continue
    except Exception as e:
        logger.write(f"Could not build the diagnostic report: {e}", level="ERROR")
        return ""

    _prune_old_reports(log_dir)
    logger.write(f"Diagnostic report written to {path}")
    return path


def issue_body(step=None, error=None, java_manager=None):
    """A GitHub issue body with the boring parts already filled in.

    Short on purpose: it goes in a URL, and browsers and GitHub both have
    limits. The full detail belongs in the attached report.
    """
    lines = [
        "**What happened**",
        "",
        "<!-- What were you doing, and what did you expect instead? -->",
        "",
        "**Details**",
        "",
        "```",
    ]
    lines += _environment_lines()
    if step:
        lines.append(f"Failed at: {step}")
    if error:
        lines.append(f"Error: {str(error).splitlines()[0][:200]}")
    lines.append("```")
    lines += [
        "",
        "**System check**",
        "",
        "```",
        _system_check_text(java_manager),
        "```",
        "",
        "<!-- Please attach the diagnostic report zip from the launcher's log folder. -->",
    ]
    return redact("\n".join(lines))


def issue_url(base_url, title, body, max_length=6000):
    """Build a prefilled 'new issue' link, truncating to something a browser
    will actually accept."""
    from urllib.parse import quote

    if not base_url:
        return ""
    encoded_title = quote(title[:120], safe="")
    prefix = f"{base_url}?title={encoded_title}&body="

    # Trim the *raw* body and re-encode, never the encoded string: cutting
    # percent-encoding mid-escape produces a URL the browser rejects outright.
    text = body
    while text:
        candidate = prefix + quote(text, safe="")
        if len(candidate) <= max_length:
            return candidate
        text = text[: int(len(text) * 0.8)]
    return prefix
