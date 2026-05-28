"""Background-daemon helpers for `mucli --gui`.

Spawns the real server in a detached child process, writes a PID file,
and returns the terminal to the user. `--gui-stop` reads the PID file
and SIGTERMs the child.

Not a full POSIX daemon (no double-fork, no umask reset, no chdir to
``/``) — that's overkill for a per-user CLI tool. We use
``subprocess.Popen(start_new_session=True)`` which:

- Detaches the child from the controlling terminal (new session).
- Survives the parent shell exiting.
- Lets us redirect stdio to a log file so the child doesn't write to
  the user's terminal.

PID file:  ``~/.mucli/gui.pid``
Log file:  ``~/.mucli/logs/gui.log``  (overwritten each boot)
"""

from __future__ import annotations

import errno
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import utils.config as _config


def _home() -> Path:
    return Path(_config.HISTORY_DIR)


def pid_file() -> Path:
    return _home() / "gui.pid"


def log_file() -> Path:
    return _home() / "logs" / "gui.log"


def is_running() -> int | None:
    """Return the PID of the existing daemon, or None if not running."""
    path = pid_file()
    if not path.exists():
        return None
    try:
        pid = int(path.read_text().strip())
    except (ValueError, OSError):
        return None
    if pid <= 0:
        return None
    try:
        # signal 0 → ESRCH if process gone, EPERM if not ours but alive.
        os.kill(pid, 0)
        return pid
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            # Stale pid file; remove it.
            try:
                path.unlink()
            except OSError:
                pass
            return None
        # EPERM means it's alive but we can't signal it — still running.
        return pid


def spawn_detached(args: list[str], *, port: int) -> int:
    """Spawn the foreground-server invocation as a detached child.

    Caller is responsible for the foreground command shape — typically
    a re-invocation of this script with an internal marker flag so the
    child knows to actually run the server.

    Returns the child PID. Caller should write it to ``pid_file()``.
    """
    log_path = log_file()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = open(log_path, "wb", buffering=0)

    # start_new_session=True detaches the child from the controlling
    # terminal. stdio → log file. Parent exits independently.
    child = subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=log_handle,
        stderr=log_handle,
        start_new_session=True,
        close_fds=True,
    )
    return child.pid


def write_pid(pid: int) -> None:
    path = pid_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(pid))


def stop(timeout: float = 5.0) -> tuple[bool, str]:
    """SIGTERM the daemon. Returns ``(ok, message)``."""
    pid = is_running()
    if pid is None:
        return False, "no GUI server is running"
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        return False, f"could not signal pid {pid}: {exc}"

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError as exc:
            if exc.errno == errno.ESRCH:
                try:
                    pid_file().unlink()
                except OSError:
                    pass
                return True, f"stopped pid {pid}"
        time.sleep(0.1)

    # Still alive after timeout — escalate.
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        pid_file().unlink()
    except OSError:
        pass
    return True, f"force-killed pid {pid} (didn't respond to SIGTERM)"


def wait_for_port(port: int, *, host: str = "127.0.0.1", timeout: float = 8.0) -> bool:
    """Poll-connect until the server starts listening, or timeout."""
    import socket as _socket

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with _socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.15)
    return False
