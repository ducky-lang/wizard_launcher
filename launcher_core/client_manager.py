import os
import subprocess
import threading

try:
    import minecraft_launcher_lib
except ImportError:
    minecraft_launcher_lib = None

from .constants import MC_VERSION, PROXY_PORT, client_jvm_args
from .exceptions import LauncherError
from .install_state import fingerprint
from .modpack import ModpackInstaller
from .platform_utils import hide_child_consoles, no_window_kwargs


class ClientManager:
    """Install Fabric + the modpack and launch the Minecraft client."""

    def __init__(self, log_fn, write_log_fn, mc_dir, cache_dir, settings=None,
                 progress_fn=None, state=None):
        self.log = log_fn
        self._write_log_file = write_log_fn
        self.mc_dir = mc_dir
        self.cache_dir = cache_dir
        self.settings = settings
        self.progress = progress_fn or (lambda fraction, message=None: None)
        self.state = state
        self.modpack = ModpackInstaller(
            log_fn, mc_dir, cache_dir, progress_fn=progress_fn, state=state)
        # prepare() runs on a background thread while the server boots. If a
        # launch fails at the server step the user can press Play again while
        # that thread is still downloading Fabric, and two installers writing
        # the same version folder corrupts it. Second caller waits instead.
        self._prepare_lock = threading.Lock()

    # ------------------------------------------------------------------
    def prepare(self, java_path):
        """Everything that can happen before the server is up.

        Split out from :meth:`launch` so the launcher can install Fabric and
        the mods *while* the server is booting, instead of after it. On a
        cold start that overlap is most of the difference between a 30 second
        wait and a 15 second one.
        """
        with self._prepare_lock:
            return self._prepare(java_path)

    def _prepare(self, java_path):
        if minecraft_launcher_lib is None:
            raise LauncherError(
                "A required component is missing from this build (minecraft-launcher-lib).\n\n"
                "How to fix:\n"
                "1. Reinstall Wizard Launcher from the official installer."
            )

        os.makedirs(self.mc_dir, exist_ok=True)

        fabric_version_id = self._ensure_fabric(java_path)
        self.modpack.ensure()
        return fabric_version_id

    def _ensure_fabric(self, java_path):
        # Recorded as well as detected: get_installed_versions() walks the
        # versions folder and reads a JSON manifest per entry, which is work
        # that produces the same answer every launch after the first.
        wanted = fingerprint("fabric", MC_VERSION, self.mc_dir)
        if self.state and self.state.matches("fabric", wanted):
            recorded = self.state.get("fabric_version")
            # Paired with an existence check, as everywhere else: the record
            # is an optimisation, the folder is the truth.
            if recorded and os.path.isdir(os.path.join(self.mc_dir, "versions", recorded)):
                self.log(f"Client ready: {recorded}")
                return recorded

        installed = [v["id"] for v in minecraft_launcher_lib.utils.get_installed_versions(self.mc_dir)]
        for vid in installed:
            if vid.startswith("fabric-loader-") and vid.endswith(f"-{MC_VERSION}"):
                self.log(f"Client ready: {vid}")
                self._remember_fabric(wanted, vid)
                return vid

        self.log(f"Installing Minecraft {MC_VERSION} with Fabric (first run only)...")

        # minecraft_launcher_lib reports progress as a running count against a
        # max; translate that into the launcher's 0..1 progress bar.
        state = {"max": 0, "value": 0, "status": ""}

        def _emit():
            fraction = (state["value"] / state["max"]) if state["max"] else None
            self.progress(fraction, state["status"] or "Downloading game files...")

        def set_status(text):
            if text:
                state["status"] = text
                _emit()

        def set_progress(value):
            state["value"] = value
            _emit()

        def set_max(value):
            state["max"] = value

        callback = {"setStatus": set_status, "setProgress": set_progress, "setMax": set_max}

        try:
            fabric_loader = minecraft_launcher_lib.mod_loader.get_mod_loader("fabric")
            # The Fabric installer is a jar that minecraft_launcher_lib runs
            # for us, and it is the one installer the library forgets to hide
            # the console window for. Without this a black cmd window sits on
            # top of the launcher for the length of the first install.
            with hide_child_consoles(minecraft_launcher_lib.fabric):
                version_id = fabric_loader.install(
                    MC_VERSION, self.mc_dir, callback=callback, java=java_path
                )
        except Exception as e:
            raise LauncherError(
                f"Could not install the modded client.\n\nDetails: {e}\n\n"
                "How to fix:\n"
                "1. Check your internet connection and press Play again,\n"
                "2. Allow Wizard Launcher through your firewall, or\n"
                "3. Use \"Clear Cache\" to start the installation over."
            )

        self.log(f"Client installed: {version_id}")
        self._remember_fabric(fingerprint("fabric", MC_VERSION, self.mc_dir), version_id)
        return version_id

    def _remember_fabric(self, wanted, version_id):
        if not self.state:
            return
        self.state.mark("fabric_version", version_id)
        self.state.mark("fabric", wanted)

    # ------------------------------------------------------------------
    def launch(self, username, java_path, process_manager, fabric_version_id, account=None):
        heap_mb = self.settings.client_ram_mb if self.settings else 2048

        options = {
            "username": username,
            # 1.20+ dropped --server/--port; Quick Play is the supported way
            # to drop the player straight onto the server.
            "quickPlayMultiplayer": f"127.0.0.1:{PROXY_PORT}",
            "jvmArguments": client_jvm_args(heap_mb),
            "executablePath": java_path,
            "launcherName": "WizardLauncher",
        }

        if account is not None and account.uuid and account.mc_access_token:
            # Passing the real identity through means the player's own skin
            # and name show up in game. Note the token lands in the client
            # process's command line - unavoidable with vanilla Minecraft,
            # and the reason app_log.redact() strips --accessToken before
            # anything is written to the log file.
            options["uuid"] = account.uuid
            options["token"] = account.mc_access_token
            options["username"] = account.username
            # Without these two, minecraft_launcher_lib leaves the literal
            # strings "${clientid}" and "${auth_xuid}" on the command line.
            # The game starts anyway, but its telemetry/multiplayer calls are
            # made with a malformed identity, and some mods log warnings that
            # look alarming in a bug report.
            options["clientId"] = getattr(account, "client_id", "") or ""
            options["xuid"] = getattr(account, "xuid", "") or ""

        try:
            command = minecraft_launcher_lib.command.get_minecraft_command(
                fabric_version_id, self.mc_dir, options
            )
        except Exception as e:
            raise LauncherError(
                f"Could not build the Minecraft launch command.\n\nDetails: {e}\n\n"
                "How to fix:\n"
                "1. Use \"Clear Cache\" to reinstall the client, then press Play again."
            )

        self.log("Launching Minecraft...")
        self._write_log_file("Client command: " + " ".join(command))

        try:
            proc = subprocess.Popen(
                command,
                cwd=self.mc_dir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **no_window_kwargs(),
            )
        except OSError as e:
            raise LauncherError(
                f"Minecraft could not be started.\n\nDetails: {e}\n\n"
                "How to fix:\n"
                "1. Check that your antivirus is not blocking Java, then press Play again."
            )

        process_manager.register(proc, "client")
        return proc

    # Kept for compatibility with the previous single-call API.
    def launch_client(self, username, java_path, process_manager, account=None):
        version_id = self.prepare(java_path)
        return self.launch(username, java_path, process_manager, version_id, account)
