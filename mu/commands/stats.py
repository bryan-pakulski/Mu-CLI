"""Mode-toggle and stats slash commands: /thinking, /agentic, /yolo, /stats."""

from typing import Any

from . import CommandResult, command


def _emit_toggle_banner(session: Any, label: str, state: bool) -> None:
    """Print a visible '<label>: ON|OFF' banner via the session UI."""
    ui = getattr(session, "ui", None)
    if ui is None or not hasattr(ui, "show_info"):
        return
    marker = "[bold green]ON[/bold green]" if state else "[bold]OFF[/bold]"
    try:
        ui.show_info(f"{label}: {marker}")
    except Exception:
        pass


@command("/thinking", help="Toggle extended thinking / reasoning mode.")
def thinking_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    session.thinking = not session.thinking
    _emit_toggle_banner(session, "🧠 Thinking mode", session.thinking)
    return CommandResult(
        ok=True,
        message=f"Thinking mode: {'ON' if session.thinking else 'OFF'}",
        data={"thinking": session.thinking},
    )


@command("/agentic", help="Toggle agentic tool-calling mode.")
def agentic_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    session.agentic = not session.agentic
    _emit_toggle_banner(session, "🛠 Agentic mode", session.agentic)
    return CommandResult(
        ok=True,
        message=f"Agentic mode: {'ON' if session.agentic else 'OFF'}",
        data={"agentic": session.agentic},
    )


@command("/yolo", help="Toggle YOLO mode — auto-approve modifying tool calls.")
def yolo_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    current = bool(session.variables.get("yolo", False))
    session.variables["yolo"] = not current
    # Persist to the session file so the setting survives restart.
    if hasattr(session, "session_manager") and hasattr(session, "folder_context"):
        session.session_manager.save_history(session.folder_context)
    _emit_toggle_banner(session, "⚡ YOLO mode", session.variables["yolo"])
    return CommandResult(
        ok=True,
        message=f"YOLO mode: {'ON' if session.variables['yolo'] else 'OFF'}",
        data={"yolo": session.variables["yolo"]},
    )


@command("/stats", help="Show runtime stats (tokens, cost, memory, queue).")
def stats_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    # Reuse the canonical collector from utils.runtime_metrics so the
    # numbers match the live status line.
    from utils.runtime_metrics import collect_runtime_metrics

    snapshot = collect_runtime_metrics(session)
    # Print a compact summary so /stats has visible output.
    ui = getattr(session, "ui", None)
    if ui is not None and hasattr(ui, "show_info"):
        try:
            tokens = snapshot.get("tokens", {}) or {}
            ctx = snapshot.get("ctx", {}) or {}
            mode = (snapshot.get("mode", {}) or {}).get("name", "default")
            yolo_on = (snapshot.get("yolo", {}) or {}).get("enabled", False)
            plan_on = (snapshot.get("plan", {}) or {}).get("enabled", False)
            ui.show_info(
                f"mode={mode} yolo={'on' if yolo_on else 'off'} "
                f"plan={'on' if plan_on else 'off'} | "
                f"ctx={ctx.get('current', 0)}/{ctx.get('maximum', 0)} | "
                f"tokens in={tokens.get('input', 0)} out={tokens.get('output', 0)} "
                f"total={tokens.get('total', 0)} cached={tokens.get('cached', 0)} "
                f"reasoning={tokens.get('reasoning', 0)}"
            )
        except Exception:
            pass
    return CommandResult(ok=True, message="ok", data=snapshot)
