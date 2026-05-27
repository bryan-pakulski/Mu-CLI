"""Handlers for agent-facing user-interaction tools.

The refinement + collaboration surface — tools the agent uses to drill
down on what the user actually wants, surface its work for review, and
hand control back at decision points. Lock requirements with pickers
instead of free-flowing chat; surface artifacts (diffs) instead of
narrating them.

Currently:

  Refinement (lock requirements before acting):
    * `ask_user_choice` — single multiple-choice picker.
    * `request_text` — single short-text input.
    * `gather_requirements` — multi-field form (N decisions, 1 flow).

  Review (verify artifacts before/after they exist):
    * `propose_change` — show a diff to the user; apply on approval.
                        Replaces silent write_file/apply_diff for
                        user-visible changes.

  Hand-off:
    * `propose_stopping_point` — for open-ended tasks: surface what's
                                 done + possible follow-ups; user
                                 picks stop or next.
    * `set_session_goal` — pin the user's top-level task so it
                            survives history compaction.

Together these replace verbose "let me ask you a bunch of questions"
chat with structured, blocking pickers AND replace "trust my summary"
narration with reviewable artifacts. One picker beats five chat
round-trips; one approved diff beats a paragraph of prose claiming the
change is correct.
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


@tool(
    name="request_text",
    description=(
        "Ask the user for a SHORT free-form text answer and BLOCK until "
        "they reply. The picker prints the prompt and reads one line "
        "of input. Use whenever you need a free-form value that no "
        "picker can enumerate — a filename, an identifier, a one-line "
        "spec, a URL, a numeric input.\n"
        "\n"
        "Use this INSTEAD OF asking in chat and waiting for the user's "
        "next message. Picker round-trips are atomic — chat round-trips "
        "are 1-3 messages of overhead.\n"
        "\n"
        "Examples:\n"
        "  • 'What should I name the new module?' (default='kalman.py')\n"
        "  • 'Paste the failing command line.'\n"
        "  • 'One-line summary of the bug.'\n"
        "\n"
        "Do NOT use for long-form prose — the picker is single-line. "
        "Do NOT use when 2-8 plausible answers exist — `ask_user_choice` "
        "is faster.\n"
        "\n"
        "Result: `{\"ok\": bool, \"value\": str, \"cancelled\": bool}`. "
        "When cancelled (blank / Ctrl+C) treat as 'user wants to opt out "
        "— follow up briefly in chat'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "The headline question. Keep it under ~80 chars.",
            },
            "default": {
                "type": "string",
                "description": (
                    "Optional pre-populated value the user can accept by "
                    "pressing Enter."
                ),
            },
        },
        "required": ["prompt"],
    },
    requires_approval=False,
    execution_kind="read",
)
def request_text_tool(args: dict[str, Any], context) -> str:
    prompt_text = str(args.get("prompt", "") or "").strip()
    default = args.get("default")
    default_str = str(default).strip() if default is not None else None

    if not prompt_text:
        return json.dumps(
            {
                "ok": False,
                "error": "request_text requires a `prompt` string.",
                "value": "",
                "cancelled": True,
            },
            indent=2,
        )

    ui = getattr(context, "ui", None)
    session = getattr(context, "session", None)
    if ui is None and session is not None:
        ui = getattr(session, "ui", None)

    if ui is None or not hasattr(ui, "prompt"):
        return json.dumps(
            {
                "ok": False,
                "error": (
                    "No interactive UI is attached — request_text can't "
                    "run. Ask the user in plain chat instead."
                ),
                "value": "",
                "cancelled": True,
            },
            indent=2,
        )

    try:
        raw = ui.prompt(prompt_text, default=default_str)
    except (KeyboardInterrupt, EOFError):
        return json.dumps(
            {"ok": True, "value": "", "cancelled": True},
            indent=2,
        )
    except Exception as exc:
        return json.dumps(
            {
                "ok": False,
                "error": f"request_text failed: {exc!s}",
                "value": "",
                "cancelled": True,
            },
            indent=2,
        )

    value = str(raw if raw is not None else "").strip()
    cancelled = value == ""
    return json.dumps(
        {"ok": True, "value": value, "cancelled": cancelled},
        indent=2,
    )


@tool(
    name="gather_requirements",
    description=(
        "Bulk-collect MULTIPLE decisions from the user in one cohesive "
        "flow. Use AT THE START of any non-trivial task to lock down "
        "what they actually want before you act. Each field becomes "
        "a picker (`kind=choice`) or a text prompt (`kind=text`) in "
        "sequence — the user answers all of them, you get one dict "
        "back. One tool call replaces N rounds of chat clarification.\n"
        "\n"
        "Strong default: when a task needs ≥ 2 clarifications, use "
        "this instead of asking serially in chat.\n"
        "\n"
        "Example:\n"
        "  fields=[\n"
        "    {key: 'language', label: 'Target language?',\n"
        "     kind: 'choice', options: ['python','rust','go']},\n"
        "    {key: 'tests',    label: 'Test framework?',\n"
        "     kind: 'choice', options: ['pytest','unittest']},\n"
        "    {key: 'name',     label: 'Module filename?',\n"
        "     kind: 'text',   default: 'mymodule.py'},\n"
        "  ]\n"
        "→ result.answers = {language: 'rust', tests: 'pytest', name: ...}\n"
        "\n"
        "Result: `{ok, answers: {key: value}, cancelled: bool, "
        "skipped_keys: [...]}`. Cancelling mid-form returns whatever "
        "was already answered. Skipped/cancelled fields are listed in "
        "`skipped_keys`."
    ),
    parameters={
        "type": "object",
        "properties": {
            "headline": {
                "type": "string",
                "description": (
                    "One-line framing shown before the first field — "
                    "'Before I start the refactor, lock these down:'"
                ),
            },
            "fields": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {
                            "type": "string",
                            "description": "Identifier for the answer in the result dict.",
                        },
                        "label": {
                            "type": "string",
                            "description": "The question for this field.",
                        },
                        "kind": {
                            "type": "string",
                            "enum": ["choice", "text"],
                            "description": "'choice' for pickers, 'text' for free-form input.",
                        },
                        "options": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Required when kind=choice.",
                        },
                        "multi_select": {
                            "type": "boolean",
                            "default": False,
                            "description": "kind=choice only — allow multiple selections.",
                        },
                        "allow_other": {
                            "type": "boolean",
                            "default": False,
                            "description": "kind=choice only — append 'Other (type your own)' option.",
                        },
                        "default": {
                            "type": "string",
                            "description": "kind=text only — pre-populated value.",
                        },
                    },
                    "required": ["key", "label", "kind"],
                },
            },
        },
        "required": ["fields"],
    },
    requires_approval=False,
    execution_kind="read",
)
def gather_requirements_tool(args: dict[str, Any], context) -> str:
    headline = str(args.get("headline", "") or "").strip()
    fields = args.get("fields") or []
    if not fields:
        return json.dumps(
            {
                "ok": False,
                "error": "gather_requirements requires at least one field.",
                "answers": {},
                "cancelled": True,
            },
            indent=2,
        )

    ui = getattr(context, "ui", None)
    session = getattr(context, "session", None)
    if ui is None and session is not None:
        ui = getattr(session, "ui", None)

    if ui is None:
        return json.dumps(
            {
                "ok": False,
                "error": (
                    "No interactive UI is attached — gather_requirements "
                    "can't run. Ask the user in plain chat instead."
                ),
                "answers": {},
                "cancelled": True,
            },
            indent=2,
        )

    if headline and hasattr(ui, "show_info"):
        try:
            ui.show_info(headline)
        except Exception:
            pass

    answers: dict[str, Any] = {}
    skipped: list[str] = []

    for raw_field in fields:
        if not isinstance(raw_field, dict):
            continue
        key = str(raw_field.get("key", "") or "").strip()
        label = str(raw_field.get("label", "") or "").strip()
        kind = str(raw_field.get("kind", "") or "").strip()
        if not key or not label or kind not in {"choice", "text"}:
            skipped.append(key or "<unnamed>")
            continue

        if kind == "choice":
            options = [str(o).strip() for o in (raw_field.get("options") or []) if str(o).strip()]
            if not options:
                skipped.append(key)
                continue
            multi_select = bool(raw_field.get("multi_select", False))
            allow_other = bool(raw_field.get("allow_other", False))
            if not hasattr(ui, "ask_user_choice"):
                skipped.append(key)
                continue
            try:
                result = ui.ask_user_choice(
                    label,
                    options,
                    multi_select=multi_select,
                    allow_other=allow_other,
                )
            except (KeyboardInterrupt, EOFError, NotImplementedError):
                skipped.append(key)
                return json.dumps(
                    {
                        "ok": True,
                        "answers": answers,
                        "cancelled": True,
                        "skipped_keys": skipped,
                    },
                    indent=2,
                )
            except Exception as exc:
                skipped.append(key)
                return json.dumps(
                    {
                        "ok": False,
                        "error": f"gather_requirements/{key}: {exc!s}",
                        "answers": answers,
                        "cancelled": True,
                        "skipped_keys": skipped,
                    },
                    indent=2,
                )
            if not isinstance(result, dict) or result.get("cancelled"):
                skipped.append(key)
                continue
            selected = list(result.get("selected") or [])
            other_text = str(result.get("other_text", "") or "").strip()
            if multi_select:
                answers[key] = selected + ([other_text] if other_text else [])
            else:
                if other_text:
                    answers[key] = other_text
                elif selected:
                    answers[key] = selected[0]
                else:
                    skipped.append(key)
            continue

        # kind == "text"
        default = raw_field.get("default")
        default_str = str(default).strip() if default is not None else None
        if not hasattr(ui, "prompt"):
            skipped.append(key)
            continue
        try:
            raw = ui.prompt(label, default=default_str)
        except (KeyboardInterrupt, EOFError):
            skipped.append(key)
            return json.dumps(
                {
                    "ok": True,
                    "answers": answers,
                    "cancelled": True,
                    "skipped_keys": skipped,
                },
                indent=2,
            )
        except Exception as exc:
            skipped.append(key)
            return json.dumps(
                {
                    "ok": False,
                    "error": f"gather_requirements/{key}: {exc!s}",
                    "answers": answers,
                    "cancelled": True,
                    "skipped_keys": skipped,
                },
                indent=2,
            )
        value = str(raw if raw is not None else "").strip()
        if not value:
            skipped.append(key)
        else:
            answers[key] = value

    return json.dumps(
        {
            "ok": True,
            "answers": answers,
            "cancelled": False,
            "skipped_keys": skipped,
        },
        indent=2,
    )


@tool(
    name="propose_change",
    description=(
        "Show a proposed file change to the user, BLOCK for approval, "
        "and APPLY ON APPROVAL. Use INSTEAD OF `write_file` / "
        "`apply_diff` whenever the change is user-visible (touches "
        "code, config, docs, scripts the user is working on). Reserve "
        "the bare writers for throwaway scaffolding (test fixtures, "
        "temp files, .pyc cleanup) where review would be noise.\n"
        "\n"
        "The tool reads the current file, renders a side-by-side diff "
        "against your proposed content, then asks the user to "
        "approve / reject / request revision. On approval the new "
        "content is written to disk. On rejection nothing changes. On "
        "revision the user types a one-line note explaining what they "
        "want different; the note comes back so you can iterate.\n"
        "\n"
        "Kinds:\n"
        "  • `edit` (default): file exists, `after` is the FULL new "
        "content. The tool computes and shows the diff against current "
        "disk state.\n"
        "  • `new`: file does not exist yet, `after` is the file's "
        "initial content. The tool shows the whole content as an "
        "addition.\n"
        "  • `delete`: confirm removal. `after` is ignored; the tool "
        "shows the file's current content as what will be lost.\n"
        "\n"
        "Always include `rationale` — one line of why the change is "
        "needed. The picker shows it alongside the diff.\n"
        "\n"
        "Result: `{ok, applied: bool, file, kind, revision_request: "
        "str | null}`. When `applied=true` the change is on disk. When "
        "`applied=false` and `revision_request` is set, the user wants "
        "you to revise; re-call propose_change with a new `after` "
        "addressing their note. When both are empty/false, the user "
        "rejected — pause and ask what they'd prefer."
    ),
    parameters={
        "type": "object",
        "properties": {
            "file": {
                "type": "string",
                "description": "Path to the file. Required for all kinds.",
            },
            "after": {
                "type": "string",
                "description": (
                    "Full new file content (kind='edit' or 'new'). "
                    "Ignored when kind='delete'."
                ),
            },
            "rationale": {
                "type": "string",
                "description": (
                    "One-line why. Shown to the user alongside the diff."
                ),
            },
            "kind": {
                "type": "string",
                "enum": ["edit", "new", "delete"],
                "default": "edit",
            },
        },
        "required": ["file", "rationale"],
    },
    requires_approval=False,
    execution_kind="mutate",
)
def propose_change_tool(args: dict[str, Any], context) -> str:
    import os as _os

    file_path = str(args.get("file", "") or "").strip()
    after = str(args.get("after", "") or "")
    rationale = str(args.get("rationale", "") or "").strip()
    kind = str(args.get("kind", "edit") or "edit").strip()

    if not file_path:
        return json.dumps({"ok": False, "error": "file is required", "applied": False}, indent=2)
    if not rationale:
        return json.dumps(
            {
                "ok": False,
                "error": "rationale is required — one-line why.",
                "applied": False,
            },
            indent=2,
        )
    if kind not in {"edit", "new", "delete"}:
        return json.dumps(
            {"ok": False, "error": f"unknown kind {kind!r}", "applied": False},
            indent=2,
        )

    exists = _os.path.exists(file_path)
    if kind == "new" and exists:
        return json.dumps(
            {
                "ok": False,
                "error": f"file {file_path!r} already exists — use kind='edit'",
                "applied": False,
            },
            indent=2,
        )
    if kind in {"edit", "delete"} and not exists:
        return json.dumps(
            {
                "ok": False,
                "error": f"file {file_path!r} does not exist — use kind='new'",
                "applied": False,
            },
            indent=2,
        )

    original = ""
    if exists:
        try:
            with open(file_path, "r", encoding="utf-8") as handle:
                original = handle.read()
        except OSError as exc:
            return json.dumps(
                {
                    "ok": False,
                    "error": f"could not read {file_path}: {exc}",
                    "applied": False,
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
                    "No interactive UI is attached — propose_change can't "
                    "block for approval. Apply the change via write_file/"
                    "apply_diff if the user already authorized this, or "
                    "surface the diff in chat."
                ),
                "applied": False,
            },
            indent=2,
        )

    # Render the diff. show_diff is a polish layer — failure shouldn't
    # block the approval flow.
    diff_view = original if kind == "delete" else after
    if hasattr(ui, "show_diff"):
        try:
            if kind == "delete":
                ui.show_diff(file_path, original, "")
            elif kind == "new":
                ui.show_diff(file_path, "", after)
            else:
                ui.show_diff(file_path, original, after)
        except Exception:
            pass

    options = ["Approve and apply", "Reject", "Request revision"]
    try:
        result = ui.ask_user_choice(
            f"{kind.upper()} {file_path}",
            options,
            multi_select=False,
            description=rationale,
        )
    except (KeyboardInterrupt, EOFError, NotImplementedError):
        return json.dumps(
            {
                "ok": True,
                "applied": False,
                "file": file_path,
                "kind": kind,
                "revision_request": None,
                "cancelled": True,
            },
            indent=2,
        )
    except Exception as exc:
        return json.dumps(
            {
                "ok": False,
                "error": f"approval prompt failed: {exc!s}",
                "applied": False,
                "file": file_path,
                "kind": kind,
            },
            indent=2,
        )

    if not isinstance(result, dict):
        result = {"selected": [], "cancelled": True}
    selected = list(result.get("selected") or [])
    choice = selected[0] if selected else ""

    if choice == "Reject" or result.get("cancelled"):
        return json.dumps(
            {
                "ok": True,
                "applied": False,
                "file": file_path,
                "kind": kind,
                "revision_request": None,
            },
            indent=2,
        )

    if choice == "Request revision":
        revision_note = ""
        if hasattr(ui, "prompt"):
            try:
                raw = ui.prompt(
                    f"What needs to change in this {kind} of {file_path}?",
                    default=None,
                )
                revision_note = str(raw or "").strip()
            except Exception:
                revision_note = ""
        return json.dumps(
            {
                "ok": True,
                "applied": False,
                "file": file_path,
                "kind": kind,
                "revision_request": revision_note or "(no note)",
            },
            indent=2,
        )

    # Approved → apply.
    try:
        if kind == "delete":
            _os.remove(file_path)
        else:
            dirname = _os.path.dirname(file_path)
            if dirname:
                _os.makedirs(dirname, exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as handle:
                handle.write(after)
    except OSError as exc:
        return json.dumps(
            {
                "ok": False,
                "error": f"approved but apply failed: {exc}",
                "applied": False,
                "file": file_path,
                "kind": kind,
            },
            indent=2,
        )

    # Best-effort folder_context refresh so retrieval indexes notice.
    folder_context = getattr(context, "folder_context", None)
    if folder_context is None and session is not None:
        folder_context = getattr(session, "folder_context", None)
    if folder_context is not None and hasattr(folder_context, "track_file") and kind != "delete":
        try:
            folder_context.track_file(file_path)
        except Exception:
            pass

    return json.dumps(
        {
            "ok": True,
            "applied": True,
            "file": file_path,
            "kind": kind,
            "revision_request": None,
        },
        indent=2,
    )


@tool(
    name="propose_stopping_point",
    description=(
        "For OPEN-ENDED tasks: surface what's done so far and the "
        "possible follow-ups, then BLOCK for the user to pick stop or "
        "next. Use when the user's original ask is vague ('clean this "
        "up', 'improve this module', 'add tests') and you've delivered "
        "the core thing — without a stopping signal you'll either "
        "wander into scope creep or stop too early.\n"
        "\n"
        "Required for any task where the work is genuinely open-ended. "
        "Do NOT use for tasks with a clear definition of done (passing "
        "test, specific bug fix) — those finish by themselves.\n"
        "\n"
        "Pattern: state `done` in past tense ('refactored auth.py to "
        "use JWT'), list 2-5 `could_also` items the user might also "
        "want, and optionally include a `recommendation` ('stop' or "
        "one of the could_also items) so the picker pre-highlights "
        "your suggestion.\n"
        "\n"
        "Result: `{ok, choice, cancelled}` where `choice` is one of "
        "the could_also strings or `'stop'`. Treat `cancelled=true` as "
        "stop."
    ),
    parameters={
        "type": "object",
        "properties": {
            "done": {
                "type": "string",
                "description": "What you accomplished — one short line, past tense.",
            },
            "could_also": {
                "type": "array",
                "items": {"type": "string"},
                "description": "2-5 possible follow-up actions. Concrete; not 'and more'.",
            },
            "recommendation": {
                "type": "string",
                "description": (
                    "Your suggested next step: 'stop' or one of the "
                    "could_also strings. Shown as the picker's default."
                ),
            },
        },
        "required": ["done", "could_also"],
    },
    requires_approval=False,
    execution_kind="read",
)
def propose_stopping_point_tool(args: dict[str, Any], context) -> str:
    done = str(args.get("done", "") or "").strip()
    could_also = [str(c).strip() for c in (args.get("could_also") or []) if str(c).strip()]
    recommendation = str(args.get("recommendation", "") or "").strip()

    if not done:
        return json.dumps(
            {"ok": False, "error": "done is required", "choice": "", "cancelled": True},
            indent=2,
        )
    if not could_also:
        return json.dumps(
            {
                "ok": False,
                "error": "could_also must list at least one follow-up; empty list means just say so in chat.",
                "choice": "",
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
                    "No interactive UI is attached — propose_stopping_point "
                    "can't block. Surface 'done X / could also Y, Z' in chat."
                ),
                "choice": "",
                "cancelled": True,
            },
            indent=2,
        )

    options = ["Stop here"] + could_also
    description = f"Done: {done}"
    if recommendation:
        description += f"\nRecommendation: {recommendation}"

    try:
        result = ui.ask_user_choice(
            "What next?",
            options,
            multi_select=False,
            description=description,
        )
    except (KeyboardInterrupt, EOFError, NotImplementedError):
        return json.dumps(
            {"ok": True, "choice": "stop", "cancelled": True},
            indent=2,
        )
    except Exception as exc:
        return json.dumps(
            {
                "ok": False,
                "error": f"prompt failed: {exc!s}",
                "choice": "",
                "cancelled": True,
            },
            indent=2,
        )

    if not isinstance(result, dict):
        result = {"selected": [], "cancelled": True}
    if result.get("cancelled"):
        return json.dumps(
            {"ok": True, "choice": "stop", "cancelled": True},
            indent=2,
        )
    selected = list(result.get("selected") or [])
    pick = selected[0] if selected else "Stop here"
    canonical = "stop" if pick == "Stop here" else pick
    return json.dumps(
        {"ok": True, "choice": canonical, "cancelled": False},
        indent=2,
    )


__all__ = [
    "ask_user_choice_tool",
    "gather_requirements_tool",
    "propose_change_tool",
    "propose_stopping_point_tool",
    "request_text_tool",
    "set_session_goal_tool",
]
