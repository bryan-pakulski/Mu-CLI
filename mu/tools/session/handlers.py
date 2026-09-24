"""`search_history` `@tool` handler.

Lets the agent search the full conversation history — including messages
compacted behind the summary anchor — by keyword, role, tool name, and
time range. Returns ranked snippets with surrounding context and cache
key passthrough for ToolResultCache integration.

Read-only, session-scoped, plan-mode safe.
"""

import json
from typing import Any, Dict

from mu.tools import tool


@tool(
    name="search_history",
    description=(
        "Search full session history (including compacted pre-anchor messages) by keyword, role or tool name. Returns ranked snippets with context, message index, anchor flag and cache key. Use to recover past decisions or tool arguments no longer in active context."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Natural-language or keyword query. Case-insensitive "
                    "substring match across text, tool calls, tool results, "
                    "and file references."
                ),
            },
            "role": {
                "type": "string",
                "enum": ["user", "assistant"],
                "description": (
                    "Filter by message role. If omitted, searches all roles."
                ),
            },
            "tool_name": {
                "type": "string",
                "description": (
                    "Filter to messages containing a tool_call or tool_result "
                    "with this tool name. Case-insensitive substring match."
                ),
            },
            "include_summarized": {
                "type": "boolean",
                "description": (
                    "If True (default), search the full history including "
                    "pre-anchor (compacted) messages. If False, search only "
                    "active (post-anchor) messages."
                ),
                "default": True,
            },
            "context_messages": {
                "type": "integer",
                "description": (
                    "Number of context messages to include before and after "
                    "each hit (default 2). Clamped to history bounds."
                ),
                "default": 2,
            },
            "max_results": {
                "type": "integer",
                "description": (
                    "Maximum number of results to return (default 20). "
                    "If more matches exist, total_matches and has_more are set."
                ),
                "default": 20,
            },
        },
        "required": ["query"],
    },
    requires_approval=False,
    execution_kind="read",
    preview_policy="none",
    server_policy="session_only",
    result_mode="raw",
)
def search_history(args: Dict[str, Any], context) -> str:
    """Search conversation history via SessionManager.search_history()."""

    query = str(args.get("query", "") or "").strip()
    tool_name = str(args.get("tool_name") or "").strip()
    if not query and not tool_name:
        return json.dumps({
            "results": [],
            "total_matches": 0,
            "has_more": False,
            "error": "query is required (or provide tool_name to filter by tool)",
        })

    session = getattr(context, "session", None)
    if session is None or not hasattr(session, "session_manager"):
        return json.dumps({
            "results": [],
            "total_matches": 0,
            "has_more": False,
            "error": "No session available for history search.",
        })

    sm = session.session_manager

    if not sm.history:
        return json.dumps({
            "results": [],
            "total_matches": 0,
            "has_more": False,
            "message": "No conversation history in this session.",
        })

    role = args.get("role")
    # tool_name already extracted above for validation
    include_summarized = bool(args.get("include_summarized", True))
    context_messages = int(args.get("context_messages", 2) or 2)
    max_results = int(args.get("max_results", 20) or 20)

    results = sm.search_history(
        query=query,
        role=role,
        tool_name=tool_name,
        include_summarized=include_summarized,
        context_messages=context_messages,
        max_results=max_results,
    )

    return json.dumps(results, default=str, indent=2)


# ============================================================ context_status


