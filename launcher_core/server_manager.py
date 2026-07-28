import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
import uuid

from .constants import (
    SERVER_PORT, PORT_WAIT_MAX_TRIES, SERVER_MAX_RESTARTS,
    SERVER_ENTRY_IP, SERVER_ENTRY_NAME,
    MAP_NAME, RESOURCE_PACK_NAME, MAP_DOWNLOAD_URL, RESOURCE_PACK_DOWNLOAD_URL,
    SERVER_OP_LEVEL, SERVER_PROPERTIES,
    server_jvm_args,
)
from .exceptions import LauncherError
from .install_state import fingerprint
from .nbt_utils import read_servers_dat, write_servers_dat
from .platform_utils import no_window_kwargs
from .port_utils import check_port_free, is_port_open, process_alive
from .resource_downloader import ensure_resource


def offline_uuid(username):
    """The UUID a server in offline mode will give this player.

    Vanilla derives it as ``UUID.nameUUIDFromBytes("OfflinePlayer:" + name)``,
    which is a version-3 (MD5) UUID. Reproducing it here is what lets the
    launcher write an ops.json entry that the server will actually match:
    ops are keyed by UUID, so a made-up one silently grants nothing.
    """
    digest = bytearray(hashlib.md5(f"OfflinePlayer:{username}".encode("utf-8")).digest())
    digest[6] = (digest[6] & 0x0F) | 0x30   # version 3
    digest[8] = (digest[8] & 0x3F) | 0x80   # RFC 4122 variant
    return str(uuid.UUID(bytes=bytes(digest)))


