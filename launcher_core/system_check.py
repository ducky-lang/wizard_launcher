"""Is this computer ready to run the castle?

Answering that *before* the first Play press is the difference between "the
launcher told me I needed 2 GB more space" and "it downloaded for eleven
minutes and then failed". Every check returns the same shape so the UI can
render a list without knowing what any individual check does:

    Check(key, title, status, detail, remedy, value)

``status`` is one of ``ok`` / ``warn`` / ``fail`` / ``pending``:

* ``ok``   - nothing to do.
* ``warn`` - it will work, but the player should know (low RAM, no Java yet
  but we can download one).
* ``fail`` - it will not work until something changes (no disk space).

Nothing here modifies the machine and nothing raises: a check that cannot
determine its answer reports ``warn`` with the reason, because "I could not
tell" is information too, and an exception thrown while reassuring somebody
that their PC is fine would be a poor joke.
"""

import os
import shutil
from collections import namedtuple

from .config import auto_client_ram_mb, auto_server_ram_mb, _total_ram_mb
from .constants import (
    DOWNLOAD_MB, JAVA_DOWNLOAD_URL, REQUIRED_DISK_MB, REQUIRED_JAVA_MAJOR,
    SERVER_JAVA_MAX, SERVER_JAVA_MIN,
)
from .paths import get_data_root
from .platform_utils import platform_name

OK, WARN, FAIL, PENDING = "ok", "warn", "fail", "pending"

Check = namedtuple("Check", "key title status detail remedy value")

# Below this the client and the server cannot both have a comfortable heap.
MIN_RAM_MB = 4096
COMFORTABLE_RAM_MB = 8192


def _mb(bytes_value):
    return int(bytes_value / (1024 * 1024))


def _gb_text(mb):
    return f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{mb} MB"


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------
def check_disk(data_root=None):
    """Free space where the world and the client will actually be written.

    Measured at the data root rather than at ``C:\\`` - the two are the same
    on most machines and very much not the same on the ones where this
    matters.
    """
    root = data_root or get_data_root()
    try:
        free_mb = _mb(shutil.disk_usage(root).free)
    except OSError as e:
        return Check("disk", "Disk space", WARN,
                     f"Could not measure free space on {root}.",
                     f"Details: {e}", None)

    detail = f"{_gb_text(free_mb)} free where the game is installed."
    if free_mb >= REQUIRED_DISK_MB:
        return Check("disk", "Disk space", OK, detail, "", free_mb)

    needed = REQUIRED_DISK_MB - free_mb
    if free_mb >= DOWNLOAD_MB:
        return Check(
            "disk", "Disk space", WARN,
            detail + f" About {_gb_text(REQUIRED_DISK_MB)} is recommended.",
            f"You can start, but free up around {_gb_text(needed)} before the first launch "
            "finishes installing the client.",
            free_mb,
        )
    return Check(
        "disk", "Disk space", FAIL,
        detail + f" About {_gb_text(REQUIRED_DISK_MB)} is needed.",
        f"Free up at least {_gb_text(needed)} on this drive, then press Play. "
        f"The castle alone is around {_gb_text(DOWNLOAD_MB)}.",
        free_mb,
    )


def check_memory():
    total_mb = _total_ram_mb()
    detail = (f"{_gb_text(total_mb)} of RAM. The launcher will give the world "
              f"{auto_server_ram_mb()} MB and Minecraft {auto_client_ram_mb()} MB.")

    if total_mb >= COMFORTABLE_RAM_MB:
        return Check("memory", "Memory", OK, detail, "", total_mb)
    if total_mb >= MIN_RAM_MB:
        return Check(
            "memory", "Memory", WARN, detail,
            "This is enough to play, but close browsers and other heavy programs "
            "before you start. Turning off background animations in Settings helps too.",
            total_mb,
        )
    return Check(
        "memory", "Memory", FAIL, detail,
        f"Running a world server and Minecraft together needs about {_gb_text(MIN_RAM_MB)}. "
        "It may start, but expect heavy stuttering.",
        total_mb,
    )