@tool(
    name="context_status",
    description=(
        "Read-only report of per-layer token fill (L0-L5), todo/scratchpad/memory counts, staleness signals (l2_stale_vs_l5, stale_memory_count, stale_todos, memory_pressure_pct) and selectable tool result_ids (oldest first; page with candidate_offset). Use before big gathers and to decide checkpoint_progress / clear_tool_results / compact / memory curation."
    ),
    parameters={"type": "object", "properties": {
        "candidate_offset": {"type": "integer", "minimum": 0, "default": 0},
        "candidate_limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 30},
    }},
    requires_approval=False,
    execution_kind="read",
    preview_policy="none",
    server_policy="session_only",
    result_mode="raw",
)
def context_status(args: Dict[str, Any], context) -> str:
    """Per-layer token fill + self-management signals."""
    session = getattr(context, "session", None)
    if session is None or not hasattr(session, "session_manager"):
        return json.dumps({"error": "No session available."})

    from utils.runtime_metrics import collect_context_layers
    from mu.session.context_maintenance import context_status as working_context_status

    try:
        layers = collect_context_layers(session)
        working_context = working_context_status(
            session, candidate_offset=args.get("candidate_offset", 0),
            candidate_limit=args.get("candidate_limit", 30),
        )
    except Exception as exc:
        return json.dumps({"error": f"context layer collection failed: {exc}"})

    by_id = {entry["layer"]: entry for entry in layers}
    total = sum(int((by_id.get(lid, {}) or {}).get("current") or 0) for lid in by_id)
    context_limit = int(by_id.get("L5", {}).get("maximum") or 0)

    sm = session.session_manager
    history_len = len(getattr(sm, "history", []) or [])
    summary_anchor = int(getattr(sm, "summary_anchor", 0) or 0)
    checkpoint_anchor = int(getattr(sm, "_checkpoint_anchor", 0) or 0)
    # Entries added since the last L2 progress checkpoint. A large gap means
    # L2 is stale relative to the work sitting in L5 — the signal to call
    # checkpoint_progress.
    uncheckpointed = max(0, history_len - max(summary_anchor, checkpoint_anchor))
    l2_stale = uncheckpointed >= 12

    todo_count = sum(
        1 for e in session.turn_scratchpad.entries if "todo" in e.tags
    ) if hasattr(session, "turn_scratchpad") else 0
    scratch_count = sum(
        1 for e in session.turn_scratchpad.entries if "todo" not in e.tags
    ) if hasattr(session, "turn_scratchpad") else 0

    # Staleness signals — the actionable pruning targets. The agent retires
    # stale memory and clears stale todos instead of letting dead state
    # accumulate and drown out what matters. See the SELF-MANAGEMENT block.
    memory_entries = []
    if hasattr(session, "task_memory") and session.task_memory is not None:
        memory_entries = session.task_memory.entries
    memory_count = len(memory_entries)
    stale_memory_count = sum(1 for e in memory_entries if e.status == "stale")
    active_memory_count = sum(1 for e in memory_entries if e.status == "active")

    stale_todos = 0
    in_progress_todos = 0
    if hasattr(session, "turn_scratchpad"):
        for e in session.turn_scratchpad.entries:
            if "todo" not in e.tags:
                continue
            if any(t == "status:completed" for t in e.tags):
                stale_todos += 1
            elif any(t == "status:in_progress" for t in e.tags):
                in_progress_todos += 1

    memory_max = int(getattr(session.task_memory, "max_entries", 0)) if memory_entries else 0
    memory_pressure = round(100.0 * memory_count / memory_max, 1) if memory_max > 0 else 0.0

    layer_rows = []
    for entry in layers:
        cur = int(entry.get("current") or 0)
        mx = int(entry.get("maximum") or 0)
        layer_rows.append({
            "layer": entry.get("layer"),
            "name": entry.get("name"),
            "tokens": cur,
            "max": mx,
            "fill_pct": round(100.0 * cur / mx, 1) if mx > 0 else 0.0,
        })

    return json.dumps({
        "layers": layer_rows,
        "working_context": working_context,
        "total_tokens": total,
        "context_limit": context_limit,
        "fill_pct": round(100.0 * total / context_limit, 1) if context_limit > 0 else 0.0,
        "self_management": {
            "uncheckpointed_entries": uncheckpointed,
            "l2_stale_vs_l5": l2_stale,
            "todo_count": todo_count,
            "scratchpad_notes": scratch_count,
            "memory_entries": memory_count,
            "active_memory": active_memory_count,
            "stale_memory_count": stale_memory_count,
            "stale_todos": stale_todos,
            "in_progress_todos": in_progress_todos,
            "memory_pressure_pct": memory_pressure,
        },
    }, default=str, indent=2)


# ================================================================ compact


@tool(
    name="compact",
    description=(
        "Summarize older completed history into L2 and advance the summary anchor (unlike checkpoint_progress). Recent/protected results and [cache:KEY] tags stay recallable. Prefer clear_tool_results first (no summarizer cost). Optional focus, through_index (last eligible history index), preserve_result_ids, clear_result_ids, and a structured checkpoint. Returns token estimates and the new anchor."
    ),
    parameters={
        "type": "object",
        "properties": {
            "focus": {
                "type": "string",
                "description": (
                    "Optional short text steering what the summary preserves "
                    "(e.g. 'the auth refactor and open decisions')."
                ),
            },
            "through_index": {"type": "integer", "minimum": 0},
            "preserve_result_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 100},
            "clear_result_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 100},
            "checkpoint": {
                "type": "object", "additionalProperties": False,
                "properties": {field: {"type": "string"} for field in
                               ("progress", "decisions", "resume", "exceptions", "next_action", "constraints")},
            },
        },
    },
    requires_approval=False,
    execution_kind="mutate",
    preview_policy="none",
    server_policy="session_only",
    result_mode="raw",
)
def compact(args: Dict[str, Any], context) -> str:
    """Manual history compaction — back end shared with the /compact command."""
    session = getattr(context, "session", None)
    if session is None or not hasattr(session, "session_manager"):
        return json.dumps({"error": "No session available."})

    from mu.agent.compactor import manual_compact

    focus = str(args.get("focus", "") or "").strip()
    result = manual_compact(session, focus=focus, checkpoint=args.get("checkpoint"),
                            preserve_result_ids=args.get("preserve_result_ids"),
                            clear_result_ids=args.get("clear_result_ids"),
                            through_index=args.get("through_index"))
    return json.dumps(result, default=str, indent=2)


