"""Mode-related slash commands: /plan (read-only enforcement).

`/plan` toggles `session.variables['plan_mode']`. When on, the pre_tool
hook installed by `mu/agent/plan_mode.py` short-circuits any write-side
tool with a clear refusal envelope. Read-only tools continue to work.

`/plan on` / `/plan off` explicitly set the state; bare `/plan` toggles.
"""

from typing import Any

from . import CommandResult, command


@command("/plan", help="Toggle plan mode (read-only tool enforcement). Args: on|off|toggle")
def plan_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    arg = (args or "").strip().lower()
    current = bool(session.variables.get("plan_mode", False))

    if arg in ("", "toggle"):
        new_value = not current
    elif arg in ("on", "true", "1", "yes", "enable"):
        new_value = True
    elif arg in ("off", "false", "0", "no", "disable"):
        new_value = False
    else:
        return CommandResult(
            ok=False,
            message=f"Unknown /plan argument: {args!r}. Use 'on', 'off', or 'toggle'.",
        )

    session.variables["plan_mode"] = new_value

    if hasattr(session, "session_manager") and hasattr(session, "folder_context"):
        try:
            session.session_manager.save_history(session.folder_context)
        except Exception:
            pass

    # Mirror the variable change onto the input handler so the prompt prefix
    # picks it up immediately on the next prompt (the input handler holds a
    # reference to session.variables, but some UIs cache state separately).
    ui = getattr(session, "ui", None)
    if ui is not None and hasattr(ui, "set_variables"):
        try:
            ui.set_variables(session.variables)
        except Exception:
            pass

    # Emit a visible banner so toggling is unmissable. The persistent
    # indicators (prompt prefix + status-line "🔒 PLAN") remain visible
    # across every subsequent turn.
    if ui is not None and hasattr(ui, "show_info"):
        try:
            if new_value:
                ui.show_info(
                    "[bold black on cyan] 🔒 PLAN MODE ENABLED [/bold black on cyan] "
                    "Write-side tools are blocked. /plan off to disable."
                )
            else:
                ui.show_info(
                    "[bold black on green] ✓ PLAN MODE DISABLED [/bold black on green] "
                    "Write-side tools are unblocked."
                )
        except Exception:
            pass

    label = "ON" if new_value else "OFF"
    msg = f"Plan mode: {label}"
    if new_value:
        msg += (
            " — write-side tools (write_file, apply_diff, bash, git mutations, "
            "feature mutators) will be blocked until plan mode is disabled."
        )
    return CommandResult(ok=True, message=msg, data={"plan_mode": new_value})
