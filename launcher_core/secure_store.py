"""Encrypted storage for the launcher's secrets.

Three things need protecting, and they are protected differently because
they have different threat models:

===================  =========================================  ==============
Item                 Where it lives                             Protection
===================  =========================================  ==============
Azure client id      ``secrets.dat`` in the data root            DPAPI-encrypted
Microsoft tokens     Windows Credential Manager (keyring)        DPAPI (OS)
  (fallback)         ``session.dat`` in the data root            DPAPI-encrypted
Cached profile       ``account.dat`` in the data root            DPAPI-encrypted
===================  =========================================  ==============

Everything on disk goes through :mod:`launcher_core.crypto`, so the files
are unreadable from another Windows account and unreadable after being
copied to another machine.

On first run this module also **migrates and destroys** the old plaintext
artifacts (``.env`` holding the client id, ``account_cache.json`` holding the
profile, and any refresh token that an older build wrote into that JSON), so
upgrading users do not keep a readable copy lying around.
"""

import json
import os
import time

from . import crypto, provisioning
from .paths import get_app_root, get_data_root, is_frozen

try:
    import keyring
except ImportError:
    keyring = None

# Populated at build time by tools/embed_secret.py. Empty in the source tree
# so the repository never contains the id, obfuscated in the shipped exe so
# `strings` on the binary does not reveal it. See crypto.obfuscate().
EMBEDDED_CLIENT_ID = ""

KEYRING_SERVICE = "WizardLauncher"
KEYRING_REFRESH_KEY = "ms_refresh_token"
KEYRING_ACCESS_KEY = "mc_access_token"

_SECRETS_FILE = "secrets.dat"
_SESSION_FILE = "session.dat"
_ACCOUNT_FILE = "account.dat"

_LEGACY_ENV_FILE = ".env"
_LEGACY_ACCOUNT_FILES = (
    os.path.join("resources", "account_cache.json"),
    "account_cache.json",
)


def _path(filename):
    return os.path.join(get_data_root(), filename)


class _EncryptedJson:
    """A JSON dict that is only ever written to disk encrypted."""

    def __init__(self, filename, log=None):
        self.path = _path(filename)
        self.log = log or (lambda msg: None)

    def load(self) -> dict:
        if not os.path.isfile(self.path):
            return {}
        try:
            return json.loads(crypto.read_encrypted(self.path).decode("utf-8"))
        except crypto.CryptoError as e:
            # Wrong user / wrong machine / tampered. Do not crash the
            # launcher over it - just start clean and say so once.
            self.log(f"Could not decrypt {os.path.basename(self.path)}: {e}")
            return {}
        except Exception as e:
            self.log(f"Could not read {os.path.basename(self.path)}: {e}")
            return {}

    def save(self, data: dict):
        try:
            crypto.write_encrypted(self.path, json.dumps(data).encode("utf-8"))
            return True
        except Exception as e:
            self.log(f"Could not write {os.path.basename(self.path)}: {e}")
            return False

    def clear(self):
        crypto.secure_delete(self.path)


