"""Session-type capability policy.

``session_type`` is deliberately orthogonal to ``agent_mode``.  The former
controls where tools execute and which capabilities exist; the latter controls
the workflow prompt.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

VALID_SESSION_TYPES = frozenset({"chat", "workspace", "container", "orchestration"})

# Keep conversational sessions useful without exposing host filesystem/process
# capabilities.  Artifact tools are safe because they write only to the
# session-owned registry directory.
CHAT_TOOLS = frozenset(
    {
        "web_search",
        "url_grounding",
        "arxiv_search",
        "doi_resolve",
        "reddit_search",
        "stackoverflow_search",
        "hackernews_search",
        "read_document",
        "assess_source",
        "save_memory",
        "search_memory",
        "list_memory",
        "search_history",
        "ask_user_choice",
        "set_session_goal",
        "upload_artifact",
        "publish_visualization",
        "list_artifacts",
        "list_attachments",
        "read_attachment",
        "search_attachments",
        "list_threads",
        "get_thread_activity",
        "send_thread_message",
        "acknowledge_thread_message",
        "wait_for_thread_reply",
        "claim_thread_paths",
        "release_thread_paths",
        "handoff_thread_paths",
        "request_thread_claim_override",
    }
)

# Orchestration sessions (documentation/plans/orchestration.md §4) have no host shell or
# filesystem: only the master-mediated orch_* tools plus research, memory and questions.
ORCHESTRATION_PREFIX = "orch_"
ORCHESTRATION_TOOLS = frozenset(
    {
        "orch_catalog_search",
        "orch_tool_describe",
        "orch_nodes_list",
        "orch_node_facts",
        "orch_tool_draft",
        "orch_tool_update",
        "orch_tool_lint",
        "orch_tool_test",
        "orch_tool_submit",
        "orch_run_propose",
        "orch_run_status",
        "orch_run_results",
        "orch_runbook_draft",
        "orch_runbook_validate",
        "orch_runbook_test",
        "web_search",
        "url_grounding",
        "read_document",
        "save_memory",
        "search_memory",
        "list_memory",
        "search_history",
        "ask_user_choice",
        "set_session_goal",
    }
)

SESSION_TYPE_TOOLS: dict[str, frozenset[str] | None] = {
    "chat": CHAT_TOOLS,
    "workspace": None,
    "container": None,
    "orchestration": ORCHESTRATION_TOOLS,
}


def normalize_session_type(value: Any) -> str:
    """Return a supported session type, defaulting safely to ``workspace``."""
    normalized = str(value or "workspace").strip().lower()
    return normalized if normalized in VALID_SESSION_TYPES else "workspace"


def allowed_tools(session_type: str) -> frozenset[str] | None:
    """Return the allowed tool names; ``None`` means all registered tools."""
    return SESSION_TYPE_TOOLS[normalize_session_type(session_type)]


def is_tool_allowed(tool_name: str, session_type: str) -> bool:
    session_type = normalize_session_type(session_type)
    # Fleet tools exist only in orchestration sessions, never beside a host shell.
    if str(tool_name).startswith(ORCHESTRATION_PREFIX) and session_type != "orchestration":
        return False
    allowed = allowed_tools(session_type)
    return allowed is None or str(tool_name) in allowed


def filter_tools_for_session_type(
    tools: Iterable[Any], session_type: str
) -> list[Any]:
    return [tool for tool in tools if is_tool_allowed(getattr(tool, "name", ""), session_type)]


def tools_enabled_without_workspace(session_type: str) -> bool:
    """Whether provider tool schemas may be exposed without attached folders."""
    return normalize_session_type(session_type) in {"chat", "container", "orchestration"}


def session_type_from_context(context: Any) -> str:
    """Resolve ``session_type`` from a tool execution context or session.

    Tool handlers use this instead of assuming that an attached
    ``FolderContext`` defines their filesystem boundary.  Container sessions
    deliberately expose the complete container filesystem while workspace
    sessions retain host-side containment.
    """
    if context is None:
        return "workspace"
    variables = getattr(context, "variables", None) or {}
    session = getattr(context, "session", None)
    if session is not None:
        variables = {**(getattr(session, "variables", None) or {}), **variables}
    return normalize_session_type(variables.get("session_type", "workspace"))


def unrestricted_container_filesystem(session_type: str) -> bool:
    """Return whether workspace containment is disabled for this runtime."""
    return normalize_session_type(session_type) == "container"


__all__ = [
    "CHAT_TOOLS",
    "ORCHESTRATION_TOOLS",
    "SESSION_TYPE_TOOLS",
    "VALID_SESSION_TYPES",
    "allowed_tools",
    "filter_tools_for_session_type",
    "is_tool_allowed",
    "normalize_session_type",
    "session_type_from_context",
    "tools_enabled_without_workspace",
    "unrestricted_container_filesystem",
]
