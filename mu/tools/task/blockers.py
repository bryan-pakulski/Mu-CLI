"""``raise_blocker``: a core tool in every mode (modes v2 §3.4).

Records a ``Blocker`` in session state (``mu.modes.blockers``). The agent loop
treats it as an intentional pause: the loop watchdog and auto-continuation
stand down until the user resolves it (next chat message, the panel's Resolve,
or ``/blocker resolve``).
"""

from __future__ import annotations

from typing import Any, Dict

from mu.modes.blockers import blocker_state
from mu.tools import tool


@tool(
    name="raise_blocker",
    description=(
        "Pause and ask the user for something you cannot get yourself: a decision, "
        "credentials, missing requirements, an environment fix. Records the blocker "
        "for the panel and stops autonomous continuation until the user answers."
    ),
    parameters={
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "Short blocker summary shown to the user."},
            "requested_input": {
                "type": "string",
                "description": "The exact information or decision needed from the user.",
            },
            "details": {
                "type": "string",
                "description": "What is blocked and what has already been tried.",
            },
            "questions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional focused questions for the user to answer.",
            },
            "ref": {
                "type": "string",
                "description": "Optional id of the record this blocks (task, case, finding, lesson).",
            },
        },
        "required": ["summary", "requested_input"],
    },
    requires_approval=False,
    execution_kind="control",
    preview_policy="none",
    result_mode="structured",
    server_policy="session_only",
    summary_builder="blocker_summary",
)
def raise_blocker(args: Dict[str, Any], context) -> Dict[str, Any]:
    variables = getattr(context, "variables", None) or {}
    session = getattr(context, "session", None)
    if not variables and session is not None:
        variables = getattr(session, "variables", None) or {}
    mode = str(variables.get("agent_mode", "default") or "default").lower()
    questions = args.get("questions") or []
    if isinstance(questions, str):
        questions = [questions]
    try:
        blocker = blocker_state(context if session is not None else None).raise_blocker(
            str(args.get("summary") or ""),
            str(args.get("requested_input") or ""),
            mode=mode,
            ref=str(args.get("ref") or ""),
            details=str(args.get("details") or ""),
            questions=[str(q) for q in questions],
        )
    except ValueError as exc:
        return {"ok": False, "error_code": "invalid_args", "message": str(exc), "data": {},
                "artifacts": [], "telemetry": {"tool_name": "raise_blocker"}}
    if session is not None:
        session._loop_blocker_raised = True
    data = blocker.to_dict()
    data["kind"] = "blocker"
    data["requested_input"] = blocker.needs
    return {
        "ok": True,
        "error_code": None,
        "message": (
            f"Blocker {blocker.id} recorded: {blocker.summary}. Tell the user exactly what you "
            "need, then stop; their reply resolves it."
        ),
        "data": data,
        "artifacts": [],
        "telemetry": {"tool_name": "raise_blocker"},
    }


__all__ = ["raise_blocker"]