def check_java(java_manager=None):
    """What Java is already here.

    Deliberately never *installs* anything - the first-run guide must not
    kick off a 200 MB download while the player is reading a screen. It only
    reports, and says plainly that the launcher will fetch a runtime itself
    if there is nothing usable.
    """
    if java_manager is None:
        return Check("java", "Java", PENDING, "Not checked yet.", "", None)

    try:
        scored = java_manager._get_scored()  # noqa: SLF001 - same package, one caller
    except Exception as e:
        return Check("java", "Java", WARN, "Could not scan for Java.",
                     f"Details: {e}", None)

    majors = sorted({major for major, _ in scored}, reverse=True)
    modern = [m for m in majors if m >= REQUIRED_JAVA_MAJOR]
    legacy = [m for m in majors if SERVER_JAVA_MIN <= m <= SERVER_JAVA_MAX]

    if not majors:
        return Check(
            "java", "Java", WARN, "No Java installed.",
            "The launcher will download an official runtime from Mojang on the first "
            f"launch - nothing for you to do. To skip that, install Java "
            f"{REQUIRED_JAVA_MAJOR} from {JAVA_DOWNLOAD_URL}.",
            [],
        )

    found = "Java " + ", ".join(str(m) for m in majors) + " found."
    if modern and legacy:
        return Check("java", "Java", OK, found, "", majors)
    if modern:
        return Check(
            "java", "Java", OK,
            found + f" No Java {SERVER_JAVA_MIN}-{SERVER_JAVA_MAX} for the 1.16.5 world server.",
            "The launcher downloads Mojang's older runtime for the server automatically "
            "on the first launch.",
            majors,
        )
    return Check(
        "java", "Java", WARN,
        found + f" Minecraft {REQUIRED_JAVA_MAJOR}+ is what the game needs.",
        "The launcher will download a newer runtime for you on the first launch.",
        majors,
    )


def check_network(probe=None):
    """Can we reach the download host at all?

    A DNS lookup, not a fetch. It answers "is this machine online and is the
    host resolvable", which is the failure people actually hit (offline,
    captive portal, DNS blocked), without spending a second of the player's
    time on a HEAD request that a CDN may rate-limit.
    """
    import socket

    probe = probe or (lambda host: socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP))
    host = "huggingface.co"
    try:
        probe(host)
    except Exception:
        return Check(
            "network", "Internet connection", WARN,
            f"Could not look up {host}.",
            "You can still play if the castle is already installed. If this is your "
            "first launch, connect to the internet first - and check whether a firewall "
            "or a captive portal (hotel or campus Wi-Fi) is blocking it.",
            False,
        )
    return Check("network", "Internet connection", OK,
                 "The download server is reachable.", "", True)


def check_install():
    """Is the castle already on disk? Not a problem either way - it decides
    whether the player is looking at a five-minute wait or a fifteen-second
    one, which is worth saying out loud before they press Play."""
    root = get_data_root()
    world = os.path.join(root, "resources", "servers")
    installed = False
    try:
        for dirpath, dirnames, _filenames in os.walk(world):
            if "world" in dirnames:
                candidate = os.path.join(dirpath, "world")
                if os.path.isfile(os.path.join(candidate, "level.dat")):
                    installed = True
                break
    except OSError:
        pass

    if installed:
        return Check("install", "Game files", OK,
                     "The castle is already installed - launches take about 15 seconds.",
                     "", True)
    return Check(
        "install", "Game files", OK,
        f"Nothing installed yet. The first launch downloads about {_gb_text(DOWNLOAD_MB)}.",
        "Stay on this screen or go and make a cup of tea - it only happens once.",
        False,
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
def run_all(java_manager=None, include_network=True):
    """Every check, in the order the player should read them."""
    checks = [check_disk(), check_memory(), check_java(java_manager)]
    if include_network:
        checks.append(check_network())
    checks.append(check_install())
    return checks


def worst_status(checks):
    for status in (FAIL, WARN, PENDING):
        if any(c.status == status for c in checks):
            return status
    return OK


def summary_line(checks):
    """One sentence for the top of the dialog."""
    status = worst_status(checks)
    if status == OK:
        return "Everything looks good. You are ready to play."
    if status == FAIL:
        blocking = [c.title.lower() for c in checks if c.status == FAIL]
        return "Needs attention before you play: " + ", ".join(blocking) + "."
    return "You can play, but there are a couple of things worth knowing."


def as_text(checks):
    """Plain-text rendering, for the diagnostics report and the clipboard."""
    symbols = {OK: "[ ok ]", WARN: "[warn]", FAIL: "[FAIL]", PENDING: "[ .. ]"}
    lines = [f"System check ({platform_name()})", "-" * 46]
    for check in checks:
        lines.append(f"{symbols.get(check.status, '[    ]')} {check.title}: {check.detail}")
        if check.remedy:
            lines.append(f"        -> {check.remedy}")
    return "\n".join(lines)
