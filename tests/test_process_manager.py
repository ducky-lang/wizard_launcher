"""Regression tests for the bug that made the *second* launch unreliable.

Run with:  python -m unittest discover -s tests

These use real child processes rather than mocks on purpose: the bug was in
how the launcher reasoned about process liveness, and a mock that reports
whatever the test tells it to would have happily passed against the broken
code too.
"""

import os
import subprocess
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from launcher_core.process_manager import ProcessManager  # noqa: E402

PYTHON = sys.executable


def spawn_sleeper(seconds=30):
    """A child that stays alive until we kill it."""
    return subprocess.Popen(
        [PYTHON, "-c", f"import time; time.sleep({seconds})"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def spawn_finished():
    """A child that has already exited by the time this returns."""
    proc = subprocess.Popen(
        [PYTHON, "-c", "pass"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    proc.wait()
    return proc


class ProcessManagerTests(unittest.TestCase):
    def setUp(self):
        self.messages = []
        self.pm = ProcessManager(self.messages.append, self.messages.append)
        self._spawned = []

    def tearDown(self):
        for proc in self._spawned:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass
        self.pm.clear_state()

    def track(self, proc):
        self._spawned.append(proc)
        return proc

    # -- the actual regression -----------------------------------------
    def test_second_registration_replaces_finished_first(self):
        """Play, quit the game, Play again: the manager must hand back the
        NEW client, not the finished one from the first session."""
        first = spawn_finished()
        self.pm.register(first, "client")

        second = self.track(spawn_sleeper())
        self.pm.register(second, "client")

        self.assertIs(self.pm.get("client"), second)
        self.assertTrue(self.pm.is_running("client"))
        # The corpse must be gone, not merely ranked lower.
        self.assertEqual([e.pid for e in self.pm.processes if e.name == "client"],
                         [second.pid])

    def test_get_ignores_dead_process(self):
        dead = spawn_finished()
        self.pm.register(dead, "server")
        self.assertIsNone(self.pm.get("server"))
        self.assertFalse(self.pm.is_running("server"))

    def test_get_returns_newest_live_after_crash_restart(self):
        """The crash supervisor registers a replacement server while the old
        entry may still be in the list."""
        crashed = spawn_finished()
        self.pm.register(crashed, "server")
        replacement = self.track(spawn_sleeper())
        self.pm.register(replacement, "server")
        self.assertIs(self.pm.get("server"), replacement)

    def test_prune_dead_drops_only_finished(self):
        alive = self.track(spawn_sleeper())
        self.pm.register(alive, "proxy")
        self.pm.register(spawn_finished(), "server")
        self.assertEqual(self.pm.prune_dead(), 1)
        self.assertEqual([e.name for e in self.pm.processes], ["proxy"])

    def test_stop_all_clears_finished_client(self):
        """What _watch_client_exit does when the player quits Minecraft."""
        client = spawn_finished()
        self.pm.register(client, "client")
        self.pm.stop_all(include_client=True)
        self.assertEqual(self.pm.processes, [])

    def test_watchdog_not_spawned_for_dead_client(self):
        """Refusing to watch an already-exited client is what stops the
        watchdog from immediately killing a healthy server."""
        server = self.track(spawn_sleeper())
        self.pm.register(server, "server")
        self.pm.register(spawn_finished(), "client")

        self.pm.spawn_watchdog()
        self.assertIsNone(self.pm.watchdog_pid)
        # The server it would have killed is untouched.
        time.sleep(0.5)
        self.assertIsNone(server.poll())

    def test_stop_is_idempotent(self):
        proc = self.track(spawn_sleeper())
        self.pm.register(proc, "server")
        self.assertTrue(self.pm.stop("server", graceful=False))
        self.assertTrue(self.pm.stop("server", graceful=False))
        self.assertEqual(self.pm.processes, [])


if __name__ == "__main__":
    unittest.main()
