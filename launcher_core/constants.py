"""Fixed facts about what the launcher installs and how it runs it.

The values live in ``data/catalog.json`` so the map URL, the port numbers
and the version pins can be changed without editing code. Every lookup
below falls back to the value that used to be hardcoded here, which means a
missing, truncated or hand-mangled catalog degrades to a working launcher
rather than a broken one.

The JVM flag builders stay in Python on purpose: they are logic (the flags
change with heap size), not configuration, and putting a JVM command line
in an editable data file is a code-execution surface for no benefit.
"""

from .content import load

_CATALOG = load("catalog")


def _section(name):
    value = _CATALOG.get(name)
    return value if isinstance(value, dict) else {}


def _int(section, key, default):
    try:
        return int(_section(section).get(key, default))
    except (TypeError, ValueError):
        return default


def _str(section, key, default):
    value = _section(section).get(key, default)
    return str(value) if isinstance(value, (str, int, float)) else default


# ---------------------------------------------------------------------------
# Versions and ports
# ---------------------------------------------------------------------------
SERVER_PORT = _int("ports", "server", 25565)
PROXY_PORT = _int("ports", "proxy", 25566)

MC_VERSION = _str("minecraft", "client_version", "1.20.1")
SERVER_VERSION_DIR = _str("minecraft", "server_version", "1.16.5")
REQUIRED_JAVA_MAJOR = _int("minecraft", "required_java_major", 17)
# The 1.16.5 Paper server refuses to boot on Java 17+, so it gets its own
# older JVM when one is available.
SERVER_JAVA_MIN = _int("minecraft", "server_java_min", 8)
SERVER_JAVA_MAX = _int("minecraft", "server_java_max", 16)


# ---------------------------------------------------------------------------
# Downloadable content
# ---------------------------------------------------------------------------
def _resources():
    raw = _CATALOG.get("resources")
    if not isinstance(raw, list):
        return []
    return [r for r in raw if isinstance(r, dict) and r.get("id") and r.get("name")]


def _resource(resource_id, fallback_name, fallback_url, fallback_mb):
    for entry in _resources():
        if entry.get("id") == resource_id:
            try:
                approx = int(entry.get("approx_mb", fallback_mb))
            except (TypeError, ValueError):
                approx = fallback_mb
            return {
                "id": resource_id,
                "name": str(entry["name"]),
                "url": str(entry.get("url") or fallback_url),
                "sha256": str(entry.get("sha256") or "").strip(),
                "approx_mb": approx,
                "description": str(entry.get("description") or ""),
            }
    return {
        "id": resource_id, "name": fallback_name, "url": fallback_url,
        "sha256": "", "approx_mb": fallback_mb, "description": "",
    }


MAP = _resource(
    "map", "Witchcraft and Wizardry",
    "https://huggingface.co/datasets/Foxybeo/wz_launcher/resolve/main/"
    "Witchcraft%20and%20Wizardry.zip?download=true",
    1100,
)
RESOURCE_PACK = _resource(
    "resource_pack", "Resource Pack",
    "https://huggingface.co/datasets/Foxybeo/wz_launcher/resolve/main/"
    "Resource%20Pack.zip?download=true",
    350,
)

MAP_NAME = MAP["name"]
RESOURCE_PACK_NAME = RESOURCE_PACK["name"]
MAP_DOWNLOAD_URL = MAP["url"]
RESOURCE_PACK_DOWNLOAD_URL = RESOURCE_PACK["url"]

# Optional integrity pins. Put the real digests in catalog.json to make the
# launcher refuse an archive that does not match byte for byte. Empty means
# "skip verification" - the download is still HTTPS-only and host-restricted.
RESOURCE_SHA256 = {
    MAP_NAME: MAP["sha256"],
    RESOURCE_PACK_NAME: RESOURCE_PACK["sha256"],
}

# Rough install footprint, used by the first-run disk check. Deliberately an
# estimate: being told "you need about 4 GB free" before a download starts is
# worth far more than being told the exact number after it fails.
#
# The modpack is part of this now that the mods are fetched rather than
# shipped - see the modpack section below, and note the disk check has to be
# told about it or a player passes the check and then runs out of space
# halfway through installing fifty jars.
def _modpack_mb():
    try:
        return int(_section("modpack").get("approx_mb", 120))
    except (TypeError, ValueError):
        return 120


DOWNLOAD_MB = MAP["approx_mb"] + RESOURCE_PACK["approx_mb"] + _modpack_mb()
CLIENT_INSTALL_MB = _int("disk", "client_mb", 1400)
DISK_HEADROOM_MB = _int("disk", "headroom_mb", 1000)
REQUIRED_DISK_MB = DOWNLOAD_MB + CLIENT_INSTALL_MB + DISK_HEADROOM_MB


