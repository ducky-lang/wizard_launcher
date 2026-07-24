"""Generate a signing key and sign the provisioning manifest.

The manifest lets the Azure application id be rotated without shipping a new
build. Because it can change which Microsoft app registration the consent
screen names, it is signed: the launcher refuses any manifest whose detached
signature does not verify against the public key baked into the binary.

    # once, on a machine you control
    python tools/sign_manifest.py --new-key

      -> writes provisioning-key.pem (PRIVATE - never commit this)
      -> prints the public key to paste into data/catalog.json

    # whenever the manifest changes
    python tools/sign_manifest.py --sign client-id.json

      -> writes client-id.json.sig next to it

Publish ``client-id.json`` and ``client-id.json.sig`` at the URLs named in
``catalog.json``. The signature is over the **exact bytes** of the file, so
re-serialising or re-formatting the JSON invalidates it - sign the file you
are actually going to upload.

Manifest format::

    {
      "client_id": "11111111-2222-3333-4444-555555555555",
      "revoked":   ["99999999-8888-7777-6666-555555555555"],
      "message":   "optional note, shown in the launcher log"
    }

Requires ``cryptography`` (``pip install cryptography``). The launcher itself
only needs it to *verify*; if you configure a public key, make sure the
package is in the build environment or verification fails closed and the
manifest is ignored.
"""

import argparse
import base64
import json
import os
import re
import stat
import sys

KEY_FILE = "provisioning-key.pem"
GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _require_cryptography():
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey, Ed25519PublicKey,
        )
        return serialization, Ed25519PrivateKey, Ed25519PublicKey
    except ImportError:
        raise SystemExit(
            "This tool needs the 'cryptography' package:\n"
            f"    {sys.executable} -m pip install cryptography"
        )


def new_key(path):
    serialization, Ed25519PrivateKey, _ = _require_cryptography()

    if os.path.exists(path):
        raise SystemExit(
            f"{path} already exists. Refusing to overwrite a signing key - "
            "every build that shipped its public key would stop trusting the manifest."
        )

    private_key = Ed25519PrivateKey.generate()
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    # Write with the permission bits set before any bytes land, so the key is
    # never briefly world-readable on a multi-user machine.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)

    public_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    public_b64 = base64.b64encode(public_raw).decode("ascii")

    print(f"Private key written to {path}")
    print("  Keep it off the network and out of git. Anyone holding it can")
    print("  redirect every installed launcher to a different Microsoft app.")
    print()
    print("Paste this into launcher_core/data/catalog.json under provisioning.public_key:")
    print()
    print(f'    "public_key": "{public_b64}"')
    print()


def load_private_key(path):
    serialization, _, _ = _require_cryptography()
    if not os.path.isfile(path):
        raise SystemExit(f"No signing key at {path}. Run --new-key first.")
    with open(path, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def validate_manifest(body):
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception as e:
        raise SystemExit(f"The manifest is not valid JSON: {e}")
    if not isinstance(data, dict):
        raise SystemExit("The manifest must be a JSON object.")

    client_id = str(data.get("client_id") or "").strip()
    if client_id and not GUID_RE.match(client_id):
        raise SystemExit(f"client_id {client_id!r} is not a GUID; the launcher would reject it.")
    for value in data.get("revoked") or []:
        if not GUID_RE.match(str(value).strip()):
            raise SystemExit(f"revoked entry {value!r} is not a GUID.")
    return data


def sign(manifest_path, key_path):
    private_key = load_private_key(key_path)

    with open(manifest_path, "rb") as f:
        body = f.read()

    data = validate_manifest(body)
    signature = private_key.sign(body)
    signature_path = manifest_path + ".sig"
    with open(signature_path, "w", encoding="ascii") as f:
        f.write(base64.b64encode(signature).decode("ascii") + "\n")

    print(f"Signed {manifest_path} ({len(body)} bytes)")
    print(f"  client_id: {data.get('client_id') or '(none)'}")
    if data.get("revoked"):
        print(f"  revoked:   {', '.join(data['revoked'])}")
    print(f"  signature: {signature_path}")
    print()
    print("Upload BOTH files. The signature covers the exact bytes, so do not")
    print("reformat the JSON afterwards - re-sign it instead.")


def verify(manifest_path, public_key_b64):
    _serialization, _, Ed25519PublicKey = _require_cryptography()
    from cryptography.exceptions import InvalidSignature

    with open(manifest_path, "rb") as f:
        body = f.read()
    with open(manifest_path + ".sig", "r", encoding="ascii") as f:
        signature = base64.b64decode(f.read().strip())

    key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
    try:
        key.verify(signature, body)
    except InvalidSignature:
        raise SystemExit("FAILED: the signature does not match this manifest.")
    print("OK: the launcher would accept this manifest.")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--new-key", action="store_true",
                        help="generate a signing key and print the public half")
    parser.add_argument("--sign", metavar="MANIFEST",
                        help="sign a manifest, writing MANIFEST.sig beside it")
    parser.add_argument("--verify", metavar="MANIFEST",
                        help="check a manifest against --public-key, as the launcher would")
    parser.add_argument("--public-key", help="base64 public key, for --verify")
    parser.add_argument("--key", default=KEY_FILE,
                        help=f"path to the private key (default: {KEY_FILE})")
    args = parser.parse_args()

    if args.new_key:
        new_key(args.key)
    elif args.sign:
        sign(args.sign, args.key)
    elif args.verify:
        if not args.public_key:
            parser.error("--verify needs --public-key")
        verify(args.verify, args.public_key)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
