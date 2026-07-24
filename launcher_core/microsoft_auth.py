"""Microsoft / Minecraft sign-in via the OAuth 2.0 Device Code flow.

Why Device Code: the launcher never sees, handles or stores the user's
password. Microsoft's own page collects it; we only ever receive tokens.

What is stored, and where:

* refresh token + Minecraft access token -> Windows Credential Manager
  (DPAPI, per-user), with a DPAPI-encrypted file as fallback
* username / UUID / skin -> DPAPI-encrypted ``account.dat``
* nothing at all in plaintext, and nothing in the log file (see
  :func:`launcher_core.app_log.redact`)

See :mod:`launcher_core.secure_store` for the storage layer.
"""

import threading
import time
import webbrowser

import requests

from .paths import get_data_root
from .secure_store import ClientIdStore, TokenStore, ProfileStore
from .version import USER_AGENT

SCOPE = "XboxLive.signin offline_access"

DEVICE_CODE_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
XBL_AUTH_URL = "https://user.auth.xboxlive.com/user/authenticate"
XSTS_AUTH_URL = "https://xsts.auth.xboxlive.com/xsts/authorize"
MC_LOGIN_URL = "https://api.minecraftservices.com/authentication/login_with_xbox"
MC_ENTITLEMENTS_URL = "https://api.minecraftservices.com/entitlements/mcstore"
MC_PROFILE_URL = "https://api.minecraftservices.com/minecraft/profile"

REQUEST_TIMEOUT = (10, 20)


class AuthError(Exception):
    pass


class MicrosoftAccount:
    def __init__(self, username, uuid, mc_access_token, ms_refresh_token,
                 skins=None, expires_in=0, xuid="", client_id=""):
        self.username = username
        self.uuid = uuid
        self.mc_access_token = mc_access_token
        self.ms_refresh_token = ms_refresh_token
        self.skins = skins or []
        self.expires_in = expires_in
        # Xbox user id and Azure application id. Neither is a secret, and
        # both belong on the game's command line - the launcher used to omit
        # them, leaving Minecraft with the unsubstituted "${auth_xuid}" and
        # "${clientid}" placeholders.
        self.xuid = xuid or ""
        self.client_id = client_id or ""

    def __repr__(self):
        # Never let a token reach a traceback or a log line.
        return f"<MicrosoftAccount {self.username} ({self.uuid})>"


def _session():
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return s


def _request(method, url, **kwargs):
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    try:
        with _session() as s:
            resp = s.request(method, url, **kwargs)
    except requests.exceptions.SSLError as e:
        raise AuthError(
            "The secure connection to Microsoft could not be verified.\n\n"
            "How to fix:\n"
            "1. Check your computer's date and time,\n"
            "2. Disable 'HTTPS scanning' in your antivirus, or\n"
            f"3. Try another network.\n\nDetails: {e}"
        )
    except requests.exceptions.Timeout:
        raise AuthError("Microsoft did not respond in time. Check your connection and try again.")
    except requests.RequestException as e:
        raise AuthError(f"Could not reach Microsoft's sign-in service.\n\nDetails: {e}")

    try:
        payload = resp.json()
    except ValueError:
        payload = {}

    if resp.status_code >= 400:
        err = payload.get("error") or payload.get("Error") or f"HTTP {resp.status_code}"
        desc = (
            payload.get("error_description")
            or payload.get("errorMessage")
            or payload.get("Message")
            or resp.text[:300]
        )
        return resp.status_code, payload, err, desc

    return resp.status_code, payload, None, None


def _post_json(url, data=None, json_body=None, headers=None):
    return _request("POST", url, data=data, json=json_body, headers=headers)


def _get_json(url, headers=None):
    return _request("GET", url, headers=headers)