# Only these domains may serve launcher content. A download URL that resolves
# anywhere else is refused before a single byte is fetched, so a tampered
# catalog or a redirect chain cannot silently pull a payload from an
# attacker-controlled host.
#
# Matched by registrable domain rather than by exact hostname. An earlier
# version listed five specific CDN hostnames, and when Hugging Face moved its
# storage to `us.aws.cdn.hf.co` every download started failing with what
# looked to the player like a security warning about their own network.
#
# A suffix match is still a real restriction: it only trusts hosts under
# domains Hugging Face controls. The leading dot in the comparison is what
# stops `evil-huggingface.co` from qualifying.
def _allowed_domains():
    raw = _section("download").get("allowed_domains")
    domains = {
        str(d).lower().strip().lstrip(".")
        for d in raw or []
        if isinstance(d, str) and d.strip()
    }
    return domains or {"huggingface.co", "hf.co"}


ALLOWED_DOWNLOAD_DOMAINS = _allowed_domains()


def _host_in(host, domains):
    host = (host or "").lower().rstrip(".")
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def is_allowed_download_host(host):
    return _host_in(host, ALLOWED_DOWNLOAD_DOMAINS)


# ---------------------------------------------------------------------------
# Client modpack
#
# Installed from Modrinth on first run rather than bundled with the installer.
# Everything is pinned in the catalog - version, URL and SHA-512 - so the
# launcher never asks an API what "latest" is. A build therefore installs the
# same mod set today and in a year, and an upstream that changes underneath us
# fails the hash check instead of silently shipping different code.
# ---------------------------------------------------------------------------
def _modpack():
    raw = _section("modpack")
    extras = []
    for entry in raw.get("extra_mods") or []:
        if not isinstance(entry, dict) or not entry.get("path") or not entry.get("url"):
            continue
        extras.append({
            "name": str(entry.get("name") or ""),
            "path": str(entry["path"]),
            "url": str(entry["url"]),
            "sha512": str(entry.get("sha512") or "").strip(),
        })
    try:
        approx = int(raw.get("approx_mb", 120))
    except (TypeError, ValueError):
        approx = 120
    return {
        "id": str(raw.get("id") or "fabulously-optimized"),
        "name": str(raw.get("name") or "Fabulously Optimized"),
        "version": str(raw.get("version") or ""),
        "url": str(raw.get("url") or ""),
        "sha512": str(raw.get("sha512") or "").strip(),
        "approx_mb": approx,
    }, extras


MODPACK, MODPACK_EXTRA_MODS = _modpack()
MODPACK_NAME = MODPACK["name"]
MODPACK_MB = MODPACK["approx_mb"]


def _allowed_modpack_domains():
    raw = _section("modpack").get("allowed_domains")
    domains = {
        str(d).lower().strip().lstrip(".")
        for d in raw or []
        if isinstance(d, str) and d.strip()
    }
    return domains or {"modrinth.com"}


ALLOWED_MODPACK_DOMAINS = _allowed_modpack_domains()


def is_allowed_modpack_host(host):
    """Mods come from Modrinth, not from the map's host.

    A separate allow-list on purpose: widening the map's list to cover
    Modrinth would also let a tampered catalog point the 1.1 GB world
    download at a mod CDN, and the two have no reason to overlap.
    """
    return _host_in(host, ALLOWED_MODPACK_DOMAINS)


# Retry policy for content downloads. A player on hotel Wi-Fi should not have
# to press Play five times to get past five dropped connections.
DOWNLOAD_MAX_RETRIES = _int("download", "max_retries", 4)
try:
    DOWNLOAD_BACKOFF_SECONDS = float(_section("download").get("backoff_seconds", 2.0))
    DOWNLOAD_BACKOFF_MAX_SECONDS = float(_section("download").get("backoff_max_seconds", 30.0))
except (TypeError, ValueError):
    DOWNLOAD_BACKOFF_SECONDS, DOWNLOAD_BACKOFF_MAX_SECONDS = 2.0, 30.0


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------
def link(name, default=""):
    """A URL from the catalog. Only ever handed to the OS browser, so it is
    checked for https before it leaves - a catalog is a file on disk, and a
    file on disk is one antivirus false-restore away from being wrong."""
    value = _section("links").get(name, default)
    value = str(value or "").strip()
    return value if value.startswith("https://") else default


# Temporary kill-switch for everything that reaches out to GitHub (issue
# tracker, releases page, update checks) while that hosting is in flux. Flip
# back to True to restore the buttons and the startup update check - nothing
# else needs to change.
EXTERNAL_LINKS_ENABLED = False

ISSUES_URL = link("issues") if EXTERNAL_LINKS_ENABLED else ""
NEW_ISSUE_URL = link("issue_new") if EXTERNAL_LINKS_ENABLED else ""
RELEASES_URL = link("releases") if EXTERNAL_LINKS_ENABLED else ""
JAVA_DOWNLOAD_URL = link("java_download", "https://adoptium.net")


# ---------------------------------------------------------------------------
# Server entry and maintenance
# ---------------------------------------------------------------------------
SERVER_ENTRY_NAME = _str("server_entry", "name", "Witchcraft and Wizardry")
SERVER_ENTRY_HOST = _str("server_entry", "host", "127.0.0.1")
SERVER_ENTRY_IP = f"{SERVER_ENTRY_HOST}:{PROXY_PORT}"

