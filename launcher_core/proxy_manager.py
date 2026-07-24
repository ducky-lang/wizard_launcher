import os
import subprocess

from .constants import (
    PROXY_PORT, SERVER_PORT, SERVER_VERSION_DIR, PROXY_JVM_ARGS, PORT_WAIT_MAX_TRIES,
)
from .exceptions import LauncherError
from .platform_utils import no_window_kwargs
from .port_utils import check_port_free, is_port_open, process_alive


class ProxyManager:
    """Run ViaProxy, which lets the 1.20.1 client talk to the 1.16.5 server."""

    def __init__(self, log_fn, proxy_dir, settings=None):
        self.log = log_fn
        self.proxy_dir = proxy_dir
        self.settings = settings

    def start_proxy(self, java_path, process_manager):
        proxy_jar = os.path.join(self.proxy_dir, "ViaProxy.jar")
        if not os.path.isfile(proxy_jar):
            raise LauncherError(
                f"The version bridge is missing.\n\nExpected ViaProxy.jar at:\n{proxy_jar}\n\n"
                "How to fix:\n"
                "1. Reinstall Wizard Launcher, or\n"
                "2. Check that your antivirus has not quarantined ViaProxy.jar."
            )

        check_port_free(PROXY_PORT, log_fn=self.log)

        # Previously hardcoded to 0.0.0.0, which published the bridge to the
        # whole local network on every launch. Now it follows the same
        # loopback-by-default rule as the server.
        bind = self.settings.bind_address if self.settings else "127.0.0.1"

        self.log("Opening the portal...")
        cmd = [java_path] + PROXY_JVM_ARGS + [
            "-jar", "ViaProxy.jar",
            "cli",
            "--target-address", f"127.0.0.1:{SERVER_PORT}",
            "--bind-address", f"{bind}:{PROXY_PORT}",
            "--target-version", SERVER_VERSION_DIR,
            "--fake-accept-resource-packs", "true",
        ]
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=self.proxy_dir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                bufsize=1,
                **no_window_kwargs(),
            )
        except OSError as e:
            raise LauncherError(
                f"Could not start Java for the version bridge.\n\nDetails: {e}\n\n"
                "How to fix:\n"
                "1. Install Java 17 from https://adoptium.net and press Play again."
            )

        process_manager.register(proc, "proxy", port=PROXY_PORT)
        process_manager.pipe_output(proc, "PROXY")

        if not is_port_open(PROXY_PORT, should_continue=lambda: process_alive(proc)):
            if not process_alive(proc):
                raise LauncherError(
                    "The version bridge stopped while starting up.\n\n"
                    "How to fix:\n"
                    "1. Press \"Stop\", then press Play again, or\n"
                    "2. Open the log folder (Settings > Diagnostics) and look for lines "
                    "starting with [PROXY]."
                )
            raise LauncherError(
                f"The version bridge did not open port {PROXY_PORT} within "
                f"{int(PORT_WAIT_MAX_TRIES * 0.5)} seconds.\n\n"
                "How to fix:\n"
                "1. Press \"Stop\" to release the ports, then press Play again."
            )

        self.log("Portal is open.")
        return proc
