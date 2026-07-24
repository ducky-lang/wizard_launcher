import socket
import time

try:
    import psutil
except ImportError:
    psutil = None

from .constants import PORT_WAIT_MAX_TRIES, PORT_WAIT_INTERVAL
from .exceptions import LauncherError

CONNECT_TIMEOUT = 0.6


def _port_in_use(port, host="127.0.0.1"):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(CONNECT_TIMEOUT)
        return sock.connect_ex((host, port)) == 0


def describe_port_holder(port):
    """Name the process sitting on `port`, so the error message can say
    "Minecraft is already running" instead of the useless "port in use"."""
    if psutil is None:
        return None
    try:
        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr and conn.laddr.port == port and conn.status == psutil.CONN_LISTEN:
                if conn.pid:
                    try:
                        proc = psutil.Process(conn.pid)
                        return f"{proc.name()} (PID {conn.pid})"
                    except Exception:
                        return f"PID {conn.pid}"
    except Exception:
        # net_connections needs elevation on some systems; not worth failing over.
        return None
    return None


def check_port_free(port, grace_seconds=6.0, log_fn=None):
    """Raise unless `port` is free, giving it a short grace period first.

    The grace period matters on a *second* launch: the previous server may
    have exited a fraction of a second ago and the OS can still be holding
    its listening socket. Failing instantly there produced the worst kind of
    error - one that goes away if the user simply tries again - so we wait
    briefly and only then give up.
    """
    if not _port_in_use(port):
        return

    if grace_seconds > 0:
        if log_fn:
            log_fn(f"Port {port} is still busy; waiting for it to be released...")
        if wait_port_released(port, timeout=grace_seconds):
            return

    holder = describe_port_holder(port)
    detail = f"\n\nIt is being used by: {holder}" if holder else ""
    raise LauncherError(
        f"Port {port} is already in use.{detail}\n\n"
        "How to fix:\n"
        "1. Press \"Stop\" in the launcher to release the ports, or\n"
        "2. Close any other Minecraft server / launcher that is running, then try again."
    )


def is_port_open(port, max_tries=PORT_WAIT_MAX_TRIES, interval=PORT_WAIT_INTERVAL,
                 should_continue=None):
    """Wait for a service to start listening.

    `should_continue` lets the caller abort early - the server manager passes
    a check that the process is still alive, so a server that dies at second
    two fails immediately instead of after the full 30 second timeout.
    """
    for _ in range(max_tries):
        if should_continue is not None and not should_continue():
            return False
        if _port_in_use(port):
            return True
        time.sleep(interval)
    return _port_in_use(port)


def wait_port_released(port, timeout=10, interval=0.4):
    """Wait for a port to actually become free after a process was stopped.

    Killing a process does not instantly free its listening socket, so
    without this the next Play press could race the OS and fail on
    "port in use".
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _port_in_use(port):
            return True
        time.sleep(interval)
    return not _port_in_use(port)


def process_alive(proc):
    return proc is not None and proc.poll() is None
