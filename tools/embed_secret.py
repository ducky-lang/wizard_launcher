"""Bake the Azure client id into the source tree, obfuscated, just before a build.

Run this immediately before compiling, and run --clean immediately after, so
the plain value never sits in the working tree any longer than the build takes.

    python tools/embed_secret.py --client-id 11111111-2222-3333-4444-555555555555
    python -m PyInstaller ... launcher.py
    python tools/embed_secret.py --clean

Honest limitation, stated plainly: this is obfuscation, not encryption.
Anything shipped inside a binary can be recovered by someone determined -
the value has to be present for the program to use it. What this buys you:

* the id is not in the git history or in any file you might share
* `strings WizardLauncher.exe` turns up nothing that looks like a GUID
* a casual decompile shows a base64 blob, not a credential

That is the correct security posture for this particular value. An Azure
*public client* id is not a secret in the OAuth sense - it is designed to be
visible in a redirect URL, and it cannot be used to impersonate anyone
without also passing Microsoft's own interactive sign-in. The reason to hide
it is quota abuse, not account compromise.

Never do this with a client *secret*. Public-client Device Code flow does not
use one, which is exactly why this launcher uses that flow.
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from launcher_core import crypto  # noqa: E402

TARGET = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "launcher_core", "secure_store.py",
)
PATTERN = re.compile(r'^EMBEDDED_CLIENT_ID = ".*"$', re.M)
GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def read_from_file(path):
    """First meaningful line of a file.

    Exists so the id never has to appear on a command line. ``--client-id
    <guid>`` is written into shell history and is visible in the process
    list to every other user on the machine for as long as this runs; a file
    you control the permissions on is not.
    """
    if not os.path.isfile(path):
        raise SystemExit(f"No such file: {path}")
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                # Tolerate a KEY=value line so a .env can be passed directly.
                return line.split("=", 1)[1].strip() if "=" in line else line
    raise SystemExit(f"{path} contains no client id.")


def write_value(encoded):
    with open(TARGET, "r", encoding="utf-8") as f:
        source = f.read()

    if not PATTERN.search(source):
        raise SystemExit(f"Could not find EMBEDDED_CLIENT_ID in {TARGET}")

    source = PATTERN.sub(f'EMBEDDED_CLIENT_ID = "{encoded}"', source)
    with open(TARGET, "w", encoding="utf-8") as f:
        f.write(source)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-id",
                        help="Azure AD application (client) id. Note: this lands in your "
                             "shell history and in the process list; prefer --from-file "
                             "or --stdin on a shared machine.")
    parser.add_argument("--from-file", metavar="PATH",
                        help="read the id from a file (first non-comment line)")
    parser.add_argument("--stdin", action="store_true",
                        help="read the id from standard input")
    parser.add_argument("--from-env", action="store_true",
                        help="read MC_LAUNCHER_CLIENT_ID from the environment instead")
    parser.add_argument("--clean", action="store_true",
                        help="blank the embedded value again (run after building)")
    args = parser.parse_args()

    if args.clean:
        write_value("")
        print("Cleared the embedded client id.")
        return

    client_id = args.client_id
    if args.from_file:
        client_id = read_from_file(args.from_file)
    elif args.stdin:
        client_id = sys.stdin.readline().strip()
    elif args.from_env:
        client_id = os.environ.get("MC_LAUNCHER_CLIENT_ID", "").strip()

    if not client_id:
        parser.error("pass --client-id <guid>, --from-file <path>, --stdin or --from-env")

    if not GUID_RE.match(client_id):
        parser.error(f"{client_id!r} does not look like an Azure application id (expected a GUID)")

    encoded = crypto.obfuscate(client_id)
    if crypto.deobfuscate(encoded) != client_id:
        raise SystemExit("Round-trip check failed; refusing to write a value the app cannot read back.")

    write_value(encoded)
    print(f"Embedded client id ({len(encoded)} chars, obfuscated).")
    print("Remember to run `python tools/embed_secret.py --clean` after the build.")


if __name__ == "__main__":
    main()
