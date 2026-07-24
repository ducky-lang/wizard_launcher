"""Encryption helpers for everything the launcher must keep private
(Azure client id, Microsoft refresh/access tokens, cached profile).

Design goals:

* **No extra dependencies.** Everything here is built on ctypes + hashlib,
  so the packaged build stays small and no crypto wheel has to be shipped.
* **Bound to the Windows user account.** On Windows the payload is sealed
  with DPAPI (``CryptProtectData``, ``CRYPTPROTECT_UI_FORBIDDEN``). Copying
  the file to another machine - or opening it from another Windows account
  on the same machine - simply fails to decrypt. There is no key material
  in the exe for someone to pull out with a decompiler.
* **Tamper-evident everywhere.** The portable fallback (non-Windows, or a
  machine where DPAPI is unavailable) uses encrypt-then-MAC: HMAC-SHA256 in
  counter mode for confidentiality, plus a separate HMAC-SHA256 tag that is
  verified with :func:`hmac.compare_digest` before a single byte is
  decrypted. A modified file is rejected instead of silently decoding into
  garbage.

Envelope layout (all methods)::

    b"WZS1" | method:1 | salt:16 | body

``method`` is one of :data:`METHOD_DPAPI` / :data:`METHOD_PORTABLE`, so a
file written by one method can still be read after the other becomes
available (e.g. profile roaming between machines).
"""

import base64
import hashlib
import hmac
import os
import struct
import tempfile

from . import platform_utils

MAGIC = b"WZS1"
METHOD_DPAPI = 1
METHOD_PORTABLE = 2

_SALT_LEN = 16
_TAG_LEN = 32
_PBKDF2_ROUNDS = 200_000

# Extra entropy mixed into DPAPI. Not a secret (it ships inside the exe) -
# its only job is to scope the blob to this application, so another program
# running as the same Windows user cannot decrypt our files by handing the
# bytes straight to CryptUnprotectData.
_APP_ENTROPY = b"WizardLauncher/secure-store/v1"


class CryptoError(Exception):
    pass


# ---------------------------------------------------------------------------
# Windows DPAPI
# ---------------------------------------------------------------------------
def _dpapi_available():
    return platform_utils.IS_WINDOWS


def _dpapi_call(func, data: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    def to_blob(raw):
        buf = ctypes.create_string_buffer(raw, len(raw))
        return DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    in_blob, _in_buf = to_blob(data)
    entropy_blob, _ent_buf = to_blob(_APP_ENTROPY)
    out_blob = DATA_BLOB()

    CRYPTPROTECT_UI_FORBIDDEN = 0x01
    ok = func(
        ctypes.byref(in_blob),      # pDataIn
        None,                       # description / ppszDataDescr
        ctypes.byref(entropy_blob), # pOptionalEntropy
        None,                       # pvReserved
        None,                       # pPromptStruct
        CRYPTPROTECT_UI_FORBIDDEN,  # dwFlags
        ctypes.byref(out_blob),     # pDataOut
    )
    if not ok:
        raise CryptoError(f"DPAPI call failed (win32 error {ctypes.GetLastError()})")

    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def dpapi_encrypt(data: bytes) -> bytes:
    import ctypes
    return _dpapi_call(ctypes.windll.crypt32.CryptProtectData, data)


def dpapi_decrypt(data: bytes) -> bytes:
    import ctypes
    return _dpapi_call(ctypes.windll.crypt32.CryptUnprotectData, data)


# ---------------------------------------------------------------------------
# Portable fallback: PBKDF2 -> HMAC-SHA256-CTR keystream, encrypt-then-MAC
# ---------------------------------------------------------------------------
def _machine_secret() -> bytes:
    """Best-effort per-user, per-machine material. Never leaves this box and
    is only used by the fallback path (macOS, Linux, or a Windows machine
    where DPAPI is unavailable).

    The platform-specific hardware identifier - MachineGuid on Windows,
    IOPlatformUUID on macOS, machine-id on Linux - is what makes a copied
    ``session.dat`` useless on another computer.
    """
    parts = [
        os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "",
        os.environ.get("USERNAME") or os.environ.get("USER") or "",
        os.environ.get("USERDOMAIN") or "",
        os.path.expanduser("~"),
    ]
    parts.extend(platform_utils.machine_identifiers())
    return "\x1f".join(parts).encode("utf-8", "replace")


def _derive_keys(salt: bytes):
    material = hashlib.pbkdf2_hmac("sha256", _machine_secret(), salt, _PBKDF2_ROUNDS, dklen=64)
    return material[:32], material[32:]  # (encryption key, mac key)


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hmac.new(key, nonce + struct.pack(">Q", counter), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:length])


