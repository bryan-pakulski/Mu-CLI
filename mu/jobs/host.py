"""Background host helpers for durable job execution."""

from __future__ import annotations

import sys
from dataclasses import dataclass

from mu.gui import daemon
from mu.gui.launcher import DEFAULT_HOST, DEFAULT_PORT, _resolve_mucli_script


@dataclass(frozen=True)
class ControllerHostStatus:
    running: bool
    pid: int | None = None
    started: bool = False
    detail: str = ""


def ensure_controller_daemon(*, port: int = DEFAULT_PORT, host: str = DEFAULT_HOST) -> ControllerHostStatus:
    """Ensure the detached MuCLI daemon that owns JobController is alive.

    The daemon also serves the GUI API, but no browser is opened or required.
    This keeps the first durable-job host compatible with GUI/mobile while TUI
    callers can exit immediately after queueing work.
    """
    existing = daemon.is_running() or daemon.pid_for_port(port)
    if existing is not None:
        return ControllerHostStatus(running=True, pid=existing, detail="controller daemon already running")

    argv = [
        sys.executable or "python3",
        _resolve_mucli_script(),
        "--gui",
        "--gui-foreground",
        "--port",
        str(int(port)),
    ]
    if host and host != DEFAULT_HOST:
        argv += ["--host", str(host)]
    try:
        pid = daemon.spawn_detached(argv, port=int(port))
        daemon.write_pid(pid)
    except Exception as exc:
        return ControllerHostStatus(running=False, detail=f"could not start controller daemon: {exc}")

    if not daemon.wait_for_port(int(port), host=host, timeout=8.0):
        return ControllerHostStatus(
            running=False,
            pid=pid,
            started=True,
            detail=f"controller daemon spawned as pid {pid} but did not become reachable",
        )
    return ControllerHostStatus(
        running=True,
        pid=pid,
        started=True,
        detail=f"controller daemon started as pid {pid}",
    )
