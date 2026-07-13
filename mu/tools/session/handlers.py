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
        "Search the full session conversation history — including messages "
        "compacted behind the summary anchor — by keyword, role, tool name, "
        "or time range. Returns ranked matching conversation snippets with "
        "surrounding context, message index, anchor-awareness flag, and "
        "cache key for ToolResultCache integration. Use this to recover "
        "past decisions, tool call arguments, or context that is no longer "
        "in the active context window."
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
        "Report the live token fill of every context layer (L0 system "
        "prompt, L1 workspace, L1B skills, L2 conversation summary, L3 active "
        "goal, L4B retrieved snippets, L5 history) plus the todo/scratchpad/"
        "memory entry counts and STALENESS signals. Use this to self-manage "
        "your context: call checkpoint_progress when L2 is stale relative to "
        "L5 progress; RETIRE/ARCHIVE stale memory when `stale_memory_count` > 0 "
        "(those entries have decayed out of the active set and are noise); "
        "call `todo_clear('completed')` when `stale_todos` > 0; and curate "
        "memory before `memory_pressure_pct` forces a silent eviction. "
        "Read-only — call freely before big gathers or when a turn feels long."
    ),
    parameters={"type": "object", "properties": {}},
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

    try:
        layers = collect_context_layers(session)
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


# ============================================================ checkpoint_progress


@tool(
    name="checkpoint_progress",
    description=(
        "Fold recent conversation history into the L2 conversation summary "
        "WITHOUT compacting — the summary anchor does not advance and "
        "entries stay verbatim in L5. Call this when context_status shows "
        "L2 is stale relative to your L5 progress (lots of uncheckpointed "
        "entries) so the next iteration sees an up-to-date Progress / "
        "Current-state picture instead of re-deriving it. Returns whether "
        "L2 was updated and how many entries were folded. No-op if there "
        "isn't enough new work yet (default >= 6 entries)."
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