SERVER_KEEP_ON_CLEAR = {"server.jar", "eula.txt", "server.properties", "plugins"}

PLAYER_DATA_WORLD_ITEMS = {"playerdata", "stats", "advancements"}
PLAYER_DATA_SERVER_FILES = {"usercache.json", "ops.json", "whitelist.json"}


# Properties forced into server.properties on every launch, on top of the
# security block ServerManager applies itself. These are what turn a local
# multiplayer server back into something that behaves like the singleplayer
# world the map was designed for: every command a world with cheats allows,
# and flight, which an unmodified server otherwise kicks you for.
def _server_properties():
    section = _section("server_properties")
    return {
        str(key): str(value)
        for key, value in section.items()
        if isinstance(key, str) and not key.startswith("_")
        and isinstance(value, (str, int, float, bool))
    } or {
        "allow-flight": "true",
        "enable-command-block": "true",
        "op-permission-level": "4",
        "function-permission-level": "2",
        "spawn-protection": "0",
        "broadcast-console-to-ops": "false",
        "broadcast-rcon-to-ops": "false",
    }


SERVER_PROPERTIES = _server_properties()

# Operator level written into ops.json for the person playing. 4 is the
# vanilla maximum and the level a singleplayer world with cheats grants: /op,
# /gamemode, /tp, /give, /time, and the rest.
SERVER_OP_LEVEL = 4

# 90s ceiling: a 1.1 GB world (chunk load + plugin init) can take much longer
# than the old 22.5s budget on a slow disk, a cold file cache or a machine
# with antivirus scanning java.exe/server.jar on every launch. This only caps
# the worst case - is_port_open() returns the moment the port actually opens.
PORT_WAIT_MAX_TRIES = 180
PORT_WAIT_INTERVAL = 0.5

# How many times a crashed server is restarted automatically before the
# launcher gives up and shows the user an error.
SERVER_MAX_RESTARTS = 2

# How many player-data backups to keep before the oldest is pruned.
PLAYER_DATA_BACKUP_KEEP = 5


# ---------------------------------------------------------------------------
# JVM flags
# ---------------------------------------------------------------------------
def server_jvm_args(heap_mb):
    """Aikar's flags, sized to the machine instead of a hardcoded 6 GB.

    ``-Xms == -Xmx`` on purpose: a fixed heap keeps G1 from resizing mid-game,
    which is what causes the periodic freeze people blame on the map.
    ``AlwaysPreTouch`` is dropped below 3 GB - on a small machine, touching
    every page up front costs more startup time than it saves later.
    """
    args = [
        f"-Xms{heap_mb}M",
        f"-Xmx{heap_mb}M",
        "-XX:+UseG1GC",
        "-XX:+ParallelRefProcEnabled",
        "-XX:MaxGCPauseMillis=130",
        "-XX:+UnlockExperimentalVMOptions",
        "-XX:+DisableExplicitGC",
        "-XX:G1NewSizePercent=30",
        "-XX:G1MaxNewSizePercent=40",
        "-XX:G1HeapRegionSize=8M",
        "-XX:G1ReservePercent=20",
        "-XX:G1HeapWastePercent=5",
        "-XX:G1MixedGCCountTarget=4",
        "-XX:InitiatingHeapOccupancyPercent=15",
        "-XX:G1MixedGCLiveThresholdPercent=90",
        "-XX:G1RSetUpdatingPauseTimePercent=5",
        "-XX:SurvivorRatio=32",
        "-XX:MaxTenuringThreshold=1",
        "-XX:+PerfDisableSharedMem",
        "-Dusing.aikars.flags=true",
        "-Daikar.new.flags=true",
        # The server is loopback-only by default; no reason to let a plugin
        # or a crafted packet reach out over RMI/JNDI.
        "-Dcom.sun.jndi.rmi.object.trustURLCodebase=false",
        "-Dcom.sun.jndi.cosnaming.object.trustURLCodebase=false",
        "-Dlog4j2.formatMsgNoLookups=true",
    ]
    if heap_mb >= 3072:
        args.insert(3, "-XX:+AlwaysPreTouch")
    return args


PROXY_JVM_ARGS = [
    "-Xms128M",
    "-Xmx384M",
    "-XX:+UseSerialGC",
    "-XX:MaxGCPauseMillis=100",
    "-Dlog4j2.formatMsgNoLookups=true",
]


def client_jvm_args(heap_mb):
    return [
        f"-Xms{min(heap_mb, 1024)}M",
        f"-Xmx{heap_mb}M",
        "-XX:+UseG1GC",
        "-XX:MaxGCPauseMillis=100",
        "-XX:+ParallelRefProcEnabled",
        "-XX:G1HeapRegionSize=16M",
        "-XX:+UnlockExperimentalVMOptions",
        "-XX:+DisableExplicitGC",
        "-Dlog4j2.formatMsgNoLookups=true",
    ]
