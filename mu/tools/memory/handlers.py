"""Session memory + scratchpad `@tool` handlers.

The handlers route through `context.session.{task_memory,turn_scratchpad}`.
A fallback in-process store keeps unit tests that pass `session=None`
working without a full session scaffold.

Lifecycle tools (update_memory_status, supersede_memory, retire_memory,
reactivate_memory, archive_memory) mutate the status field on MemoryEntry
so the agent can distinguish active work from completed/superseded/
archived entries. All are session-scoped and plan-mode blocked (same as
save_memory).
"""

import json
from typing import Any, Dict, List, Optional

from mu.tools import tool


# ---------------------------------------------------------------- stores


def _task_memory(context):
    session = getattr(context, "session", None)
    if session is not None and hasattr(session, "task_memory"):
        return session.task_memory
    return _fallback_task_memory()


def _scratchpad(context):
    session = getattr(context, "session", None)
    if session is not None and hasattr(session, "turn_scratchpad"):
        return session.turn_scratchpad
    return _fallback_scratchpad()


_FALLBACK_TASK_MEMORY = None
_FALLBACK_SCRATCHPAD = None


def _fallback_task_memory():
    """Process-local TaskMemoryStore for session-less contexts.

    Only used by unit tests that build a `ToolExecutionContext` directly
    without a Session; the real REPL always has `context.session` set.
    """
    global _FALLBACK_TASK_MEMORY
    if _FALLBACK_TASK_MEMORY is None:
        from mu.memory.stores import TaskMemoryStore

        _FALLBACK_TASK_MEMORY = TaskMemoryStore()
    return _FALLBACK_TASK_MEMORY


def _fallback_scratchpad():
    global _FALLBACK_SCRATCHPAD
    if _FALLBACK_SCRATCHPAD is None:
        from mu.memory.stores import ScratchpadStore

        _FALLBACK_SCRATCHPAD = ScratchpadStore()
    return _FALLBACK_SCRATCHPAD


def _int_arg(args: Dict[str, Any], key: str, default: int) -> int:
    raw = args.get(key, default)
    try:
        value = int(raw or default)
    except (TypeError, ValueError):
        return default
    return value


# ---------------------------------------------------------------- task memory


@tool(
    name="save_memory",
    description=(
        "Saves a short, important fact into the in-task memory store so "
        "it can be reused later without replaying large context. Use "
        "kind to classify: decision (architectural choice), finding "
        "(verified fact), observation (general note), or goal (active "
        "work target). Use status to set the lifecycle state."
    ),
    parameters={
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The concise fact, decision, or reminder to store.",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional tags to help later retrieval.",
            },
            "source": {
                "type": "string",
                "description": "Optional note about where this memory came from.",
            },
            "kind": {
                "type": "string",
                "enum": ["decision", "finding", "observation", "goal"],
                "description": (
                    "Classification of this memory. Defaults to 'observation'. "
                    "Use 'decision' for architectural choices, 'finding' for "
                    "verified facts, 'goal' for active work targets."
                ),
                "default": "observation",
            },
            "status": {
                "type": "string",
                "enum": ["active", "done", "superseded", "archived", "stale"],
                "description": (
                    "Lifecycle state of this entry. Defaults to 'active'. "
                    "Use 'done' when work is complete, 'superseded' when a "
                    "newer entry replaces this one, 'archived' to remove "
                    "from search/summary but retain audit trail."
                ),
                "default": "active",
            },
        },
        "required": ["content"],
    },
    requires_approval=False,
    execution_kind="memory",
    preview_policy="none",
    server_policy="session_only",
    result_mode="raw",
)
def save_memory(args: Dict[str, Any], context) -> str:
    kind = str(args.get("kind", "observation") or "observation").strip()
    status = str(args.get("status", "active") or "active").strip()
    entry = _task_memory(context).save(
        args.get("content", ""),
        tags=args.get("tags", []),
        source=args.get("source", ""),
        kind=kind,
        status=status,
    )
    return f"Saved memory #{entry.id} [kind={entry.kind}, status={entry.status}] with tags={entry.tags}."


