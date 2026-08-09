"""Background host helpers for durable job execution."""

from __future__ import annotations

import sys
from dataclasses import dataclass


DEFAULT_CONTROLLER_PORT = 30311
DEFAULT_CONTROLLER_HOST = "127.0.0.1"


@dataclass(frozen=True)
class ControllerHostStatus:
    running: bool
    pid: int | None = None
    started: bool = False
    detail: str = ""


def ensure_controller_daemon(
    *,
    port: int = DEFAULT_CONTROLLER_PORT,
    host: str = DEFAULT_CONTROLLER_HOST,
) -> ControllerHostStatus:
    """Ensure the detached MuCLI daemon that owns JobController is alive.

    GUI imports stay lazy so merely registering `/job` does not pull FastAPI /
    uvicorn into normal TUI startup.
    """
    from mu.gui import daemon
    from mu.gui.launcher import _resolve_mucli_script

    existing = daemon.is_running() or daemon.pid_for_port(port)
    if existing is not None:
        return ControllerHostStatus(
            running=True,
            pid=existing,
            detail="controller daemon already running",
        )

    argv = [
        sys.executable or "python3",
        _resolve_mucli_script(),
        "--gui",
        "--gui-foreground",
        "--port",
        str(int(port)),
    ]
    if host and host != DEFAULT_CONTROLLER_HOST:
        argv += ["--host", str(host)]
    try:
        pid = daemon.spawn_detached(argv, port=int(port))
        daemon.write_pid(pid)
    except Exception as exc:
        return ControllerHostStatus(
            running=False,
            detail=f"could not start controller daemon: {exc}",
        )

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
