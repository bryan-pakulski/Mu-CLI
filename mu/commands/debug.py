"""``/debug`` — the session's debug cases (``mu.debug``).

    /debug                     — the current case: status, repro, hypotheses, runs, next step
    /debug cases               — every case
    /debug select <id>         — make a case current
    /debug close <root cause>  — close the verified current case
    /debug unreproducible <why> — close it as unreproducible
"""

from __future__ import annotations

from typing import Any

from . import CommandResult, command
from ._mode_util import reply, save


@command("/debug", help="Debug cases: /debug [status|cases|select <id>|close <cause>|unreproducible <why>].")
def debug_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    from mu.debug import engine
    from mu.debug.state import debug_state
    from mu.modes.events import GateError

    parts = (args or "").split(None, 1)
    sub = parts[0].lower() if parts else "status"
    rest = parts[1].strip() if len(parts) > 1 else ""
    state = debug_state(session)
    try:
        if sub == "status":
            if state.current_case() is None:
                return reply(session, "No debug case open. Switch to debug mode and describe the bug.", allow_prompt)
            body = engine.capsule(session)
            runs = [r for r in session.session_manager.mode_state.get("checks").list(ref=state.current)]
            if runs:
                body += "\nRuns:\n" + "\n".join("  " + r.summary() for r in runs[-8:])
            return reply(session, body, allow_prompt)
        if sub == "cases":
            lines = [f"{'*' if c.id == state.current else ' '} {c.id} [{c.status}] {c.title}"
                     for c in state.cases.values()] or ["No cases."]
            return reply(session, "\n".join(lines), allow_prompt)
        if sub == "select" and rest:
            case = engine.select_case(session, rest, actor="user:cli")
            save(session)
            return reply(session, f"Current case: {case.id} {case.title}", allow_prompt)
        if sub == "close" and rest:
            result = engine.close_case(session, cause=rest, actor="user:cli")
            save(session)
            return reply(session, f"Closed {result['case'].id}." + (
                f" Gap: {'; '.join(result['gaps'])}" if result["gaps"] else ""), allow_prompt)
        if sub == "unreproducible" and rest:
            result = engine.close_case(session, unreproducible_reason=rest, actor="user:cli")
            save(session)
            return reply(session, f"{result['case'].id} closed as unreproducible.", allow_prompt)
    except (GateError, ValueError) as exc:
        return reply(session, str(exc), allow_prompt, ok=False)
    return reply(session, "Usage: /debug [status|cases|select <id>|close <cause>|unreproducible <why>]",
                 allow_prompt, ok=False)