@tool(
    name="clear_tool_results",
    description=(
        "Remove (action=clear), pin (keep) or restore named completed tool payloads in active context with zero summarizer cost; result_ids come from context_status. Originals stay recallable; recent results, failures and provider-signed content cannot be cleared. Optional structured checkpoint. Use before compact; never redo a write to recover its old output."
    ),
    parameters={"type": "object", "properties": {
        "result_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 100},
        "action": {"type": "string", "enum": ["clear", "keep", "restore"], "default": "clear"},
        "checkpoint": {"type": "object", "additionalProperties": False,
                       "properties": {field: {"type": "string"} for field in
                                      ("progress", "decisions", "resume", "exceptions", "next_action", "constraints")}},
    }, "required": ["result_ids"]},
    requires_approval=False, execution_kind="mutate", preview_policy="none",
    server_policy="session_only", result_mode="raw",
)
def clear_tool_results(args: Dict[str, Any], context) -> str:
    from mu.session.context_maintenance import edit_tool_results
    session = getattr(context, "session", None)
    if session is None or not hasattr(session, "session_manager"):
        return json.dumps({"ok": False, "error": "No session available."})
    try:
        result = edit_tool_results(session, args.get("result_ids"),
                                   action=args.get("action", "clear"), checkpoint=args.get("checkpoint"))
    except ValueError as exc:
        result = {"ok": False, "error": str(exc)}
    return json.dumps(result, default=str)


# ============================================================ checkpoint_progress


@tool(
    name="checkpoint_progress",
    description=(
        "Fold recent history into the L2 summary WITHOUT advancing the anchor (entries stay verbatim in L5). Use when context_status shows L2 stale vs L5. No-op below min_new_entries (default 6)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "min_new_entries": {
                "type": "integer",
                "description": (
                    "Minimum new history entries since the last checkpoint "
                    "required to actually fold (default 6). Below this the "
                    "call is a no-op — avoids burning a provider call for a "
                    "tiny update."
                ),
                "default": 6,
            },
        },
    },
    requires_approval=False,
    execution_kind="memory",
    preview_policy="none",
    server_policy="session_only",
    result_mode="json",
)
def checkpoint_progress(args: Dict[str, Any], context) -> Dict[str, Any]:
    """Agent-callable wrapper around HistoryMixin.force_progress_checkpoint."""
    session = getattr(context, "session", None)
    if session is None or not hasattr(session, "session_manager"):
        return {
            "ok": False,
            "error_code": "no_session",
            "message": "No session available for checkpoint.",
            "data": {},
            "artifacts": [],
            "telemetry": {"tool_name": "checkpoint_progress"},
        }
    try:
        min_new = int(args.get("min_new_entries", 6) or 6)
    except (TypeError, ValueError):
        min_new = 6
    sm = session.session_manager
    before = len(getattr(sm, "history", []) or [])
    try:
        updated = sm.force_progress_checkpoint(
            getattr(session, "provider", None),
            min_new_entries=min_new,
        )
    except Exception as exc:  # defensive — never break the turn on a checkpoint failure
        return {
            "ok": False,
            "error_code": "checkpoint_failed",
            "message": f"checkpoint_progress failed: {exc}",
            "data": {"updated": False},
            "artifacts": [],
            "telemetry": {"tool_name": "checkpoint_progress"},
        }
    return {
        "ok": True,
        "error_code": None,
        "message": (
            "L2 conversation summary refreshed with recent progress."
            if updated
            else "Not enough new work since the last checkpoint — no change."
        ),
        "data": {
            "updated": bool(updated),
            "history_len": before,
        },
        "artifacts": [],
        "telemetry": {"tool_name": "checkpoint_progress"},
    }
