"""GUI server boot.

Two entry points:
  * ``run_gui(args, build_session)`` — what mucli calls when ``--gui``
    is set. By default it daemonizes (forks itself with a marker flag)
    and returns to the terminal.
  * ``run_server_foreground(args, build_session)`` — what the daemon
    child runs. Stays in the foreground (logs to gui.log) and runs
    uvicorn until killed.

The marker flag is ``--gui-serve`` and is internal — users never set
it. It's how the parent process tells the spawned child "you're the
worker, actually run the server."
"""

from __future__ import annotations

import os
import sys

from utils.logger import logger

from . import daemon
from .app import create_app


DEFAULT_PORT = 30311


def run_gui(args, build_session) -> None:
    """Top-level entry. Daemonizes by default; runs in foreground if
    ``args.gui_foreground`` is set."""
    port = int(getattr(args, "port", None) or DEFAULT_PORT)

    # gui_foreground marker MUST be checked before is_running — the
    # parent writes the pid file before spawning the child, so the
    # child would otherwise read its OWN pid and exit thinking the GUI
    # is "already running."
    if getattr(args, "gui_foreground", False):
        run_server_foreground(args, build_session, port=port)
        return

    existing = daemon.is_running()
    if existing is not None:
        url = f"http://127.0.0.1:{port}/"
        print(f"  mucli GUI already running at {url} (pid {existing})")
        print(f"  stop with: mucli --gui-stop")
        return

    # Re-invoke ourselves with the internal marker so the child runs
    # the server in foreground while we detach.
    child_args = _build_child_argv(args, port)
    pid = daemon.spawn_detached(child_args, port=port)
    daemon.write_pid(pid)

    if not daemon.wait_for_port(port, timeout=8.0):
        print(
            f"  mucli GUI spawned (pid {pid}) but isn't listening on {port} yet.\n"
            f"  Tail the log: tail -f {daemon.log_file()}"
        )
        return

    print(f"  mucli GUI → http://127.0.0.1:{port}/  (pid {pid})")
    print(f"  log:  {daemon.log_file()}")
    print(f"  stop: mucli --gui-stop")


def run_server_foreground(args, build_session, *, port: int) -> None:
    """Run uvicorn in the current process. Used by the daemon child."""
    app = create_app(args=args, build_session_fn=build_session, port=port)

    import uvicorn

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
        loop="asyncio",
    )
    server = uvicorn.Server(config)
    try:
        server.run()
    finally:
        try:
            app.state.prompts.cancel_all()
        except Exception:
            pass
        session = getattr(app.state, "session", None)
        if session is not None:
            try:
                session.session_manager.save_history(session.folder_context)
            except Exception:
                pass
        try:
            daemon.pid_file().unlink()
        except OSError:
            pass
        logger.info("GUI: server stopped")


def stop_gui() -> int:
    """`mucli --gui-stop` entry. Returns shell exit code."""
    ok, msg = daemon.stop()
    print(f"  {msg}")
    return 0 if ok else 1


# ---------------------------------------------------------------------------


def _build_child_argv(args, port: int) -> list[str]:
    """Construct the argv for the daemonized child.

    Forwards the user's flags (session, provider, model, workspace,
    yolo, debug, system) and tags the invocation with ``--gui
    --gui-foreground --port <port>`` so the child runs the server in
    foreground (no further forking).
    """
    py = sys.executable or "python3.11"
    script = _resolve_mucli_script()
    argv = [py, script, "--gui", "--gui-foreground", "--port", str(port)]

    if getattr(args, "session", None):
        argv += ["--session", str(args.session)]
    if getattr(args, "provider", None):
        argv += ["--provider", str(args.provider)]
    if getattr(args, "model", None):
        argv += ["--model", str(args.model)]
    for workspace in getattr(args, "workspace", None) or []:
        argv += ["--workspace", str(workspace)]
    if getattr(args, "yolo", False):
        argv += ["--yolo"]
    if getattr(args, "debug", False):
        argv += ["--debug"]
    return argv


def _resolve_mucli_script() -> str:
    """Locate mucli.py on disk for child re-invocation."""
    # The repository layout is fixed: mucli.py lives at the tools root.
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    candidate = os.path.join(here, "mucli.py")
    if os.path.exists(candidate):
        return candidate
    # Fallback: $0 from argv if available.
    if sys.argv and os.path.exists(sys.argv[0]):
        return sys.argv[0]
    return "mucli.py"
