"""Handlers for agent-facing user-interaction tools.

  * `ask_user_choice` — multiple-choice picker (single or multi-select).
  * `set_session_goal` — pin the user's top-level task so it survives
                          history compaction.
"""

from __future__ import annotations

import json
from typing import Any

from mu.tools import tool


@tool(
    name="ask_user_choice",
    description=(
        "Show the user a multiple-choice picker and block until they choose or cancel. Use for disambiguation, confirming an approach, or quizzes (2-8 options); not for free-form input or trivial yes/no. multi_select=true allows any subset; allow_other=true adds a free-form 'Other' entry returned in other_text. Result: {selected: [labels], other_text: str, cancelled: bool}; cancelled means follow up in plain chat."
    ),
    parameters={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "Headline question (<~80 chars); put extra framing in `description`.",
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Choices in display order (2-8 ideal).",
            },
            "multi_select": {
                "type": "boolean",
                "default": False,
                "description": "Select-all-that-apply instead of exactly one.",
            },
            "allow_other": {
                "type": "boolean",
                "default": False,
                "description": "Append an 'Other (type your own)' entry; its prose answer returns in `other_text`.",
            },
            "description": {
                "type": "string",
                "description": "Optional brief context shown under the question.",
            },
        },
        "required": ["question", "options"],
    },
    requires_approval=False,
    execution_kind="read",
)
def ask_user_choice_tool(args: dict[str, Any], context) -> str:
    question = str(args.get("question", "") or "").strip()
    options = [str(o).strip() for o in (args.get("options") or []) if str(o).strip()]
    multi_select = bool(args.get("multi_select", False))
    allow_other = bool(args.get("allow_other", False))
    description = str(args.get("description", "") or "").strip()

    if not question:
        return json.dumps(
            {
                "ok": False,
                "error": "ask_user_choice requires a `question` string.",
                "selected": [],
                "cancelled": True,
            },
            indent=2,
        )
    if not options:
        return json.dumps(
            {
                "ok": False,
                "error": "ask_user_choice requires at least one option.",
                "selected": [],
                "cancelled": True,
            },
            indent=2,
        )

    ui = getattr(context, "ui", None)
    session = getattr(context, "session", None)
    if ui is None and session is not None:
        ui = getattr(session, "ui", None)

    if ui is None or not hasattr(ui, "ask_user_choice"):
        return json.dumps(
            {
                "ok": False,
                "error": (
                    "No interactive UI is attached — ask_user_choice can't "
                    "run. Ask the user in plain chat instead."
                ),
                "selected": [],
                "cancelled": True,
            },
            indent=2,
        )

    try:
        result = ui.ask_user_choice(
            question,
            options,
            multi_select=multi_select,
            description=description,
            allow_other=allow_other,
        )
    except NotImplementedError:
        return json.dumps(
            {
                "ok": False,
                "error": (
                    "The active UI doesn't support interactive choice "
                    "prompts. Ask the user in plain chat instead."
                ),
                "selected": [],
                "cancelled": True,
            },
            indent=2,
        )
    except Exception as exc:
        return json.dumps(
            {
                "ok": False,
                "error": f"ask_user_choice failed: {exc!s}",
                "selected": [],
                "cancelled": True,
            },
            indent=2,
        )

    if not isinstance(result, dict):
        result = {"selected": [], "other_text": "", "cancelled": True}
    selected = list(result.get("selected") or [])
    other_text = str(result.get("other_text", "") or "").strip()
    cancelled = bool(result.get("cancelled", False))
    return json.dumps(
        {
            "ok": True,
            "selected": selected,
            "other_text": other_text,
            "cancelled": cancelled,
            "multi_select": multi_select,
            "allow_other": allow_other,
            "option_count": len(options),
        },
        indent=2,
    )


@tool(
    name="set_session_goal",
    description=(
        "Pin the user's top-level task into L3 so it survives mid-turn compaction. Call at the start of any multi-step task (one concise line, <=~200 chars); call again to replace when focus shifts. Auto-clears at end of turn; pass clear=true to remove early."
    ),
    parameters={
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": "One-line summary of the user's top-level task. Required unless clear=true.",
            },
            "clear": {
                "type": "boolean",
                "default": False,
                "description": "Remove the pinned goal instead of setting one.",
            },
        },
    },
    requires_approval=False,
    execution_kind="mutate",
)
def set_session_goal_tool(args: dict[str, Any], context) -> str:
    session = getattr(context, "session", None)
    if session is None:
        return json.dumps(
            {
                "ok": False,
                "error": "set_session_goal requires an active session.",
            },
            indent=2,
        )
    clear = bool(args.get("clear", False))
    if clear:
        previous = str(session.variables.get("session_goal", "") or "").strip()
        session.variables["session_goal"] = ""
        # Mark the goal memory entry as done (audit trail retained).
        goal_id = getattr(session, "_active_goal_memory_id", None)
        if goal_id is not None:
            entry = session.task_memory.get_entry(goal_id)
            if entry is not None and getattr(entry, "status", "active") == "active":
                session.task_memory.update_status(goal_id, "done")
            session._active_goal_memory_id = None
        try:
            session.session_manager.save_history(session.folder_context)
        except Exception:
            pass
        return json.dumps(
            {
                "ok": True,
                "cleared": True,
                "previous_goal": previous,
            },
            indent=2,
        )
    goal = str(args.get("goal", "") or "").strip()
    if not goal:
        return json.dumps(
            {
                "ok": False,
                "error": (
                    "set_session_goal requires a non-empty `goal` string. "
                    "Pass clear=true if you mean to remove the pin."
                ),
            },
            indent=2,
        )
    previous = str(session.variables.get("session_goal", "") or "").strip()
    session.variables["session_goal"] = goal
    try:
        session.session_manager.save_history(session.folder_context)
    except Exception:
        pass
    # Mirror into task_memory immediately so the durable audit catches
    # the goal even if the loop body doesn't run between this tool call
    # and the next compaction.
    if hasattr(session, "_ensure_session_goal_persistence"):
        try:
            session._ensure_session_goal_persistence()
        except Exception:
            pass
    return json.dumps(
        {
            "ok": True,
            "goal": goal,
            "previous_goal": previous,
            "replaced": bool(previous and previous != goal),
        },
        indent=2,
    )


__all__ = [
    "ask_user_choice_tool",
    "set_session_goal_tool",
]
