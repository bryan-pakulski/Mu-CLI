"""Handlers for `ask_user_choice`."""

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


__all__ = ["ask_user_choice_tool"]
