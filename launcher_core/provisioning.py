"""Fetching the Azure application id from a signed remote manifest.

Read this first, because the threat model is not the obvious one.

**An Azure public-client application id is not a secret.** The launcher uses
the OAuth device code flow, which is a *public client* flow: Microsoft's own
documentation says such clients cannot hold secrets, and the id is
transmitted in plaintext in every token request, appears in the sign-in URL,
and is visible to anyone who points a proxy at the app for thirty seconds.
No amount of encryption, key vaulting or server-side fetching changes that -
the program has to present the value to Microsoft, so the program has to be
able to produce it.

So what is the point of this module? Two things that are real:

1. **Rotation without a re-release.** If the app registration has to change -
   quota abuse, a new tenant, a mistake in the redirect configuration -
   today that means rebuilding, re-signing, re-uploading an installer and
   persuading every player to reinstall. With a manifest it is a one-line
   edit to a JSON file, picked up on the next start.
2. **Revocation.** A build whose id is being abused can be pointed at a new
   registration, or told to stop using the old one, without touching the
   binary.

And one thing that is *not* real, stated so nobody relies on it: this does
not make the id confidential. It is cached on disk (encrypted, but on the
player's own machine, so decryptable by that player) and sent over the wire
to Microsoft in the clear.

Because the manifest can change which Microsoft app registration the sign-in
consent screen names, it is an injection target: an attacker who could serve
a manifest could point players at *their* registration, and the consent
screen would then ask for permissions on behalf of an app the player did not
choose. So the fetch is defended accordingly:

* **HTTPS only**, host allow-listed by registrable domain, redirects
  re-checked at every hop.
* **Detached Ed25519 signature over the exact bytes received.** No
  canonicalisation, no re-serialisation - the signature covers the literal
  response body, which removes a whole class of parser-differential bugs.
* **Fail closed.** A configured public key with no valid signature means the
  manifest is discarded, and the build-time value keeps being used. An
  unreachable server is not an error; it just means nothing changed.
* **Shape validation.** Only a well-formed GUID is ever accepted, whatever
  the manifest says.

Signature verification needs ``cryptography``. If it is not installed and a
public key *is* configured, verification fails and the manifest is refused -
never silently accepted.
"""

import base64
import json
import re
import time
from urllib.parse import urlparse

from .content import load as _load_data
from .version import USER_AGENT

try:
    import requests
except ImportError:  # pragma: no cover - requests is a hard dependency
    requests = None

GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

REQUEST_TIMEOUT = (6, 10)
MAX_MANIFEST_BYTES = 64 * 1024


def _config():
    catalog = _load_data("catalog")
    section = catalog.get("provisioning")
    return section if isinstance(section, dict) else {}


def is_enabled():
    return bool(str(_config().get("manifest_url") or "").strip())


def refresh_hours():
    try:
        return max(1.0, float(_config().get("refresh_hours", 24)))
    except (TypeError, ValueError):
        return 24.0


def _allowed_domains():
    raw = _config().get("allowed_domains")
    domains = {
        str(d).lower().strip().lstrip(".")
        for d in raw or []
        if isinstance(d, str) and d.strip()
    }
    return domains


def _host_allowed(url):
    """https, on a domain the catalog names. Suffix match, so the leading dot
    is what stops ``evil-githubusercontent.com`` from qualifying."""
    parsed = urlparse(url or "")
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        return False
    domains = _allowed_domains()
    if not domains:
        return False   # no allow-list configured means nothing is allowed
    return any(host == d or host.endswith("." + d) for d in domains)


def _fetch(url, log):
    if requests is None:
        return None
    if not _host_allowed(url):
        log(f"Provisioning: refusing a manifest URL on an unexpected host: {url}")
        return None
    try:
        with requests.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True,
                          headers={"User-Agent": USER_AGENT}) as resp:
            for hop in list(resp.history) + [resp]:
                if not _host_allowed(hop.url):
                    log("Provisioning: a redirect left the allowed hosts; discarding.")
                    return None
            resp.raise_for_status()
            body = resp.content
    except Exception as e:
        # Offline, blocked, 404 - all the same answer: nothing changed today.
        log(f"Provisioning check skipped: {e}")
        return None

    if len(body) > MAX_MANIFEST_BYTES:
        log("Provisioning: the manifest is implausibly large; discarding.")
        return None
    return body


def _verify_signature(body, signature_b64, public_key_b64, log):
    """True only when the signature genuinely verifies.

    Every failure path returns False, including "the library is missing" -
    an unverifiable manifest must never be treated as a verified one.
    """
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:
        log("Provisioning: a public key is configured but the 'cryptography' package "
            "is not installed, so the manifest cannot be verified. Ignoring it.")
        return False

    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
        key.verify(base64.b64decode(signature_b64), body)
        return True
    except InvalidSignature:
        log("Provisioning: the manifest signature does not match. Ignoring it.")
        return False
    except Exception as e:
        log(f"Provisioning: signature check failed ({e}). Ignoring the manifest.")
        return False


def fetch_manifest(log=None):
    """Return a validated manifest dict, or ``None``.

    ``None`` covers every uninteresting case - feature switched off, offline,
    unsigned, malformed - because the caller's response is identical in all
    of them: keep using what you already have.
    """
    log = log or (lambda message: None)
    config = _config()
    manifest_url = str(config.get("manifest_url") or "").strip()
    if not manifest_url:
        return None

    body = _fetch(manifest_url, log)
    if body is None:
        return None

    public_key = str(config.get("public_key") or "").strip()
    if public_key:
        signature_url = str(config.get("signature_url") or "").strip() \
            or manifest_url + ".sig"
        signature = _fetch(signature_url, log)
        if signature is None:
            log("Provisioning: no signature available; ignoring the manifest.")
            return None
        if not _verify_signature(body, signature.decode("ascii", "ignore").strip(),
                                 public_key, log):
            return None
    else:
        # Allowed, but say so. Without a key the only thing standing between
        # a player and a swapped consent screen is TLS plus the host
        # allow-list - which is a real defence, but a weaker one.
        log("Provisioning: no public key configured; the manifest is trusted on "
            "TLS and the host allow-list alone.")

    try:
        data = json.loads(body.decode("utf-8"))
    except Exception as e:
        log(f"Provisioning: the manifest is not valid JSON ({e}).")
        return None
    if not isinstance(data, dict):
        return None

    client_id = str(data.get("client_id") or "").strip()
    if client_id and not GUID_RE.match(client_id):
        log("Provisioning: the manifest offered something that is not an "
            "application id. Ignoring it.")
        return None

    revoked = {
        str(value).strip().lower()
        for value in data.get("revoked") or []
        if isinstance(value, str) and GUID_RE.match(value.strip())
    }

    return {
        "client_id": client_id,
        "revoked": revoked,
        "message": str(data.get("message") or "")[:500],
        "fetched_at": int(time.time()),
    }
