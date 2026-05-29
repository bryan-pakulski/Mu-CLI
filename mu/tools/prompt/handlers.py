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
        "Ask the user a multiple-choice question and BLOCK until they "
        "pick (or cancel). Renders a live full-screen picker; the user "
        "navigates with arrow keys and submits with Enter. When "
        "multi_select=true the picker shows checkboxes and the user can "
        "toggle any combination with Space (or `a` / `n` for all / none).\n"
        "\n"
        "Set allow_other=true when you're asking a CLARIFYING question "
        "and the listed options might not cover every case. The picker "
        "appends an extra 'Other (type your own)…' entry; picking it "
        "opens a free-form text prompt. The returned `other_text` field "
        "captures that prose answer.\n"
        "\n"
        "Use this WHENEVER:\n"
        "  • Teacher mode quiz: 2–6 plausible answers, want the learner "
        "to pick. Set multi_select=true for 'select all that apply'.\n"
        "  • Disambiguation: the user said 'edit the auth code' and three "
        "files plausibly qualify — ask them to pick which. Set "
        "allow_other=true so they can name a different file if you "
        "missed it.\n"
        "  • Confirming a path-of-action: the user asked for a refactor "
        "with multiple reasonable approaches — surface 2–4 named options "
        "instead of free-form prose. allow_other=true gives them an "
        "escape hatch.\n"
        "\n"
        "Do NOT use for pure free-form input — open-ended questions "
        "belong in regular chat. Do NOT use for binary yes/no when a "
        "simple clarifying sentence works.\n"
        "\n"
        "The result is `{\"selected\": [...labels], \"other_text\": str, "
        "\"cancelled\": bool}`. When cancelled (Esc / Ctrl+C / blank), "
        "both `selected` and `other_text` are empty — interpret that as "
        "'the user wants to opt out of the picker; follow up in plain "
        "chat'. When `other_text` is non-empty, treat it as the user's "
        "authoritative answer alongside (or instead of) `selected`."
    ),
    parameters={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": (
                    "The headline question, shown bold at the top of the "
                    "picker. Keep it under ~80 chars; expand context via "
                    "`description`."
                ),
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "The choices, in display order. 2–8 is the sweet spot; "
                    "more than ~10 and the user will skim past them."
                ),
            },
            "multi_select": {
                "type": "boolean",
                "default": False,
                "description": (
                    "When true, the picker is select-all-that-apply: the "
                    "user toggles any subset with Space and submits with "
                    "Enter. When false (default), the user picks exactly "
                    "one option."
                ),
            },
            "allow_other": {
                "type": "boolean",
                "default": False,
                "description": (
                    "When true, append an 'Other (type your own)…' entry. "
                    "Picking it opens a follow-up text prompt; the prose "
                    "answer comes back in `other_text`. Strongly recommended "
                    "for clarifying questions where you're not sure your "
                    "options cover the full space of plausible answers."
                ),
            },
            "description": {
                "type": "string",
                "description": (
                    "Optional additional context shown under the question. "
                    "Use for 'why I'm asking' framing — keep it brief."
                ),
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
        "Pin the user's top-level task into L3 of the system prompt for "
        "the current turn. Keeps you oriented across many iterations "
        "and the L2 conversation-summary compaction that happens "
        "mid-turn — the pinned goal survives both, while the original "
        "text in the user's first message may be summarized away.\n"
        "\n"
        "**LIFECYCLE**: the goal automatically clears at the END of the "
        "turn. Each new user message starts fresh — re-pin at the top "
        "if the next task is also multi-step. Don't carry over old "
        "goals; an unrelated next request shouldn't be biased by what "
        "was pinned before.\n"
        "\n"
        "Call this when:\n"
        "  • The user just stated a multi-step task ('refactor the auth "
        "layer', 'teach me Perl', 'audit this codebase for SQL "
        "injection'). Pin it immediately at the start of the turn so "
        "you don't drift across iterations.\n"
        "  • The user's focus shifts mid-turn (call again with the new "
        "text — replaces the previous goal). Rare — usually a new ask "
        "is a new turn.\n"
        "  • Pass `clear=true` to explicitly remove the pin before the "
        "turn ends. Rarely needed since the auto-clear handles the "
        "common case.\n"
        "\n"
        "The user can also set it manually with `/goal <text>` and "
        "inspect with `/goal show`. This tool gives YOU the same lever "
        "so a forgotten `/goal` isn't fatal. Goal text should be a "
        "concise one-line summary of the request — full sentences are "
        "fine but keep it ≤ ~200 chars for L3 budget."
    ),
    parameters={
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": (
                    "The concise one-line summary of the user's top-level "
                    "task. Required unless `clear=true`."
                ),
            },
            "clear": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Set to true to clear the pinned goal instead of "
                    "setting one. Ignores the `goal` field when true."
                ),
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