@tool(
    name="search_memory",
    description=(
        "Searches the in-task memory store for previously saved facts. "
        "By default returns only active entries — pass include_all=True "
        "or a status filter to see historical entries. Use kind to filter "
        "by entry classification (decision/finding/observation/goal)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search terms to match against memory content, tags, and sources.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of memory entries to return.",
                "default": 5,
            },
            "status": {
                "type": "string",
                "enum": ["active", "done", "superseded", "archived", "stale"],
                "description": (
                    "Filter by lifecycle status. If omitted, defaults to "
                    "active-only. Pass 'done' or 'superseded' to see "
                    "historical entries of that type."
                ),
            },
            "kind": {
                "type": "string",
                "enum": ["decision", "finding", "observation", "goal"],
                "description": "Filter by entry classification.",
            },
            "tags_exclude": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Exclude entries that have any of these tags.",
            },
            "include_all": {
                "type": "boolean",
                "description": (
                    "If True, return all entries regardless of status "
                    "(overrides status filter). Use for full audit/debugging."
                ),
                "default": False,
            },
        },
    },
    requires_approval=False,
    execution_kind="memory",
    preview_policy="none",
    server_policy="session_only",
    result_mode="raw",
)
def search_memory(args: Dict[str, Any], context) -> str:
    store = _task_memory(context)
    status = args.get("status")
    kind = args.get("kind")
    tags_exclude = args.get("tags_exclude")
    include_all = bool(args.get("include_all", False))
    entries = store.search(
        args.get("query", ""),
        limit=_int_arg(args, "limit", 5),
        status_filter=status,
        kind_filter=kind,
        tags_exclude=tags_exclude,
        include_all=include_all,
    )
    return store.format_results(entries)


@tool(
    name="list_memory",
    description=(
        "Lists the most recent in-task memory entries. Pass status to "
        "filter by lifecycle state (active/done/superseded/archived/stale). "
        "If omitted, lists all statuses."
    ),
    parameters={
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Maximum number of memory entries to return.",
                "default": 10,
            },
            "status": {
                "type": "string",
                "enum": ["active", "done", "superseded", "archived", "stale"],
                "description": (
                    "Filter by lifecycle status. If omitted, lists all statuses."
                ),
            },
        },
    },
    requires_approval=False,
    execution_kind="memory",
    preview_policy="none",
    server_policy="session_only",
    result_mode="raw",
)
def list_memory(args: Dict[str, Any], context) -> str:
    store = _task_memory(context)
    status = args.get("status")
    entries = store.list_entries(
        limit=_int_arg(args, "limit", 10),
        status_filter=status,
    )
    return store.format_results(entries)


# ---------------------------------------------------------------- lifecycle tools


@tool(
    name="update_memory_status",
    description=(
        "Update the lifecycle status of a memory entry. Valid statuses: "
        "active, done, superseded, archived, stale. Use 'done' when the "
        "work described is complete, 'superseded' when a newer entry "
        "replaces it, 'archived' to remove from search/summary but retain "
        "audit trail."
    ),
    parameters={
        "type": "object",
        "properties": {
            "entry_id": {
                "type": "integer",
                "description": "The numeric id of the memory entry to update.",
            },
            "status": {
                "type": "string",
                "enum": ["active", "done", "superseded", "archived", "stale"],
                "description": "The new lifecycle status.",
            },
            "reason": {
                "type": "string",
                "description": (
                    "Optional reason for the transition. If status='superseded' "
                    "and reason contains a numeric entry ID, that ID is set as "
                    "superseded_by."
                ),
            },
        },
        "required": ["entry_id", "status"],
    },
    requires_approval=False,
    execution_kind="memory",
    preview_policy="none",
    server_policy="session_only",
    result_mode="raw",
)
def update_memory_status(args: Dict[str, Any], context) -> str:
    store = _task_memory(context)
    entry_id = _int_arg(args, "entry_id", 0)
    if entry_id <= 0:
        return "Error: entry_id must be a positive integer."

    status = str(args.get("status", "") or "").strip()
    from mu.memory.stores import ALLOWED_STATUSES

    if status not in ALLOWED_STATUSES:
        return f"Error: Invalid status {status!r}. Valid: {', '.join(sorted(ALLOWED_STATUSES))}."

    entry = store.get_entry(entry_id)
    if entry is None:
        return f"Error: No memory entry with id #{entry_id}."

    old_status = entry.status
    reason = str(args.get("reason", "") or "").strip()

    # If superseded and reason contains an entry ID reference, set superseded_by
    if status == "superseded" and reason:
        import re

        id_match = re.search(r"#(\d+)", reason)
        if id_match:
            new_id = int(id_match.group(1))
            if store.get_entry(new_id) is not None:
                result = store.supersede(entry_id, new_id)
                if result is not None:
                    return (
                        f"Memory #{entry_id} status: {old_status} → superseded "
                        f"(superseded_by=#{new_id})."
                    )
        # Fall through to regular update if supersede didn't work

    updated = store.update_status(entry_id, status)
    if updated is None:
        return f"Error: Could not update memory #{entry_id}."

    return f"Memory #{entry_id} status: {old_status} → {status}."