class ServerManager:
    """Prepare the world/resource pack and run the 1.16.5 Minecraft server."""

    def __init__(self, log_fn, map_source_dir, world_dest_dir,
                 resourcepack_source, mc_dir, server_dir, settings=None,
                 progress_fn=None, state=None):
        self.log = log_fn
        self.map_source_dir = map_source_dir
        self.world_dest_dir = world_dest_dir
        self.resourcepack_source = resourcepack_source
        self.mc_dir = mc_dir
        self.server_dir = server_dir
        self.settings = settings
        self.progress = progress_fn or (lambda fraction, message=None: None)
        self.state = state

        self._restart_count = 0
        self._supervisor_stop = threading.Event()
        # The server process this manager currently owns. Held directly rather
        # than looked up from the ProcessManager, because the supervisor's
        # whole job is to notice the moment it stops being alive - and a
        # lookup that (correctly) only returns live processes cannot tell
        # "crashed" apart from "was never started".
        self._proc = None

    # ------------------------------------------------------------------
    # Content preparation
    # ------------------------------------------------------------------
    def copy_map(self, force=False):
        """Install the world, downloading it first if it is not cached.

        ``force`` is used by "Clear Player Data", which deliberately deletes
        the world before calling this so the castle comes back exactly as it
        ships. The cached archive is reused, so a reinstall is a local copy
        rather than another gigabyte over the wire.
        """
        if not force and os.path.exists(self.world_dest_dir) and os.listdir(self.world_dest_dir):
            self.log("World already installed.")
            return
        ensure_resource(MAP_NAME, MAP_DOWNLOAD_URL, self.map_source_dir, self.log, self.progress)
        if not os.path.isdir(self.map_source_dir):
            raise LauncherError(
                f"The map files could not be found after download.\n\nExpected at:\n{self.map_source_dir}\n\n"
                "How to fix:\n"
                "1. Check your internet connection and press Play again, or\n"
                "2. Use \"Clear Cache\" and let the launcher download the map fresh."
            )
        self.log("Installing the castle..." if force
                 else "Installing the castle for the first time...")
        self.progress(None, "Installing the castle...")
        # Copy into a staging folder first: a half-copied world directory
        # would look "already installed" on the next run and boot broken.
        staging = self.world_dest_dir + "_installing"
        if os.path.isdir(staging):
            shutil.rmtree(staging, ignore_errors=True)
        shutil.copytree(self.map_source_dir, staging)
        if os.path.isdir(self.world_dest_dir):
            shutil.rmtree(self.world_dest_dir, ignore_errors=True)
        os.replace(staging, self.world_dest_dir)
        self.log("Castle installed.")

    # ------------------------------------------------------------------
    def _resourcepack_fingerprint(self):
        return fingerprint(RESOURCE_PACK_NAME, RESOURCE_PACK_DOWNLOAD_URL, self.mc_dir)

    def setup_resourcepack(self):
        """Put the resource pack in the game folder and switch it on.

        Gated on :mod:`launcher_core.install_state`, because the copy is
        several hundred megabytes and it produced an identical result on
        every launch after the first. The state check is paired with an
        existence check on the installed pack, so deleting it by hand still
        brings it back.
        """
        pack_name = os.path.basename(self.resourcepack_source)
        installed = os.path.join(self.mc_dir, "resourcepacks", pack_name)
        wanted = self._resourcepack_fingerprint()
        if (self.state and self.state.matches("resourcepack", wanted)
                and os.path.exists(installed)):
            self.log("Resource pack already installed.")
            return

        ensure_resource(
            RESOURCE_PACK_NAME, RESOURCE_PACK_DOWNLOAD_URL,
            self.resourcepack_source, self.log, self.progress,
        )
        if not os.path.exists(self.resourcepack_source):
            self.log(f"Resource pack not found at {self.resourcepack_source}; skipping.")
            return

        os.makedirs(self.mc_dir, exist_ok=True)
        dst_dir = os.path.join(self.mc_dir, "resourcepacks")
        os.makedirs(dst_dir, exist_ok=True)

        dst_path = os.path.join(dst_dir, pack_name)
        self.progress(None, f"Installing {RESOURCE_PACK_NAME}...")

        try:
            if os.path.isdir(self.resourcepack_source):
                if os.path.exists(dst_path):
                    shutil.rmtree(dst_path, ignore_errors=True)
                shutil.copytree(self.resourcepack_source, dst_path)
            else:
                if os.path.exists(dst_path):
                    os.remove(dst_path)
                shutil.copy2(self.resourcepack_source, dst_path)
            self.log(f"Resource pack ready: {pack_name}")
        except Exception as e:
            self.log(f"Could not copy the resource pack: {e}")
            return

        self._enable_resourcepack_in_options(pack_name)
        if self.state:
            self.state.mark("resourcepack", wanted)

    def _enable_resourcepack_in_options(self, pack_name):
        options_path = os.path.join(self.mc_dir, "options.txt")
        entry = f"file/{pack_name}"

        lines = []
        if os.path.isfile(options_path):
            try:
                with open(options_path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
            except Exception as e:
                self.log(f"Could not read options.txt: {e}")

        found = False
        new_lines = []
        for line in lines:
            if line.startswith("resourcePacks:"):
                found = True
                if entry in line:
                    new_lines.append(line)
                else:
                    content = line.strip()
                    if content.endswith("]"):
                        content = content[:-1]
                        content += ("" if content.endswith("[") else ",") + f'"{entry}"]'
                    new_lines.append(content + "\n")
            else:
                new_lines.append(line)

        if not found:
            new_lines.append(f'resourcePacks:["{entry}"]\n')

        try:
            os.makedirs(self.mc_dir, exist_ok=True)
            with open(options_path, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
        except Exception as e:
            self.log(f"Could not write options.txt: {e}")

    def setup_server_list(self):
        servers_path = os.path.join(self.mc_dir, "servers.dat")
        wanted = fingerprint(SERVER_ENTRY_NAME, SERVER_ENTRY_IP)
        if (self.state and self.state.matches("server_list", wanted)
                and os.path.isfile(servers_path)):
            return
        try:
            existing = read_servers_dat(servers_path)
            updated = False
            for s in existing:
                if s.get("ip") == SERVER_ENTRY_IP:
                    s["name"] = SERVER_ENTRY_NAME
                    updated = True
                    break
            if not updated:
                existing.insert(0, {"name": SERVER_ENTRY_NAME, "ip": SERVER_ENTRY_IP})
            write_servers_dat(servers_path, existing)
            if self.state:
                self.state.mark("server_list", wanted)
        except Exception as e:
            self.log(f"Could not update the multiplayer list: {e}")

    # ------------------------------------------------------------------
    # server.properties hardening
    # ------------------------------------------------------------------
    def apply_server_properties(self):
        """Force the security- and gameplay-critical properties every launch.

        ``server-ip`` is the important one for safety. Left blank (the shipped
        default) the server binds 0.0.0.0 and anyone on the same coffee-shop
        Wi-Fi can connect to the player's world. Unless LAN play was explicitly
        turned on in Settings, we pin it to loopback so only this machine can
        reach it.

        The rest come from ``catalog.json`` and exist to undo the ways a
        *server* is more restrictive than the singleplayer world this map was
        built for: flight is allowed rather than treated as cheating, command
        blocks run, and the operator level is the one a world with cheats
        gives you. Rewritten on every launch on purpose - the server itself
        rewrites this file on shutdown, so anything set once does not stay set.
        """
        path = os.path.join(self.server_dir, "server.properties")
        if not os.path.isfile(path):
            return

        bind = self.settings.bind_address if self.settings else "127.0.0.1"

        # Gameplay first, security second: the security block wins any overlap,
        # so a hand-edited catalog can tune flight and command levels but can
        # never unpick the loopback binding or switch RCON back on.
        enforced = dict(SERVER_PROPERTIES)
        enforced.update({
            "server-ip": "" if bind == "0.0.0.0" else bind,
            "server-port": str(SERVER_PORT),
            # RCON and query are remote-control surfaces this launcher never
            # uses. Any value other than "off" is an open door for nothing.
            "enable-rcon": "false",
            "enable-query": "false",
            "rcon.password": "",
            # The client authenticates against Microsoft in the launcher; the
            # local server stays offline-mode so ViaProxy can bridge versions.
            "online-mode": "false",
            "prevent-proxy-connections": "false",
            "snooper-enabled": "false",
        })

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception as e:
            self.log(f"Could not read server.properties: {e}")
            return

        seen = set()
        out = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                out.append(line)
                continue
            key = stripped.split("=", 1)[0].strip()
            if key in enforced:
                seen.add(key)
                out.append(f"{key}={enforced[key]}\n")
            else:
                out.append(line)

        for key, value in enforced.items():
            if key not in seen:
                out.append(f"{key}={value}\n")

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(out)
        except Exception as e:
            self.log(f"Could not write server.properties: {e}")
            return

        if bind == "0.0.0.0":
            self.log("LAN play is ON - other devices on your network can join.")
        else:
            self.log("Server locked to this computer only (loopback).")

    # ------------------------------------------------------------------
    def grant_operator(self, username):
        """Make the player an operator, so every singleplayer command works.

        ``allow-flight`` and ``enable-command-block`` get you most of the way,
        but ``/gamemode``, ``/tp``, ``/give``, ``/time`` and the rest are gated
        on operator level rather than on a property. A singleplayer world with
        cheats grants exactly this, which is the behaviour the map assumes.

        The entry has to be keyed by the UUID the *server* will compute, not
        the player's real Mojang one: the world runs offline-mode so ViaProxy
        can bridge versions, and an offline server derives the UUID from the
        name. Writing the real UUID here would look right and grant nothing.

        Rewritten each launch because the name can change between sessions -
        signing in, signing out, or simply typing a different one.
        """
        if not username:
            return
        path = os.path.join(self.server_dir, "ops.json")
        entry = {
            "uuid": offline_uuid(username),
            "name": username,
            "level": SERVER_OP_LEVEL,
            "bypassesPlayerLimit": True,
        }
        try:
            os.makedirs(self.server_dir, exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump([entry], f, indent=2)
            os.replace(tmp, path)
            self.log(f"{username} has full command access in this world.")
        except Exception as e:
            # Not fatal: the world still loads, the player just cannot use
            # cheats. Worth a line in the log, not worth blocking a launch.
            self.log(f"Could not grant command access: {e}")

    # ------------------------------------------------------------------
    # Process
    # ------------------------------------------------------------------
    def start_server(self, java_path, process_manager, username=None):
        server_jar = os.path.join(self.server_dir, "server.jar")
        if not os.path.isfile(server_jar):
            raise LauncherError(
                f"The server files are missing.\n\nExpected server.jar at:\n{server_jar}\n\n"
                "How to fix:\n"
                "1. Reinstall Wizard Launcher - the installer places these files, or\n"
                "2. Check that your antivirus has not quarantined server.jar."
            )

        eula_file = os.path.join(self.server_dir, "eula.txt")
        if not os.path.isfile(eula_file):
            # Recreating it is safe and saves the user a confusing dead end:
            # the file only records acceptance of Mojang's EULA, which the
            # installer already presented.
            try:
                with open(eula_file, "w", encoding="utf-8") as f:
                    f.write("eula=true\n")
            except Exception:
                raise LauncherError(
                    f"eula.txt is missing and could not be created at:\n{eula_file}\n\n"
                    "How to fix:\n"
                    "1. Create that file manually with the single line: eula=true"
                )

        self.apply_server_properties()
        self.grant_operator(username)
        check_port_free(SERVER_PORT, log_fn=self.log)

        heap_mb = self.settings.server_ram_mb if self.settings else 2048
        self.log(f"Starting the world server ({heap_mb} MB)...")

        proc = self._spawn(java_path, heap_mb)
        self._proc = proc
        process_manager.register(proc, "server", graceful_command="stop\n", port=SERVER_PORT)
        process_manager.pipe_output(proc, "SERVER")

        ready = is_port_open(
            SERVER_PORT,
            should_continue=lambda: process_alive(proc),
        )
        if not ready:
            if not process_alive(proc):
                raise LauncherError(
                    "The world server stopped while starting up.\n\n"
                    "Most common cause: not enough free memory, or a corrupted world folder.\n\n"
                    "How to fix:\n"
                    "1. Close other heavy programs and press Play again,\n"
                    "2. Lower the server memory in Settings, or\n"
                    "3. Use \"Clear Cache\" to reinstall the world (this erases your progress)."
                )
            raise LauncherError(
                f"The world server did not finish starting within "
                f"{int(PORT_WAIT_MAX_TRIES * 0.5)} seconds.\n\n"
                "How to fix:\n"
                "1. Press Play again - the first start is always the slowest, or\n"
                "2. Open the log folder (Settings > Diagnostics) to see what the server "
                "was doing."
            )

        self.log("World server is ready.")
        self._restart_count = 0
        return proc

    def _spawn(self, java_path, heap_mb):
        cmd = [java_path] + server_jvm_args(heap_mb) + ["-jar", "server.jar", "nogui"]
        try:
            return subprocess.Popen(
                cmd,
                cwd=self.server_dir,
                # stdin as a pipe is what makes a *graceful* stop possible:
                # ProcessManager writes "stop\n" here so the server saves the
                # world instead of being killed mid-write.
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                bufsize=1,
                **no_window_kwargs(),
            )
        except OSError as e:
            raise LauncherError(
                f"Could not start Java for the server.\n\nJava path:\n{java_path}\n\nDetails: {e}\n\n"
                "How to fix:\n"
                "1. Install Java 17 from https://adoptium.net and press Play again."
            )

    # ------------------------------------------------------------------
    # Crash supervision
    # ------------------------------------------------------------------
    def start_supervisor(self, java_path, process_manager, on_restart=None, on_give_up=None):
        """Watch the server and bring it back if it dies while the player is
        still in the game.

        Without this, a server OOM mid-session drops everyone to the
        "connection lost" screen with no explanation and no way back short of
        restarting the launcher.
        """
        if self.settings is not None and not self.settings.get("auto_restart_server"):
            return

        self._supervisor_stop.clear()

        def _watch():
            while not self._supervisor_stop.is_set():
                time.sleep(2)
                proc = self._proc
                if proc is None or self._supervisor_stop.is_set():
                    return
                if process_alive(proc):
                    continue

                # The server is gone. If the client already exited too, this
                # is a normal shutdown - not a crash.
                if not process_manager.is_running("client"):
                    return

                if self._restart_count >= SERVER_MAX_RESTARTS:
                    self.log("The world server crashed too many times; not restarting again.")
                    if on_give_up:
                        on_give_up()
                    return

                self._restart_count += 1
                self.log(
                    f"The world server stopped unexpectedly. "
                    f"Restarting (attempt {self._restart_count}/{SERVER_MAX_RESTARTS})..."
                )
                if on_restart:
                    on_restart(self._restart_count)

                try:
                    process_manager.stop("server", graceful=False)
                    new_proc = self._spawn(java_path, self.settings.server_ram_mb if self.settings else 2048)
                    self._proc = new_proc
                    process_manager.register(new_proc, "server", graceful_command="stop\n", port=SERVER_PORT)
                    process_manager.pipe_output(new_proc, "SERVER")
                    if is_port_open(SERVER_PORT, should_continue=lambda: process_alive(new_proc)):
                        self.log("World server is back up - rejoin from the multiplayer menu.")
                    else:
                        self.log("The world server could not be restarted.")
                        if on_give_up:
                            on_give_up()
                        return
                except Exception as e:
                    self.log(f"Could not restart the world server: {e}")
                    if on_give_up:
                        on_give_up()
                    return

        threading.Thread(target=_watch, daemon=True, name="server-supervisor").start()

    def stop_supervisor(self):
        """Must be called before a deliberate stop, otherwise the supervisor
        sees the shutdown as a crash and restarts the server we just killed."""
        self._supervisor_stop.set()
        self._proc = None
