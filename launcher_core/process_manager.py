"""Lifecycle management for the three child processes the launcher owns:
the 1.16.5 server, ViaProxy, and the Minecraft client.

The old implementation had four problems that made "Stop" unreliable:

* It called ``terminate()`` on the Minecraft server. A server killed that
  way never runs its shutdown hook, so chunks and player data that were
  still in memory are lost - and on a bad day the region file is left
  half-written. The server now gets ``stop`` on stdin first and is only
  force-killed if it ignores that.
* The detached watchdog process kept running after Stop, still holding the
  old PIDs. Once the OS recycled one of those PIDs the watchdog would kill
  an unrelated process. The watchdog is now tracked and stopped too.
* Nothing survived a launcher crash: orphaned server/proxy processes kept
  ports 25565/25566 bound and the next launch failed with "port in use"
  until the user rebooted. A run-state file now lets a fresh launcher
  identify and reap exactly its own orphans.
* Stop did not verify anything actually died, so the UI reported success
  while the port was still held.
* Entries were registered but never unregistered. A finished client stayed
  in the list forever, so on the *second* Play press ``get("client")``
  handed back the previous run's dead process object. The watchdog then
  waited on a PID that had already exited (or, worse, on whatever process
  the OS had since given that number to) and killed the server and proxy
  seconds after the new game started - intermittently, which is exactly why
  it looked like a random failure. Dead entries are now reaped on every
  register and every lookup, and lookups return the *newest live* entry.
"""

import json
import os
import subprocess
import sys
import threading
import time

try:
    import psutil
except ImportError:
    psutil = None

from .paths import is_frozen, get_app_root, get_data_root
from .platform_utils import IS_WINDOWS, detached_kwargs, no_window_kwargs
from .port_utils import process_alive, wait_port_released

RUN_STATE_FILE = "runtime_state.json"

# How long each stage of a shutdown is given before escalating.
GRACEFUL_TIMEOUT = 25   # server saving the world after `stop`
TERMINATE_TIMEOUT = 8   # polite SIGTERM / WM_CLOSE
KILL_TIMEOUT = 5        # last-resort SIGKILL


class ManagedProcess:
    def __init__(self, proc, name, graceful_command=None, port=None):
        self.proc = proc
        self.name = name
        self.graceful_command = graceful_command
        self.port = port
        self.pid = proc.pid
        self.create_time = self._read_create_time()

    def _read_create_time(self):
        """Recorded so a later run can tell "PID 4231 is still my server"
        apart from "PID 4231 is now somebody's text editor"."""
        if psutil is None:
            return 0.0
        try:
            return psutil.Process(self.pid).create_time()
        except Exception:
            return 0.0

    def to_state(self):
        return {"pid": self.pid, "name": self.name, "create_time": self.create_time}


