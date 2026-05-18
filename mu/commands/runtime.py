"""Runtime-flow slash commands: /continue.

The collation buffer is drained by the model via the `flush` tool — no
user-facing /flush command exists.
"""

from typing import Any

from . import CommandResult, command


def _emit_dim(session: Any, body: str, allow_prompt: bool) -> None:
    ui = getattr(session, "ui", None)
    console = getattr(ui, "console", None) if ui is not None else None
    if console is not None and allow_prompt:
        try:
            from utils.helpers import safe_markup

            console.print(f"[dim]{safe_markup(body)}[/dim]")
        except Exception:
            pass


@command(
    "/continue",
    help="Resume last paused execution (after Ctrl+C or a blocker).",
)
def continue_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    paused = str(getattr(session, "paused_execution_text", "") or "").strip()
    if not paused:
        return CommandResult(ok=False, message="No paused execution to continue.")
    _emit_dim(session, "Resuming paused execution...", allow_prompt)
    send_result = session.send_message(paused)
    return CommandResult(
        ok=bool(send_result.get("ok", True)),
        message="Resumed paused execution.",
        data={"resumed_text": paused, "send_result": send_result},
    )
