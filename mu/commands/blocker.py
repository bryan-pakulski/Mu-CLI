"""``/blocker`` — the session's blockers (raised by the agent with raise_blocker).

    /blocker                       — list open blockers
    /blocker resolve <id> [answer] — resolve one (your next chat message also resolves them)
"""

from __future__ import annotations

from typing import Any

from . import CommandResult, command
from ._mode_util import reply, save


@command("/blocker", "/blockers", help="List or resolve blockers the agent raised: /blocker [resolve <id> [answer]].")
def blocker_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    from mu.modes.blockers import blocker_state

    state = blocker_state(session)
    parts = (args or "").split(None, 2)
    sub = parts[0].lower() if parts else ""
    if sub in ("", "list"):
        open_ = state.open_blockers()
        if not open_:
            return reply(session, "No open blockers.", allow_prompt)
        lines = [f"{b.id} [{b.mode or 'any'}] {b.summary} — needs: {b.needs}" for b in open_]
        return reply(session, "\n".join(lines), allow_prompt, data={"open": [b.to_dict() for b in open_]})
    if sub == "resolve" and len(parts) >= 2:
        try:
            blocker = state.resolve(parts[1], parts[2] if len(parts) > 2 else "", actor="user:cli")
        except KeyError as exc:
            return reply(session, str(exc.args[0]), allow_prompt, ok=False)
        save(session)
        return reply(session, f"Resolved {blocker.id}: {blocker.summary}", allow_prompt)
    return reply(session, "Usage: /blocker [resolve <id> [answer]]", allow_prompt, ok=False)
