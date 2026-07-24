"""Tests for the port checks that gate every launch.

The grace period is the interesting part: pressing Play immediately after
Stop used to fail with "port 25565 is already in use" and then succeed on the
next try, because the previous server's listening socket had not been
released yet.
"""

import os
import socket
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from launcher_core.exceptions import LauncherError  # noqa: E402
from launcher_core.port_utils import (  # noqa: E402
    check_port_free, is_port_open, wait_port_released,
)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Listener:
    """A minimal server that actually accepts connections.

    A bare ``listen()`` with nobody accepting is not a good stand-in for the
    Minecraft server: its backlog fills after the first probe and every
    later probe is refused, so the port starts looking free while it is
    still bound. That would make these tests pass for the wrong reason.
    """

    def __init__(self, port=0):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", port))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while self._running:
            try:
                conn, _addr = self.sock.accept()
                conn.close()
            except OSError:
                return

    def close(self):
        self._running = False
        try:
            self.sock.close()
        except OSError:
            pass
        self._thread.join(timeout=2)


class PortUtilsTests(unittest.TestCase):
    def setUp(self):
        self.listeners = []

    def tearDown(self):
        for listener in self.listeners:
            listener.close()

    def listen(self, port=0):
        listener = Listener(port)
        self.listeners.append(listener)
        return listener, listener.port

    def test_free_port_passes_immediately(self):
        check_port_free(free_port(), grace_seconds=0)

    def test_busy_port_raises_a_helpful_error(self):
        _listener, port = self.listen()
        with self.assertRaises(LauncherError) as ctx:
            check_port_free(port, grace_seconds=0)
        message = str(ctx.exception)
        self.assertIn(str(port), message)
        self.assertIn("How to fix", message)

    def test_port_released_during_grace_period_is_accepted(self):
        """The Play-immediately-after-Stop case: the socket goes away a
        moment after the check starts, and the launch must continue."""
        listener, port = self.listen()

        def close_soon():
            time.sleep(0.6)
            listener.close()

        threading.Thread(target=close_soon, daemon=True).start()
        check_port_free(port, grace_seconds=5)   # must not raise

    def test_grace_period_gives_up_on_a_genuinely_busy_port(self):
        _listener, port = self.listen()
        started = time.time()
        with self.assertRaises(LauncherError):
            check_port_free(port, grace_seconds=1)
        self.assertGreaterEqual(time.time() - started, 0.9)

    def test_wait_port_released(self):
        listener, port = self.listen()
        self.assertFalse(wait_port_released(port, timeout=0.5))
        listener.close()
        self.assertTrue(wait_port_released(port, timeout=2))

    def test_is_port_open_aborts_when_told_to_stop(self):
        """The server manager passes a liveness check here, so a server that
        dies at second two fails then - not after the full 30s timeout."""
        port = free_port()
        started = time.time()
        self.assertFalse(is_port_open(port, should_continue=lambda: False))
        self.assertLess(time.time() - started, 1.0)


if __name__ == "__main__":
    unittest.main()