@tool(
    name="supersede_memory",
    description=(
        "Mark an old memory entry as superseded by a newer one. Sets "
        "old.status='superseded', old.superseded_by=new_id, and "
        "new.supersedes=old_id. Both entries must exist. This is a "
        "singly-linked list, not a tree."
    ),
    parameters={
        "type": "object",
        "properties": {
            "old_id": {
                "type": "integer",
                "description": "The id of the entry being superseded.",
            },
            "new_id": {
                "type": "integer",
                "description": "The id of the entry that replaces it.",
            },
        },
        "required": ["old_id", "new_id"],
    },
    requires_approval=False,
    execution_kind="memory",
    preview_policy="none",
    server_policy="session_only",
    result_mode="raw",
)
def supersede_memory(args: Dict[str, Any], context) -> str:
    store = _task_memory(context)
    old_id = _int_arg(args, "old_id", 0)
    new_id = _int_arg(args, "new_id", 0)

    if old_id <= 0 or new_id <= 0:
        return "Error: old_id and new_id must be positive integers."

    old_entry = store.get_entry(old_id)
    new_entry = store.get_entry(new_id)
    if old_entry is None:
        return f"Error: No memory entry with id #{old_id}."
    if new_entry is None:
        return f"Error: No memory entry with id #{new_id}."

    result = store.supersede(old_id, new_id)
    if result is None:
        return f"Error: Could not supersede memory #{old_id}."

    old, new, old_status, new_status = result
    return (
        f"Memory #{old_id} [{old_status} → superseded] superseded by #{new_id}. "
        f"Memory #{new_id} now supersedes #{old_id}."
    )


@tool(
    name="retire_memory",
    description=(
        "Mark a memory entry as done — the work it describes is complete. "
        "Entry stays searchable but deprioritized in search and summary. "
        "Shorthand for update_memory_status(entry_id, 'done')."
    ),
    parameters={
        "type": "object",
        "properties": {
            "entry_id": {
                "type": "integer",
                "description": "The id of the memory entry to retire.",
            },
        },
        "required": ["entry_id"],
    },
    requires_approval=False,
    execution_kind="memory",
    preview_policy="none",
    server_policy="session_only",
    result_mode="raw",
)
def retire_memory(args: Dict[str, Any], context) -> str:
    store = _task_memory(context)
    entry_id = _int_arg(args, "entry_id", 0)
    if entry_id <= 0:
        return "Error: entry_id must be a positive integer."

    entry = store.get_entry(entry_id)
    if entry is None:
        return f"Error: No memory entry with id #{entry_id}."

    old_status = entry.status
    updated = store.update_status(entry_id, "done")
    if updated is None:
        return f"Error: Could not retire memory #{entry_id}."

    return f"Memory #{entry_id} retired: {old_status} → done."


@tool(
    name="reactivate_memory",
    description=(
        "Set a memory entry's status back to 'active'. Clears "
        "superseded_by if set. Use when revisiting completed or "
        "superseded work that is now relevant again."
    ),
    parameters={
        "type": "object",
        "properties": {
            "entry_id": {
                "type": "integer",
                "description": "The id of the memory entry to reactivate.",
            },
        },
        "required": ["entry_id"],
    },
    requires_approval=False,
    execution_kind="memory",
    preview_policy="none",
    server_policy="session_only",
    result_mode="raw",
)
def reactivate_memory(args: Dict[str, Any], context) -> str:
    store = _task_memory(context)
    entry_id = _int_arg(args, "entry_id", 0)
    if entry_id <= 0:
        return "Error: entry_id must be a positive integer."

    entry = store.get_entry(entry_id)
    if entry is None:
        return f"Error: No memory entry with id #{entry_id}."

    old_status = entry.status
    # Clear superseded_by when reactivating
    if entry.superseded_by is not None:
        entry.superseded_by = None

    updated = store.update_status(entry_id, "active")
    if updated is None:
        return f"Error: Could not reactivate memory #{entry_id}."

    return f"Memory #{entry_id} reactivated: {old_status} → active."


