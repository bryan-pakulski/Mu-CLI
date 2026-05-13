"""Mode-related slash commands: /plan (read-only enforcement), /mode (agent strategy).

`/plan` toggles `session.variables['plan_mode']`. When on, the pre_tool
hook installed by `mu/agent/plan_mode.py` short-circuits any write-side
tool with a clear refusal envelope. Read-only tools continue to work.

`/plan on` / `/plan off` explicitly set the state; bare `/plan` toggles.

`/mode` switches between agent strategies (default, debug, feature,
research, loop, security). Each mode is described in `documentation/<mode>_mode.md`.
"""

from typing import Any

from . import CommandResult, command


def _refresh_hud(session: Any) -> None:
    try:
        from mucli import refresh_memory_hud

        refresh_memory_hud(session, getattr(session, "ui", None))
    except ImportError:
        pass


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


@command(
    "/mode",
    help="Switch agent mode (default|debug|feature|research|loop|security).",
)
def mode_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    from utils.config import AGENT_MODE_METADATA

    arg = (args or "").strip()

    if arg and arg.lower() in AGENT_MODE_METADATA:
        chosen = arg.lower()
        session.variables["agent_mode"] = chosen
        fc = getattr(session, "folder_context", None)
        session.session_manager.save_history(fc)
        _refresh_hud(session)
        meta = AGENT_MODE_METADATA[chosen]
        message = (
            f"Agent strategy set to: {chosen} — "
            f"{meta.get('description', '')} "
            f"({meta.get('documentation', '')})"
        ).strip()
        return CommandResult(
            ok=True,
            message=message,
            data={
                "current_mode": chosen,
                "mode": {"name": chosen, **meta},
                "available_modes": AGENT_MODE_METADATA,
            },
        )

    # No arg, or unknown — print overview and return current.
    if allow_prompt:
        try:
            from mucli import print_mode_overview

            print_mode_overview(session)
        except ImportError:
            pass

    if arg:
        return CommandResult(
            ok=False,
            message=f"Unknown mode: {arg}",
            data={
                "current_mode": session.variables.get("agent_mode", "default"),
                "available_modes": AGENT_MODE_METADATA,
            },
        )

    return CommandResult(
        ok=True,
        message="Listed available agent modes.",
        data={
            "current_mode": session.variables.get("agent_mode", "default"),
            "available_modes": AGENT_MODE_METADATA,
        },
    )