class ProcessManager:

    def __init__(self, log_fn, write_log_fn):
        self.log = log_fn
        self._write_log_file = write_log_fn
        self.processes = []
        self.processes_lock = threading.RLock()
        self.watchdog_pid = None
        self._state_path = os.path.join(get_data_root(), RUN_STATE_FILE)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------
    def register(self, proc, name, graceful_command=None, port=None):
        entry = ManagedProcess(proc, name, graceful_command, port)
        with self.processes_lock:
            # Drop anything under this name that has already exited before
            # adding the new one. Without this the list grows one dead entry
            # per session and every later lookup finds the corpse first.
            dropped = [e for e in self.processes
                       if e.name == name and not process_alive(e.proc)]
            if dropped:
                self.processes = [e for e in self.processes if e not in dropped]
                self._write_log_file(
                    f"Cleared {len(dropped)} finished '{name}' entr"
                    f"{'y' if len(dropped) == 1 else 'ies'} from a previous session."
                )
            self.processes.append(entry)
        self._save_state()
        self._write_log_file(f"Registered process '{name}' (pid={entry.pid})")
        return entry

    def prune_dead(self):
        """Forget every process that has exited. Returns how many were dropped."""
        with self.processes_lock:
            live = [e for e in self.processes if process_alive(e.proc)]
            dropped = len(self.processes) - len(live)
            self.processes = live
        if dropped:
            self._save_state()
        return dropped

    def get_entry(self, name):
        """The newest *live* process registered under `name`, or None.

        Newest-live rather than first-registered on purpose: after a crash
        restart there can legitimately be two server entries, and the caller
        always means the one that is currently running.
        """
        with self.processes_lock:
            for entry in reversed(self.processes):
                if entry.name == name and process_alive(entry.proc):
                    return entry
        return None

    def get(self, name):
        entry = self.get_entry(name)
        return entry.proc if entry else None

    def is_running(self, name):
        return self.get_entry(name) is not None

    def any_running(self, names=("server", "proxy", "client")):
        return any(self.is_running(name) for name in names)

    def pipe_output(self, proc, label):
        def _reader():
            try:
                for line in proc.stdout:
                    line = line.rstrip()
                    if line:
                        self._write_log_file(f"[{label}] {line}")
            except Exception:
                pass
        threading.Thread(target=_reader, daemon=True, name=f"pipe-{label}").start()

    @staticmethod
    def process_alive(proc):
        return process_alive(proc)

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------
    def stop(self, name, graceful=True, on_progress=None):
        """Stop every process registered under `name`.

        Returns True when nothing under that name is left running.

        The escalation ladder is: graceful console command -> terminate the
        process tree -> kill the process tree. Each step is only taken if
        the previous one did not finish in time, so a healthy server always
        gets the chance to save.
        """
        with self.processes_lock:
            targets = [e for e in self.processes if e.name == name]

        if not targets:
            return True

        report = on_progress or (lambda msg: None)
        all_stopped = True

        for entry in targets:
            proc = entry.proc
            try:
                if not process_alive(proc):
                    continue

                if graceful and entry.graceful_command:
                    report(f"Asking {name} to shut down and save...")
                    if self._graceful_stop(entry):
                        continue
                    self._write_log_file(
                        f"{name} did not exit within {GRACEFUL_TIMEOUT}s of the stop command; escalating."
                    )

                report(f"Stopping {name}...")
                self._terminate_tree(entry)

                if process_alive(proc):
                    self._write_log_file(f"{name} ignored terminate; killing.")
                    self._kill_tree(entry)

                if process_alive(proc):
                    all_stopped = False
                    self._write_log_file(f"Could not stop {name} (pid={entry.pid}).")
            except Exception as e:
                all_stopped = False
                self._write_log_file(f"Error stopping process {name}: {e}")

        with self.processes_lock:
            self.processes = [
                e for e in self.processes
                if e.name != name or process_alive(e.proc)
            ]
        self._save_state()

        # A dead process is not the same as a free port: Windows can hold the
        # socket in TIME_WAIT briefly. Waiting here is what makes pressing
        # Play again immediately after Stop actually work.
        for entry in targets:
            if entry.port and not wait_port_released(entry.port, timeout=10):
                self._write_log_file(f"Port {entry.port} still bound after stopping {name}.")
                all_stopped = False

        return all_stopped

    def _graceful_stop(self, entry):
        """Send the console command (``stop`` for the Minecraft server) and
        wait. Returns True if the process exited on its own."""
        proc = entry.proc
        try:
            if proc.stdin is None or proc.stdin.closed:
                return False
            proc.stdin.write(entry.graceful_command)
            proc.stdin.flush()
        except Exception as e:
            self._write_log_file(f"Could not send graceful stop to {entry.name}: {e}")
            return False

        deadline = time.time() + GRACEFUL_TIMEOUT
        while time.time() < deadline:
            if not process_alive(proc):
                self._write_log_file(f"{entry.name} shut down cleanly.")
                return True
            time.sleep(0.25)
        return False

    def _terminate_tree(self, entry):
        proc = entry.proc
        if psutil is not None:
            try:
                parent = psutil.Process(proc.pid)
                children = parent.children(recursive=True)
                for child in children:
                    try:
                        child.terminate()
                    except Exception:
                        pass
                parent.terminate()
                psutil.wait_procs([parent] + children, timeout=TERMINATE_TIMEOUT)
                return
            except psutil.NoSuchProcess:
                return
            except Exception:
                pass
        try:
            proc.terminate()
            proc.wait(timeout=TERMINATE_TIMEOUT)
        except subprocess.TimeoutExpired:
            pass
        except Exception:
            pass

    def _kill_tree(self, entry):
        proc = entry.proc
        if psutil is not None:
            try:
                parent = psutil.Process(proc.pid)
                children = parent.children(recursive=True)
                for child in children:
                    try:
                        child.kill()
                    except Exception:
                        pass
                parent.kill()
                psutil.wait_procs([parent] + children, timeout=KILL_TIMEOUT)
                return
            except psutil.NoSuchProcess:
                return
            except Exception:
                pass
        try:
            proc.kill()
            proc.wait(timeout=KILL_TIMEOUT)
        except Exception:
            pass

    def stop_all(self, include_client=False, on_progress=None):
        """Shut the whole session down. The watchdog goes first so it cannot
        race us and kill a PID we are about to reuse."""
        self.stop_watchdog()
        ok = True
        # Proxy before server: it is the front door, and closing it first
        # stops a client from reconnecting while the server is saving.
        ok &= self.stop("proxy", on_progress=on_progress)
        ok &= self.stop("server", on_progress=on_progress)
        if include_client:
            ok &= self.stop("client", on_progress=on_progress)
        # Anything that exited on its own (typically the client, when the
        # player simply quit Minecraft) is dropped here, so the next launch
        # starts from a list that contains only live processes.
        self.prune_dead()
        return ok

    # ------------------------------------------------------------------
    # Detached watchdog
    # ------------------------------------------------------------------
    def spawn_watchdog(self, client_proc=None, client_name="client",
                       kill_names=("proxy", "server")):
        """Spawn a watchdog DETACHED from the launcher, so the server/proxy
        still get shut down when Minecraft exits even if the user closed the
        launcher first.

        `client_proc` should be the object the caller just started. Passing it
        explicitly - rather than looking it up by name - is what guarantees
        the watchdog watches *this* game and not a previous one: a lookup can
        only ever be as correct as the bookkeeping behind it, and this is the
        one call where getting it wrong kills a running session.
        """
        # Make sure a previous session's watchdog is gone before starting a
        # new one; two watchdogs holding different PID sets is how a stale one
        # ends up killing the current server.
        self.stop_watchdog()

        if client_proc is None:
            client_proc = self.get(client_name)
        if client_proc is None or not process_alive(client_proc):
            self._write_log_file("No live client process to watch; skipping watchdog.")
            return

        with self.processes_lock:
            kill_pids = [str(e.pid) for e in self.processes
                         if e.name in kill_names and process_alive(e.proc)]
        if not kill_pids:
            return

        if is_frozen():
            # A packaged build has no separate script to run, so this same
            # exe is re-invoked with --watchdog (see launcher.py).
            cmd = [sys.executable, "--watchdog", str(client_proc.pid)] + kill_pids
            watchdog_cwd = get_app_root()
        else:
            watchdog_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watchdog.py")
            if not os.path.isfile(watchdog_path):
                self._write_log_file("watchdog.py not found, skipping watchdog spawn.")
                return

            python_exe = sys.executable
            if IS_WINDOWS:
                pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
                if os.path.isfile(pythonw):
                    python_exe = pythonw
            cmd = [python_exe, watchdog_path, str(client_proc.pid)] + kill_pids
            watchdog_cwd = os.path.dirname(watchdog_path)

        kwargs = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        kwargs.update(detached_kwargs())

        try:
            proc = subprocess.Popen(cmd, cwd=watchdog_cwd, **kwargs)
            self.watchdog_pid = proc.pid
            self._save_state()
            self._write_log_file(
                f"Watchdog spawned (pid={proc.pid}, client_pid={client_proc.pid}, watching_to_kill={kill_pids})"
            )
        except Exception as e:
            self._write_log_file(f"Could not spawn watchdog: {e}")

    def stop_watchdog(self):
        """Kill the detached watchdog.

        This is the fix for the nastiest bug in the old Stop path: after a
        manual stop the watchdog stayed alive holding the now-dead server and
        proxy PIDs. Windows recycles PIDs aggressively, so the next time the
        Minecraft client exited the watchdog would happily kill whatever
        process had inherited those numbers.
        """
        pid = self.watchdog_pid
        if not pid:
            return
        self.watchdog_pid = None
        self._kill_pid(pid, "watchdog")
        self._save_state()

    def _kill_pid(self, pid, label):
        if psutil is not None:
            try:
                proc = psutil.Process(pid)
                proc.terminate()
                proc.wait(timeout=TERMINATE_TIMEOUT)
                self._write_log_file(f"Stopped {label} (pid={pid}).")
                return True
            except psutil.NoSuchProcess:
                return True
            except Exception:
                try:
                    psutil.Process(pid).kill()
                    return True
                except Exception:
                    pass
        elif IS_WINDOWS:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    **no_window_kwargs(),
                )
                return True
            except Exception:
                pass
        else:
            try:
                os.kill(pid, 15)
                return True
            except Exception:
                pass
        self._write_log_file(f"Could not stop {label} (pid={pid}).")
        return False

    # ------------------------------------------------------------------
    # Crash recovery across launcher restarts
    # ------------------------------------------------------------------
    def _save_state(self):
        """Record our live PIDs so a future launcher can reap them if this
        process dies without cleaning up."""
        try:
            with self.processes_lock:
                entries = [e.to_state() for e in self.processes if process_alive(e.proc)]
            state = {
                "launcher_pid": os.getpid(),
                "saved_at": time.time(),
                "watchdog_pid": self.watchdog_pid,
                "processes": entries,
            }
            tmp = self._state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f)
            os.replace(tmp, self._state_path)
        except Exception:
            pass

    def clear_state(self):
        try:
            if os.path.isfile(self._state_path):
                os.remove(self._state_path)
        except Exception:
            pass

    def reap_orphans(self):
        """Kill leftovers from a previous run that ended badly.

        Only processes whose PID *and* creation timestamp both match what we
        recorded are touched - matching on PID alone would be a great way to
        terminate a stranger's process after the OS recycled the number.
        Returns the number of processes reaped.
        """
        if not os.path.isfile(self._state_path):
            return 0
        try:
            with open(self._state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            self.clear_state()
            return 0

        if state.get("launcher_pid") == os.getpid():
            return 0

        reaped = 0
        candidates = list(state.get("processes") or [])
        watchdog_pid = state.get("watchdog_pid")
        if watchdog_pid:
            candidates.append({"pid": watchdog_pid, "name": "watchdog", "create_time": 0.0})

        for item in candidates:
            pid = item.get("pid")
            name = item.get("name", "?")
            recorded_ct = item.get("create_time") or 0.0
            if not pid:
                continue

            if psutil is not None:
                try:
                    proc = psutil.Process(pid)
                except psutil.NoSuchProcess:
                    continue
                except Exception:
                    continue
                # Identity check: same PID AND same start time (1s tolerance
                # for float rounding across runs).
                if recorded_ct:
                    try:
                        if abs(proc.create_time() - recorded_ct) > 1.0:
                            self._write_log_file(
                                f"Skipping pid {pid}: it is no longer the {name} we started."
                            )
                            continue
                    except Exception:
                        continue
                elif name != "watchdog":
                    # No creation time recorded and it is not our own
                    # watchdog - too risky to kill blind.
                    continue

            if self._kill_pid(pid, f"orphaned {name}"):
                reaped += 1

        self.clear_state()
        if reaped:
            self.log(f"Cleaned up {reaped} leftover process(es) from a previous session.")
        return reaped

    # ------------------------------------------------------------------
    def cleanup(self):
        """Called when the launcher window closes.

        If the game is still running we deliberately leave the server, the
        proxy and the watchdog alone: the player asked to close the
        *launcher*, not to be kicked out of the castle. The detached
        watchdog takes over and shuts everything down when Minecraft exits.

        If the game is not running, this is a plain tidy-up and everything
        goes away now.
        """
        if self.is_running("client"):
            self._write_log_file(
                "Launcher closing while the game is still running - "
                "handing shutdown over to the detached watchdog."
            )
            self._save_state()
            return

        with self.processes_lock:
            has_any = any(process_alive(e.proc) for e in self.processes)
        if not has_any and not self.watchdog_pid:
            self.clear_state()
            return

        self.log("Shutting down background processes...")
        self.stop_all()
        with self.processes_lock:
            self.processes.clear()
        self.clear_state()
        self.log("Cleaned up.")