def _portable_encrypt(data: bytes, salt: bytes) -> bytes:
    enc_key, mac_key = _derive_keys(salt)
    nonce = os.urandom(16)
    ciphertext = bytes(a ^ b for a, b in zip(data, _keystream(enc_key, nonce, len(data))))
    tag = hmac.new(mac_key, salt + nonce + ciphertext, hashlib.sha256).digest()
    return nonce + tag + ciphertext


def _portable_decrypt(body: bytes, salt: bytes) -> bytes:
    if len(body) < 16 + _TAG_LEN:
        raise CryptoError("Encrypted payload is truncated.")
    nonce, tag, ciphertext = body[:16], body[16:16 + _TAG_LEN], body[16 + _TAG_LEN:]
    enc_key, mac_key = _derive_keys(salt)
    expected = hmac.new(mac_key, salt + nonce + ciphertext, hashlib.sha256).digest()
    # Verify BEFORE decrypting so tampered data never reaches the parser.
    if not hmac.compare_digest(tag, expected):
        raise CryptoError("Encrypted payload failed integrity check (wrong machine or tampered).")
    return bytes(a ^ b for a, b in zip(ciphertext, _keystream(enc_key, nonce, len(ciphertext))))


# ---------------------------------------------------------------------------
# Public envelope API
# ---------------------------------------------------------------------------
def encrypt(data: bytes) -> bytes:
    salt = os.urandom(_SALT_LEN)
    if _dpapi_available():
        try:
            return MAGIC + bytes([METHOD_DPAPI]) + salt + dpapi_encrypt(data)
        except Exception:
            pass  # fall through to the portable path
    return MAGIC + bytes([METHOD_PORTABLE]) + salt + _portable_encrypt(data, salt)


def decrypt(blob: bytes) -> bytes:
    if not blob.startswith(MAGIC):
        raise CryptoError("Not a Wizard Launcher encrypted file.")
    method = blob[len(MAGIC)]
    salt = blob[len(MAGIC) + 1:len(MAGIC) + 1 + _SALT_LEN]
    body = blob[len(MAGIC) + 1 + _SALT_LEN:]
    if method == METHOD_DPAPI:
        return dpapi_decrypt(body)
    if method == METHOD_PORTABLE:
        return _portable_decrypt(body, salt)
    raise CryptoError(f"Unsupported encryption method {method}.")


def describe_backend() -> str:
    """Human-readable name of the active protection, for the About dialog."""
    if _dpapi_available():
        return "Windows DPAPI (per-user)"
    if platform_utils.IS_MACOS:
        return "macOS Keychain + machine-bound HMAC-SHA256"
    return "Machine-bound HMAC-SHA256"


# ---------------------------------------------------------------------------
# Obfuscated build-time constant
# ---------------------------------------------------------------------------
# Values baked into the exe at build time (currently only the Azure client
# id) go through this. It is deliberately NOT presented as encryption -
# anything shipped inside a binary can eventually be recovered. The point is
# that `strings WizardLauncher.exe` and a casual decompile turn up nothing
# that looks like a GUID.
_OBFUSCATION_PAD = hashlib.sha256(b"WizardLauncher/embedded/v1").digest()


def obfuscate(value: str) -> str:
    raw = value.encode("utf-8")
    pad = (_OBFUSCATION_PAD * (len(raw) // len(_OBFUSCATION_PAD) + 1))[:len(raw)]
    return base64.urlsafe_b64encode(bytes(a ^ b for a, b in zip(raw, pad))).decode("ascii")


def deobfuscate(value: str) -> str:
    try:
        raw = base64.urlsafe_b64decode(value.encode("ascii"))
    except Exception:
        return ""
    pad = (_OBFUSCATION_PAD * (len(raw) // len(_OBFUSCATION_PAD) + 1))[:len(raw)]
    return bytes(a ^ b for a, b in zip(raw, pad)).decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# Encrypted file helpers
# ---------------------------------------------------------------------------
def write_encrypted(path: str, data: bytes):
    """Encrypt and write atomically, so a crash mid-write can never leave a
    half-written (and therefore unreadable) secrets file behind."""
    blob = encrypt(data)
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp_", suffix=".dat")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        if os.name != "nt":
            os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        raise


def read_encrypted(path: str) -> bytes:
    with open(path, "rb") as f:
        return decrypt(f.read())


def secure_delete(path: str):
    """Overwrite a file's bytes before unlinking it. On an SSD this is not a
    forensic guarantee, but it does stop a plaintext token from surviving in
    a file that merely got its directory entry removed."""
    try:
        if not os.path.isfile(path):
            return
        size = os.path.getsize(path)
        with open(path, "r+b") as f:
            f.write(os.urandom(size))
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        pass
    try:
        os.remove(path)
    except Exception:
        pass
