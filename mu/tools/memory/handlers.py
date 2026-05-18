"""Session memory + scratchpad `@tool` handlers.

The handlers route through `context.session.{task_memory,turn_scratchpad}`.
A fallback in-process store keeps unit tests that pass `session=None`
working — the legacy short-circuit in `Session._execute_tool_with_memory`
used to provide this implicitly via the session reference.
"""

from typing import Any, Dict, Optional

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
        "it can be reused later without replaying large context."
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
    entry = _task_memory(context).save(
        args.get("content", ""),
        tags=args.get("tags", []),
        source=args.get("source", ""),
    )
    return f"Saved memory #{entry.id} with tags={entry.tags}."


@tool(
    name="search_memory",
    description="Searches the in-task memory store for previously saved facts.",
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
    entries = store.search(args.get("query", ""), limit=_int_arg(args, "limit", 5))
    return store.format_results(entries)


@tool(
    name="list_memory",
    description="Lists the most recent in-task memory entries.",
    parameters={
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Maximum number of memory entries to return.",
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
def list_memory(args: Dict[str, Any], context) -> str:
    store = _task_memory(context)
    entries = store.list_entries(limit=_int_arg(args, "limit", 10))
    return store.format_results(entries)


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
