"""`/goal` — pin the user's top-level session ask so the model never
loses direction across long, compaction-heavy runs.

The pinned goal lives in `session.variables["session_goal"]` and is
rendered in L3 of every turn's system prompt (see
`mu.session.context.inject_hierarchical_context`). It is also mirrored
into `task_memory` with a `goal:locked` tag so compaction can never
erase the user's original ask.

Subcommands:
    /goal                      — show the current session goal
    /goal <text>               — set / replace the session goal
    /goal set <text>           — explicit form of the above
    /goal clear                — clear the pinned goal
    /goal show                 — show the current session goal (explicit)
    /goal help                 — print this help

The agent can self-pin via the `set_session_goal` tool when it detects
a multi-step task — useful when the user forgets to run /goal.
"""

from __future__ import annotations

from typing import Any

from . import CommandResult, command


_HELP_TEXT = """\
/goal — pin the top-level session ask so it survives history compaction.

  /goal                Show the current session goal.
  /goal <text>         Set / replace the session goal.
  /goal set <text>     Explicit set form.
  /goal clear          Clear the pinned goal.
  /goal show           Show the current session goal.
  /goal help           This help.

The pinned goal renders in L3 of every turn's system prompt across all
modes (default, debug, feature, research, loop, security, teacher), so
the model retains direction even after long runs roll history into the
L2 summary and the original wording is lost. The goal is also mirrored
into task_memory with a `goal:locked` tag for durable audit.

The agent can also self-pin with the `set_session_goal` tool when it
detects a multi-step task — handy if you forget to set one manually.
"""


def _ui(session: Any):
    return getattr(session, "ui", None)


def _print(session: Any, message: str, *, style: str | None = None) -> None:
    if not message:
        return
    ui = _ui(session)
    if ui is None:
        return
    console = getattr(ui, "console", None)
    if console is not None:
        try:
            if style:
                console.print(message, style=style, markup=False, highlight=False)
            else:
                console.print(message, markup=False, highlight=False)
            return
        except Exception:
            pass
    if hasattr(ui, "show_info"):
        try:
            ui.show_info(message)
        except Exception:
            pass


def _persist(session: Any) -> None:
    if hasattr(session, "session_manager") and hasattr(session, "folder_context"):
        try:
            session.session_manager.save_history(session.folder_context)
        except Exception:
            pass


def _set(session: Any, text: str) -> CommandResult:
    cleaned = (text or "").strip()
    if not cleaned:
        return CommandResult(
            ok=False,
            message="Usage: /goal <text>. Empty goal not allowed — use /goal clear instead.",
        )
    previous = str(session.variables.get("session_goal", "") or "").strip()
    session.variables["session_goal"] = cleaned
    _persist(session)
    # Eagerly mirror into task_memory so the durable audit trail
    # captures the goal even if the loop body doesn't run before the
    # next compaction.
    if hasattr(session, "_ensure_session_goal_persistence"):
        try:
            session._ensure_session_goal_persistence()
        except Exception:
            pass
    if previous and previous != cleaned:
        msg = (
            f"🎯 Session goal updated.\n"
            f"  Previous: {previous!r}\n"
            f"  Current:  {cleaned!r}"
        )
    else:
        msg = f"🎯 Session goal pinned: {cleaned!r}"
    return CommandResult(ok=True, message=msg, data={"session_goal": cleaned})


def _clear(session: Any) -> CommandResult:
    previous = str(session.variables.get("session_goal", "") or "").strip()
    if not previous:
        return CommandResult(ok=True, message="No session goal pinned.")
    session.variables["session_goal"] = ""
    _persist(session)
    return CommandResult(
        ok=True,
        message=f"🎯 Session goal cleared (was: {previous!r}).",
        data={"session_goal": ""},
    )


def _show(session: Any) -> CommandResult:
    current = str(session.variables.get("session_goal", "") or "").strip()
    if not current:
        return CommandResult(
            ok=True,
            message=(
                "No session goal pinned. Set one with `/goal <text>` to keep "
                "the model on track across long runs."
            ),
        )
    return CommandResult(
        ok=True,
        message=f"🎯 Session goal: {current!r}",
        data={"session_goal": current},
    )


def _help(session: Any) -> CommandResult:
    return CommandResult(ok=True, message=_HELP_TEXT)


@command(
    "/goal",
    help=(
        "Pin the top-level session ask so it survives history "
        "compaction. Usage: /goal <text>, /goal clear, /goal show. "
        "Renders in L3 of every turn across all modes."
    ),
)
def goal_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    raw = (args or "").strip()
    if not raw:
        result = _show(session)
    else:
        head, _, rest = raw.partition(" ")
        sub = head.lower()
        rest = rest.strip()
        if sub == "clear":
            result = _clear(session)
        elif sub == "show":
            result = _show(session)
        elif sub == "help":
            result = _help(session)
        elif sub == "set":
            result = _set(session, rest)
        else:
            # Bare `/goal <text>` (no `set` keyword) treats everything
            # as the goal text. This is the natural form for users who
            # don't want to type the verb.
            result = _set(session, raw)
    if allow_prompt:
        _print(session, result.message)
    return result
