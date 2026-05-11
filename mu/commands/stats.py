"""Mode-toggle and stats slash commands: /thinking, /agentic, /yolo, /stats."""

from typing import Any

from . import CommandResult, command


@command("/thinking", help="Toggle extended thinking / reasoning mode.")
def thinking_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    session.thinking = not session.thinking
    return CommandResult(
        ok=True,
        message=f"Thinking mode: {session.thinking}",
        data={"thinking": session.thinking},
    )


@command("/agentic", help="Toggle agentic tool-calling mode.")
def agentic_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    session.agentic = not session.agentic
    return CommandResult(
        ok=True,
        message=f"Agentic mode: {session.agentic}",
        data={"agentic": session.agentic},
    )


@command("/yolo", help="Toggle YOLO mode — auto-approve modifying tool calls.")
def yolo_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    current = bool(session.variables.get("yolo", False))
    session.variables["yolo"] = not current
    # Persist to the session file so the setting survives restart.
    if hasattr(session, "session_manager") and hasattr(session, "folder_context"):
        session.session_manager.save_history(session.folder_context)
    return CommandResult(
        ok=True,
        message=f"YOLO mode: {session.variables['yolo']}",
        data={"yolo": session.variables["yolo"]},
    )


@command("/stats", help="Show runtime stats (tokens, cost, memory, queue).")
def stats_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    # Reuse the canonical collector from utils.runtime_metrics so the
    # numbers match the live status line.
    from utils.runtime_metrics import collect_runtime_metrics

    snapshot = collect_runtime_metrics(session)
    return CommandResult(ok=True, message="ok", data=snapshot)
