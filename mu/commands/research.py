"""Research slash command: /research status|sources|<query>.

Bare `/research <query>` flips the session into research mode and sends
the query as a turn. `/research status` and `/research sources` are
diagnostics that don't trigger a model call.
"""

from typing import Any

from . import CommandResult, command


def _refresh_hud(session: Any) -> None:
    try:
        from mucli import refresh_memory_hud

        refresh_memory_hud(session, getattr(session, "ui", None))
    except ImportError:
        pass


def _research_helpers():
    """Lazy import the research helpers from mucli — they live there
    because they touch globals (the citation registry et al.)."""
    try:
        from mucli import _extract_recent_sources, _research_tool_names

        return _extract_recent_sources, _research_tool_names
    except ImportError:
        return (lambda *a, **k: []), (lambda *a, **k: [])


@command(
    "/research",
    help="Research workflow: /research status, /research sources, or /research <query>.",
)
def research_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    raw = (args or "").strip()
    sub = raw.lower()

    extract, tool_names = _research_helpers()

    if sub in ("status", ""):
        active_mode = str(session.variables.get("agent_mode", "default"))
        return CommandResult(
            ok=True,
            message="Research status snapshot.",
            data={
                "current_mode": active_mode,
                "available_tools": tool_names(),
                "recent_sources": extract(session.session_manager.history, limit=6),
                "citation_policy": "When researching, include source URLs and cite claims.",
            },
        )

    if sub == "sources":
        return CommandResult(
            ok=True,
            message="Collected recent research sources.",
            data={"sources": extract(session.session_manager.history, limit=20)},
        )

    # Treat anything else as the query body — flip into research mode and send.
    if not raw:
        return CommandResult(
            ok=False,
            message="Usage: /research <status|sources|query>",
        )

    session.variables["agent_mode"] = "research"
    fc = getattr(session, "folder_context", None)
    session.session_manager.save_history(fc)
    _refresh_hud(session)

    prompt = (
        "Research request:\n"
        f"{raw}\n\n"
        "Requirements:\n"
        "- Prefer primary/official sources when possible.\n"
        "- Include explicit source URLs.\n"
        "- Clearly separate facts vs inference.\n"
    )
    send_result = session.send_message(prompt)
    return CommandResult(
        ok=bool(send_result.get("ok", True)),
        message="Executed research query.",
        data={"query": raw, "send_result": send_result},
    )
