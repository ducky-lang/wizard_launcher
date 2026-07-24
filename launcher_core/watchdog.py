"""
Standalone watchdog: runs detached from the launcher to monitor the
Minecraft client process. When the client exits, it automatically kills
the remaining server/proxy processes to free ports 25565/25566 - this
works even if the launcher was already closed beforehand.

Usage: python watchdog.py <client_pid> <pid_to_kill_1> <pid_to_kill_2> ...
"""
import os
import sys
import time

try:
    import psutil
except ImportError:
    psutil = None


def pid_alive(pid):
    if psutil is not None:
        return psutil.pid_exists(pid)
    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def kill_tree(pid):
    if psutil is not None:
        try:
            parent = psutil.Process(pid)
            for child in parent.children(recursive=True):
                try:
                    child.terminate()
                except Exception:
                    pass
            parent.terminate()
            try:
                parent.wait(timeout=5)
            except Exception:
                try:
                    parent.kill()
                except Exception:
                    pass
        except Exception:
            pass
    else:
        if os.name == "nt":
            os.system(f"taskkill /PID {pid} /T /F >nul 2>&1")
        else:
            try:
                os.kill(pid, 15)
            except Exception:
                pass


def main():
    if len(sys.argv) < 3:
        return
    client_pid = int(sys.argv[1])
    kill_pids = [int(p) for p in sys.argv[2:]]

    while pid_alive(client_pid):
        time.sleep(2)

    for pid in kill_pids:
        kill_tree(pid)


if __name__ == "__main__":
    main()