class MicrosoftAuth:
    is_automatic = True

    def __init__(self, log=None, schedule_callback=None, use_embedded_window=False):
        self.log = log or (lambda msg: None)
        self._schedule_callback = schedule_callback
        self.use_embedded_window = use_embedded_window
        self._cancel = False

        self.client_ids = ClientIdStore(self.log)
        self.tokens = TokenStore(self.log)
        self.profiles = ProfileStore(self.log)

    # ------------------------------------------------------------------
    @property
    def client_id(self):
        return self.client_ids.get()

    @property
    def is_configured(self):
        return bool(self.client_id)

    def _require_client_id(self):
        if not self.client_id:
            raise AuthError(
                "Microsoft sign-in is not set up in this build of the launcher.\n\n"
                "You can still play as a guest - sign-in only controls your in-game name and skin.\n\n"
                "How to fix (for the launcher author):\n"
                "1. Run tools/embed_secret.py --from-file <path> before building, or\n"
                "2. Publish a signed provisioning manifest (tools/sign_manifest.py).\n"
                "   In a source checkout only, MC_LAUNCHER_CLIENT_ID or a .env next to\n"
                "   launcher.py also works; a packaged build ignores both on purpose.\n"
                f"   Data folder: {get_data_root()}"
            )
        return self.client_id

    def _emit(self, callback, *args):
        if self._schedule_callback:
            self._schedule_callback(callback, *args)
        else:
            callback(*args)

    # ------------------------------------------------------------------
    # Cached profile (instant UI, no network)
    # ------------------------------------------------------------------
    def peek_cached_profile(self):
        """Return the stored username/uuid without touching the network, so
        the account pill can render correctly on the very first frame."""
        data = self.profiles.load()
        return data if data.get("username") else None

    def load_cached_account(self):
        """Silently restore a session using the stored refresh token."""
        refresh_token, _access, _expires = self.tokens.load()
        if not refresh_token:
            return None
        if not self.client_id:
            return None
        return self.refresh_login(refresh_token)

    def _save(self, account):
        self.tokens.save(account.ms_refresh_token, account.mc_access_token, account.expires_in)
        self.profiles.save(account.username, account.uuid, account.skins)

    def logout(self):
        self._cancel = True
        self.tokens.clear()
        self.profiles.clear()

    def cancel(self):
        """Abort an in-flight device-code poll without wiping stored data."""
        self._cancel = True

    # ------------------------------------------------------------------
    # Device code flow
    # ------------------------------------------------------------------
    def _request_device_code(self):
        client_id = self._require_client_id()
        status, payload, err, desc = _post_json(DEVICE_CODE_URL, data={
            "client_id": client_id,
            "scope": SCOPE,
        })
        if err:
            self.log(f"Device code request failed ({status}): {err}")
            if err == "unauthorized_client":
                raise AuthError(
                    "This launcher's Microsoft app is not allowed to use Device Code sign-in.\n\n"
                    "How to fix (for the launcher author):\n"
                    "1. In portal.azure.com open the app registration,\n"
                    "2. Under Authentication, turn on 'Allow public client flows'."
                )
            raise AuthError(f"Could not start Microsoft sign-in.\n\nDetails: {desc}")
        return payload

    def _poll_for_token(self, device_code, interval, expires_in):
        client_id = self.client_id
        deadline = time.time() + expires_in
        while time.time() < deadline:
            if self._cancel:
                raise AuthError("Sign-in cancelled.")
            time.sleep(interval)
            if self._cancel:
                raise AuthError("Sign-in cancelled.")

            status, payload, err, desc = _post_json(TOKEN_URL, data={
                "client_id": client_id,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
            })
            if err is None:
                return payload
            if err == "authorization_pending":
                continue
            if err == "slow_down":
                interval += 5
                continue
            if err == "authorization_declined":
                raise AuthError("Sign-in was declined in the browser.")
            if err == "expired_token":
                raise AuthError("The sign-in code expired. Please try again.")
            self.log(f"Token poll failed ({status}): {err}")
            raise AuthError(f"Sign-in failed.\n\nDetails: {desc}")
        raise AuthError("Sign-in timed out. Please try again.")

    def _refresh_aad_token(self, refresh_token):
        client_id = self._require_client_id()
        status, payload, err, desc = _post_json(TOKEN_URL, data={
            "client_id": client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": SCOPE,
        })
        if err:
            raise AuthError(f"Your saved Microsoft session could not be renewed.\n\nDetails: {desc}")
        return payload

    # ------------------------------------------------------------------
    # Xbox Live -> Minecraft chain
    # ------------------------------------------------------------------
    def _authenticate_xbl(self, ms_access_token):
        status, payload, err, desc = _post_json(XBL_AUTH_URL, json_body={
            "Properties": {
                "AuthMethod": "RPS",
                "SiteName": "user.auth.xboxlive.com",
                "RpsTicket": f"d={ms_access_token}",
            },
            "RelyingParty": "http://auth.xboxlive.com",
            "TokenType": "JWT",
        })
        if err:
            raise AuthError(f"Xbox Live sign-in failed.\n\nDetails: {desc}")
        try:
            return payload["Token"], payload["DisplayClaims"]["xui"][0]["uhs"]
        except (KeyError, IndexError, TypeError):
            raise AuthError("Xbox Live returned an unexpected response. Please try again.")

    def _authenticate_xsts(self, xbl_token):
        status, payload, err, desc = _post_json(XSTS_AUTH_URL, json_body={
            "Properties": {"SandboxId": "RETAIL", "UserTokens": [xbl_token]},
            "RelyingParty": "rp://api.minecraftservices.com/",
            "TokenType": "JWT",
        })
        if err:
            xerr = payload.get("XErr")
            if xerr == 2148916233:
                raise AuthError(
                    "This Microsoft account does not have an Xbox profile yet.\n\n"
                    "How to fix:\n"
                    "1. Go to xbox.com, sign in and create a profile,\n"
                    "2. Come back and sign in again."
                )
            if xerr == 2148916238:
                raise AuthError(
                    "This account is registered to a child and needs to be added to a Family group.\n\n"
                    "How to fix:\n"
                    "1. Have an adult add this account to their Microsoft Family, then try again."
                )
            if xerr == 2148916235:
                raise AuthError("Xbox Live is not available in this account's region.")
            raise AuthError(f"Xbox Live authorisation failed.\n\nDetails: {desc}")
        try:
            token = payload["Token"]
        except (KeyError, TypeError):
            raise AuthError("Xbox Live returned an unexpected response. Please try again.")
        # xid is optional in the response; the launch works without it.
        xuid = ""
        try:
            xuid = payload["DisplayClaims"]["xui"][0].get("xid", "") or ""
        except (KeyError, IndexError, TypeError):
            pass
        return token, xuid

    def _authenticate_minecraft(self, xsts_token, user_hash):
        status, payload, err, desc = _post_json(MC_LOGIN_URL, json_body={
            "identityToken": f"XBL3.0 x={user_hash};{xsts_token}",
        })
        if err:
            raise AuthError(f"Minecraft sign-in failed.\n\nDetails: {desc}")
        return payload.get("access_token"), payload.get("expires_in", 0)

    def _check_ownership(self, mc_access_token):
        status, payload, err, desc = _get_json(
            MC_ENTITLEMENTS_URL, headers={"Authorization": f"Bearer {mc_access_token}"}
        )
        if err:
            raise AuthError(f"Could not check your Minecraft licence.\n\nDetails: {desc}")
        if not payload.get("items"):
            raise AuthError(
                "This Microsoft account does not own Minecraft: Java Edition.\n\n"
                "How to fix:\n"
                "1. Sign in with the account that owns the game, or\n"
                "2. Close this window and play as a guest instead."
            )

    def _fetch_profile(self, mc_access_token):
        status, payload, err, desc = _get_json(
            MC_PROFILE_URL, headers={"Authorization": f"Bearer {mc_access_token}"}
        )
        if err:
            if status == 404:
                raise AuthError(
                    "This account has not picked a Minecraft username yet.\n\n"
                    "How to fix:\n"
                    "1. Open the official Minecraft Launcher once and choose a name,\n"
                    "2. Then sign in here again."
                )
            raise AuthError(f"Could not load your Minecraft profile.\n\nDetails: {desc}")
        return payload

    def _complete_chain(self, ms_access_token, ms_refresh_token):
        xbl_token, user_hash = self._authenticate_xbl(ms_access_token)
        xsts_token, xuid = self._authenticate_xsts(xbl_token)
        mc_access_token, expires_in = self._authenticate_minecraft(xsts_token, user_hash)
        self._check_ownership(mc_access_token)
        profile = self._fetch_profile(mc_access_token)
        return MicrosoftAccount(
            username=profile.get("name"),
            uuid=profile.get("id"),
            mc_access_token=mc_access_token,
            ms_refresh_token=ms_refresh_token,
            skins=profile.get("skins", []),
            expires_in=expires_in,
            xuid=xuid,
            client_id=self.client_ids.get(),
        )

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------
    def login(self, on_url, on_success, on_error, timeout=300):
        self._cancel = False

        def _run():
            try:
                device_data = self._request_device_code()
                verification_uri = device_data.get("verification_uri", "https://microsoft.com/devicelogin")
                user_code = device_data["user_code"]
                device_code = device_data["device_code"]
                interval = device_data.get("interval", 5)
                expires_in = min(device_data.get("expires_in", timeout), timeout)

                self._emit(on_url, verification_uri, user_code)
                try:
                    webbrowser.open(verification_uri)
                except Exception:
                    pass

                # The code itself is short-lived and single-use, but it is
                # still a sign-in credential - keep it off disk.
                self.log("Waiting for you to finish signing in with Microsoft...")

                token_data = self._poll_for_token(device_code, interval, expires_in)
                account = self._complete_chain(
                    token_data["access_token"], token_data.get("refresh_token")
                )
                self._save(account)
                self.log(f"Signed in as {account.username}.")
                self._emit(on_success, account)

            except AuthError as e:
                self.log(f"Sign-in error: {e}")
                self._emit(on_error, str(e))
            except Exception as e:
                self.log(f"Unexpected sign-in error: {type(e).__name__}")
                self._emit(on_error, f"Something went wrong during sign-in.\n\nDetails: {e}")

        threading.Thread(target=_run, daemon=True, name="ms-login").start()

    def refresh_login(self, refresh_token):
        if not refresh_token:
            return None
        try:
            token_data = self._refresh_aad_token(refresh_token)
            account = self._complete_chain(
                token_data["access_token"], token_data.get("refresh_token", refresh_token)
            )
            self._save(account)
            return account
        except AuthError as e:
            self.log(f"Could not restore your saved session: {e}")
            return None
        except Exception as e:
            self.log(f"Could not restore your saved session: {type(e).__name__}")
            return None