@tool(
    name="archive_memory",
    description=(
        "Archive a memory entry — removes it from search results (unless "
        "include_all=True) and from the system-prompt summary, but retains "
        "it in the store for audit trail. Use for old project context or "
        "superseded decisions with no replacement."
    ),
    parameters={
        "type": "object",
        "properties": {
            "entry_id": {
                "type": "integer",
                "description": "The id of the memory entry to archive.",
            },
        },
        "required": ["entry_id"],
    },
    requires_approval=False,
    execution_kind="memory",
    preview_policy="none",
    server_policy="session_only",
    result_mode="raw",
)
def archive_memory(args: Dict[str, Any], context) -> str:
    store = _task_memory(context)
    entry_id = _int_arg(args, "entry_id", 0)
    if entry_id <= 0:
        return "Error: entry_id must be a positive integer."

    entry = store.get_entry(entry_id)
    if entry is None:
        return f"Error: No memory entry with id #{entry_id}."

    old_status = entry.status
    updated = store.update_status(entry_id, "archived")
    if updated is None:
        return f"Error: Could not archive memory #{entry_id}."

    return f"Memory #{entry_id} archived: {old_status} → archived."


# ---------------------------------------------------------------- scratchpad


@tool(
    name="save_scratchpad",
    description=(
        "Saves a temporary note in the current turn scratchpad. Use this "
        "for short-lived plans or observations that do not need durable memory."
    ),
    parameters={
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The temporary note to store for the current turn.",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional tags to help later retrieval during this turn.",
            },
            "source": {
                "type": "string",
                "description": "Optional source note for the scratchpad entry.",
            },
        },
        "required": ["content"],
    },
    requires_approval=False,
    execution_kind="memory",
    preview_policy="none",
    server_policy="session_only",
    result_mode="raw",
)
def save_scratchpad(args: Dict[str, Any], context) -> str:
    entry = _scratchpad(context).save(
        args.get("content", ""),
        tags=args.get("tags", []),
        source=args.get("source", ""),
    )
    return f"Saved scratchpad note #{entry.id} with tags={entry.tags}."


@tool(
    name="search_scratchpad",
    description="Searches turn-local scratchpad notes saved during the current task loop.",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search terms to match against scratchpad content, tags, and sources.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of scratchpad entries to return.",
                "default": 5,
            },
        },
    },
    requires_approval=False,
    execution_kind="memory",
    preview_policy="none",
    server_policy="session_only",
    result_mode="raw",
)
def search_scratchpad(args: Dict[str, Any], context) -> str:
    store = _scratchpad(context)
    entries = store.search(args.get("query", ""), limit=_int_arg(args, "limit", 5))
    return store.format_results(entries)


@tool(
    name="list_scratchpad",
    description="Lists the most recent turn-local scratchpad entries.",
    parameters={
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Maximum number of scratchpad entries to return.",
                "default": 10,
            }
        },
    },
    requires_approval=False,
    execution_kind="memory",
    preview_policy="none",
    server_policy="session_only",
    result_mode="raw",
)
def list_scratchpad(args: Dict[str, Any], context) -> str:
    store = _scratchpad(context)
    entries = store.list_entries(limit=_int_arg(args, "limit", 10))
    return store.format_results(entries)


@tool(
    name="clear_scratchpad",
    description="Clears the current turn scratchpad.",
    parameters={"type": "object", "properties": {}},
    requires_approval=False,
    execution_kind="memory",
    preview_policy="none",
    server_policy="session_only",
    result_mode="raw",
)
def clear_scratchpad(args: Dict[str, Any], context) -> str:
    _scratchpad(context).clear()
    return "Turn scratchpad cleared."


# ---------------------------------------------------------------- tool result cache


@tool(
    name="recall",
    description=(
        "Recall a previously-cached tool result by its cache key. "
        "When the L4 compression summary shows [cache:KEY], call this tool "
        "with the key to fetch the full original result — no need to re-read "
        "files or re-run searches."
    ),
    parameters={
        "type": "object",
        "properties": {
            "cache_key": {
                "type": "string",
                "description": "The cache key from a [cache:KEY] tag in the compressed summary.",
            },
        },
        "required": ["cache_key"],
    },
    requires_approval=False,
    execution_kind="memory",
    preview_policy="none",
    server_policy="session_only",
    result_mode="raw",
)
def recall(args: Dict[str, Any], context) -> str:
    """Fetch a cached tool result by its cache key.

    The L4 compression system stores full tool results in a sidecar cache
    before compressing them into short summary lines. This tool retrieves
    the original full result — avoiding re-reading files or re-running searches.
    """
    import json as _json

    session = getattr(context, "session", None)
    if session is None or not hasattr(session, "tool_result_cache"):
        return "Error: No tool result cache available on this session."
    key = args.get("cache_key", "")
    if not key:
        return "Error: cache_key argument is required."
    result = session.tool_result_cache.recall(key)
    if result is None:
        return (
            f"Cache key '{key}' not found or evicted. "
            "The result may have been dropped due to LRU eviction. "
            "Re-run the original tool call if needed."
        )
    return _json.dumps(result, default=str, indent=2)