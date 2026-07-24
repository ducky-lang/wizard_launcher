"""Lightweight update check.

Deliberately conservative: it only ever *tells* the user a new version
exists and opens the download page in their browser. It never downloads or
runs an installer by itself - a launcher that silently fetches and executes
a binary is exactly the mechanism a supply-chain attacker wants, and the
convenience is not worth handing it to them.

Disabled by default: with :data:`version.UPDATE_MANIFEST_URL` empty, every
call returns ``None`` without touching the network, so a user without a
manifest never sees a failed request.

Manifest format (JSON, served over HTTPS)::

    {
      "version": "1.2.0",
      "url": "https://github.com/<you>/wizard-launcher/releases/latest",
      "notes": "What changed in this release",
      "mandatory": false
    }
"""

import re
from urllib.parse import urlparse

import requests

from .version import VERSION, UPDATE_MANIFEST_URL, USER_AGENT

REQUEST_TIMEOUT = 6
ALLOWED_UPDATE_SCHEMES = {"https"}


def _parse_version(text):
    """Turn "1.2.3" into (1, 2, 3). Unparseable input sorts lowest so a
    malformed manifest can never look newer than what is installed."""
    parts = re.findall(r"\d+", str(text or ""))[:4]
    return tuple(int(p) for p in parts) if parts else (0,)


def is_newer(candidate, current=VERSION):
    return _parse_version(candidate) > _parse_version(current)


def check_for_update(log=None):
    """Return ``{"version", "url", "notes", "mandatory"}`` or None.

    Never raises: an update check failing is not a reason to interrupt
    somebody who just wants to play.
    """
    log = log or (lambda msg: None)
    if not UPDATE_MANIFEST_URL:
        return None

    try:
        resp = requests.get(
            UPDATE_MANIFEST_URL,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log(f"Update check skipped: {e}")
        return None

    if not isinstance(data, dict):
        return None

    latest = str(data.get("version") or "").strip()
    if not latest or not is_newer(latest):
        return None

    url = str(data.get("url") or "").strip()
    # The manifest is remote content, so the link inside it is untrusted.
    # Refuse anything that is not plain HTTPS before it can reach
    # page.launch_url() - that call would otherwise happily hand a
    # file:// or a custom scheme to the OS.
    if urlparse(url).scheme not in ALLOWED_UPDATE_SCHEMES:
        log(f"Update manifest offered a non-HTTPS download link; ignoring it.")
        return None

    return {
        "version": latest,
        "url": url,
        "notes": str(data.get("notes") or "")[:1000],
        "mandatory": bool(data.get("mandatory")),
    }