# ---------------------------------------------------------------------------
# Azure client id
# ---------------------------------------------------------------------------
class ClientIdStore:
    """Resolves the Azure AD client id, in priority order:

    1. ``MC_LAUNCHER_CLIENT_ID`` / a ``.env`` next to ``launcher.py`` -
       **source checkouts only** (see :meth:`_developer_override`)
    2. the encrypted ``secrets.dat``, which is also where a value fetched
       from the signed remote manifest is cached
    3. the value embedded (obfuscated) at build time
    4. a legacy plaintext ``.env`` in the data folder - migrated into (2)
       and securely deleted

    Whatever the source, the value must be a well-formed GUID. A malformed
    one is dropped with a log line rather than sent to Microsoft, because
    "sign-in is not set up" is a message a player can act on and an opaque
    ``invalid_client`` from Azure is not.
    """

    def __init__(self, log=None):
        self.log = log or (lambda msg: None)
        self._store = _EncryptedJson(_SECRETS_FILE, log)
        self._cached = None

    def get(self) -> str:
        if self._cached is not None:
            return self._cached
        self._cached = self._resolve()
        return self._cached

    @staticmethod
    def _valid(value):
        value = (value or "").strip()
        return value if provisioning.GUID_RE.match(value) else ""

    def _developer_override(self) -> str:
        """The environment variable and the checkout ``.env``.

        **Both are ignored in a shipped build**, and that gating is the point
        rather than tidiness. The client id decides which Microsoft app
        registration the consent screen names. If a packaged launcher honoured
        an environment variable, then anything able to set one in the player's
        session - a shortcut with a modified environment, a batch file from a
        forum post, ordinary malware - could point the sign-in at an attacker's
        registration while the window still said Wizard Launcher. Restricting
        it to source checkouts keeps the convenience for whoever is developing
        the launcher and removes the attack surface for everybody else.
        """
        if is_frozen():
            return ""

        env_value = self._valid(os.environ.get("MC_LAUNCHER_CLIENT_ID"))
        if env_value:
            return env_value
        return self._valid(self._parse_env_file(
            os.path.join(get_app_root(), _LEGACY_ENV_FILE)))

    def _resolve(self) -> str:
        # Always run the migration first, whatever ends up being used. A
        # plaintext .env sitting in the data folder must be shredded even
        # when a developer override takes priority over its contents -
        # otherwise the very file this release exists to remove survives.
        migrated = self._migrate_legacy_env()

        override = self._developer_override()
        if override:
            self.log("Using the developer client id override (source checkout).")
            return override

        if is_frozen() and (os.environ.get("MC_LAUNCHER_CLIENT_ID") or "").strip():
            self.log("Ignoring MC_LAUNCHER_CLIENT_ID: a packaged build never takes "
                     "its Microsoft app id from the environment.", )

        stored = self._valid(self._store.load().get("client_id"))
        if stored:
            return stored

        if migrated:
            return migrated

        if EMBEDDED_CLIENT_ID:
            embedded = self._valid(crypto.deobfuscate(EMBEDDED_CLIENT_ID))
            if embedded:
                # Cache it encrypted so later starts skip the deobfuscation.
                self.set(embedded)
                return embedded

        return ""

    def set(self, client_id: str, source="local"):
        client_id = (client_id or "").strip()
        data = self._store.load()
        data["client_id"] = client_id
        data["source"] = source
        data["updated_at"] = int(time.time())
        self._store.save(data)
        self._cached = client_id

    # ------------------------------------------------------------------
    # Remote provisioning
    # ------------------------------------------------------------------
    def refresh_from_manifest(self, force=False) -> bool:
        """Consult the signed remote manifest. Returns True if the id changed.

        Called on a background thread at startup. Never raises, never blocks
        a launch, and never overrides a developer's checkout override - a
        remote file has no business winning over the value someone is
        actively testing with.

        The reason this exists is rotation, not confidentiality: see the
        module docstring of :mod:`launcher_core.provisioning`.
        """
        if not provisioning.is_enabled() or self._developer_override():
            return False

        data = self._store.load()
        if not force:
            age_hours = (time.time() - int(data.get("provisioned_at") or 0)) / 3600.0
            if age_hours < provisioning.refresh_hours():
                return False

        manifest = provisioning.fetch_manifest(self.log)
        if manifest is None:
            return False

        current = self._valid(data.get("client_id"))
        new_id = manifest["client_id"]
        revoked = manifest["revoked"]

        changed = False
        if current and current.lower() in revoked and new_id and new_id != current:
            self.log("The Microsoft app registration this build shipped with has been "
                     "retired; switching to the current one.")
            changed = True
        elif new_id and new_id != current:
            self.log("Picked up an updated Microsoft app registration.")
            changed = True

        if changed:
            self.set(new_id, source="manifest")

        # Record the check even when nothing changed, so a reachable server
        # is not re-polled on every single start.
        data = self._store.load()
        data["provisioned_at"] = manifest["fetched_at"]
        if manifest["message"]:
            data["manifest_message"] = manifest["message"]
        self._store.save(data)

        return changed

    @staticmethod
    def _parse_env_file(env_path) -> str:
        if not os.path.isfile(env_path):
            return ""
        try:
            with open(env_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#") or not line.startswith("MC_LAUNCHER_CLIENT_ID="):
                        continue
                    value = line.split("=", 1)[1].strip().strip('"').strip("'")
                    # Ignore the placeholder shipped in .env.example.
                    return "" if value.startswith("0000") else value
        except Exception:
            return ""
        return ""

    def _migrate_legacy_env(self) -> str:
        """Read a pre-1.1 plaintext .env, re-store the id encrypted, then
        shred the .env. Silent no-op when there is nothing to migrate."""
        env_path = _path(_LEGACY_ENV_FILE)
        if not os.path.isfile(env_path):
            return ""

        value = self._parse_env_file(env_path)
        if not value:
            return ""

        self.set(value)
        crypto.secure_delete(env_path)
        self.log("Migrated the Azure client id into encrypted storage and removed the plaintext .env.")
        return value


# ---------------------------------------------------------------------------
# Microsoft / Minecraft session tokens
# ---------------------------------------------------------------------------
class TokenStore:
    """Keyring first (Windows Credential Manager, DPAPI-backed by the OS),
    with a DPAPI-encrypted file as fallback when keyring is unavailable -
    which happens on stripped-down systems and inside some sandboxes.

    Also remembers when the Minecraft access token expires, so a warm start
    can reuse it instead of walking the whole Xbox Live chain again.
    """

    def __init__(self, log=None):
        self.log = log or (lambda msg: None)
        self._file = _EncryptedJson(_SESSION_FILE, log)
        self._keyring_ok = keyring is not None

    def _keyring_set(self, key, value):
        if not self._keyring_ok:
            return False
        try:
            keyring.set_password(KEYRING_SERVICE, key, value or "")
            return True
        except Exception as e:
            self.log(f"Credential Manager unavailable ({e}); using encrypted file instead.")
            self._keyring_ok = False
            return False

    def _keyring_get(self, key):
        if not self._keyring_ok:
            return None
        try:
            return keyring.get_password(KEYRING_SERVICE, key)
        except Exception as e:
            self.log(f"Could not read from Credential Manager ({e}); trying encrypted file.")
            self._keyring_ok = False
            return None

    def save(self, refresh_token, access_token, expires_in=None):
        stored_in_keyring = (
            self._keyring_set(KEYRING_REFRESH_KEY, refresh_token)
            and self._keyring_set(KEYRING_ACCESS_KEY, access_token)
        )

        data = {"access_expires_at": int(time.time() + (expires_in or 0))}
        if not stored_in_keyring:
            data["refresh_token"] = refresh_token or ""
            data["access_token"] = access_token or ""
        self._file.save(data)

    def load(self):
        """Return ``(refresh_token, access_token, access_expires_at)``."""
        data = self._file.load()
        refresh = self._keyring_get(KEYRING_REFRESH_KEY) or data.get("refresh_token") or None
        access = self._keyring_get(KEYRING_ACCESS_KEY) or data.get("access_token") or None
        return refresh, access, data.get("access_expires_at", 0)

    def access_token_valid(self, skew=120):
        """True when the cached Minecraft access token still has time left.
        `skew` keeps a safety margin so we never hand the game a token that
        expires mid-handshake."""
        _refresh, access, expires_at = self.load()
        return bool(access) and time.time() + skew < expires_at

    def clear(self):
        if keyring is not None:
            for key in (KEYRING_REFRESH_KEY, KEYRING_ACCESS_KEY):
                try:
                    keyring.delete_password(KEYRING_SERVICE, key)
                except Exception:
                    pass
        self._file.clear()


# ---------------------------------------------------------------------------
# Cached (non-secret but personal) profile
# ---------------------------------------------------------------------------
class ProfileStore:
    """Username / UUID / skin list. Not a credential, but it is personal
    data and it identifies the player, so it gets encrypted too."""

    def __init__(self, log=None):
        self.log = log or (lambda msg: None)
        self._store = _EncryptedJson(_ACCOUNT_FILE, log)
        self._migrate_legacy()

    def load(self) -> dict:
        return self._store.load()

    def save(self, username, uuid, skins=None):
        self._store.save({
            "username": username,
            "uuid": uuid,
            "skins": skins or [],
            "saved_at": int(time.time()),
        })

    def clear(self):
        self._store.clear()

    def _migrate_legacy(self):
        """Fold a pre-1.1 plaintext account_cache.json into the encrypted
        store, then shred it. Older builds also wrote the refresh token into
        that file, so this is the one that actually matters."""
        for rel in _LEGACY_ACCOUNT_FILES:
            legacy = _path(rel)
            if not os.path.isfile(legacy):
                continue
            try:
                with open(legacy, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                crypto.secure_delete(legacy)
                continue

            if data.get("username"):
                self.save(data.get("username"), data.get("uuid"), data.get("skins", []))

            leaked_token = data.get("ms_refresh_token")
            if leaked_token:
                TokenStore(self.log).save(leaked_token, data.get("mc_access_token"), 0)
                self.log("Moved a refresh token out of plaintext account_cache.json into encrypted storage.")

            crypto.secure_delete(legacy)


def wipe_all(log=None):
    """Full sign-out: remove every stored secret and personal file."""
    TokenStore(log).clear()
    ProfileStore(log).clear()
