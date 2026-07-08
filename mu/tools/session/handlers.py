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