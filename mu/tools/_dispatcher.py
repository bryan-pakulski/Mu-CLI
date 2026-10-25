"""Tool dispatcher.

`dispatch(tool_name, args, ...)` is the canonical entry point for
executing a tool. It performs descriptor lookup, argument-shape
validation and path-emptiness gating, binds the session's research and mode
states, then invokes the registered handler and wraps the result in the
canonical envelope.

The function returns a JSON-encoded envelope string. `mu.tools.execute(...)`
parses that back into a dict for callers that want structured access;
`execute_tool` re-exports `dispatch` as the legacy callable signature.

`TOOL_HANDLERS` is the bridge dict the `@tool` decorator mirrors into
at decoration time, kept for back-compat with test fixtures that mutate
it directly. The canonical handler registry lives in `mu/tools/__init__.py`.
"""

from __future__ import annotations

import contextlib
import json
import re
import traceback
from typing import Any, Callable, Dict, Optional

from utils.logger import logger

from ._envelope import _build_tool_envelope, _envelope_from_handler_result

_STUB_MARKER = "__stubbed__"
# Placeholder serialised as JSON ({"__stubbed__": ...) or mangled by the model
# ({__stubbed__: ... — the exact text bash was asked to run). Anchored on the
# first key so real content that merely mentions the marker passes.
_STUB_STRING_RE = re.compile(r'^\s*\{\s*"?__stubbed__"?\s*:')


def _find_stub_placeholder(args: dict) -> Optional[str]:
    """Key of a top-level arg that is an elided-args placeholder, else None.

    Matches the dict form produced by ``stub_oversized_arg_values`` and the
    same object serialised (or mangled) into a string.
    """
    for key, value in args.items():
        if isinstance(value, dict) and value.get(_STUB_MARKER) is True:
            return str(key)
        if isinstance(value, str) and _STUB_STRING_RE.match(value):
            return str(key)
    return None


# =================================================================== handler registry


TOOL_HANDLERS: dict[str, Callable[[dict, Any], str]] = {}

# These tools register bibliography entries in CitationManager.  The manager
# is process-global for backwards compatibility, but its contents are
# snapshotted on the owning SessionManager so unloaded research sessions can
# resume with their evidence intact.
# Tools that read or change the session's ResearchState: dispatch binds that
# state for the call and saves the session afterwards.
_RESEARCH_SOURCE_TOOLS = {
    "url_grounding",
    "research_fetch",
    "web_search",
    "arxiv_search",
    "doi_resolve",
    "reddit_search",
    "stackoverflow_search",
    "hackernews_search",
    "read_document",
    "assess_source",
    "research_question",
    "record_claim",
    "add_evidence",
}


def _path_arg_error(key: str) -> str:
    return (
        f"Error: The '{key}' argument is empty. "
        "You must provide a valid filesystem path for the active runtime."
    )


def _resolve_handler(descriptor) -> Optional[Any]:
    """Resolve the handler for a tool, checking the bridge dict first
    and falling back to the canonical `mu.tools._HANDLERS` registry."""
    handler = TOOL_HANDLERS.get(descriptor.handler_key)
    if handler is not None:
        return handler

    from . import _HANDLERS, _ensure_legacy_loaded

    _ensure_legacy_loaded()
    return _HANDLERS.get(descriptor.handler_key)


def dispatch(
    tool_name: str,
    args: Dict[str, Any],
    folder_context,
    ui: Any = None,
    variables: Optional[Dict[str, Any]] = None,
    *,
    invocation_source: str = "session",
    session: Any = None,
) -> str:
    """Canonical tool dispatcher.

    Resolves the tool descriptor, validates arguments, invokes the handler,
    and returns a JSON-encoded envelope string.
    """
    from mu.tools.descriptors import get_tool_descriptor, build_tool_context

    descriptor = get_tool_descriptor(tool_name)
    if not descriptor:
        return json.dumps(
            _build_tool_envelope(
                tool_name=tool_name,
                ok=False,
                error_code="not_found",
                message=f"Unknown tool: {tool_name}",
            )
        )

    # Capability filtering is enforced again at dispatch time so a stale model
    # tool call or direct programmatic invocation cannot bypass the provider
    # schema filter used by the agent loop.
    from mu.tools.capabilities import is_tool_allowed, normalize_session_type

    runtime_variables = variables or getattr(session, "variables", {}) or {}
    session_type = normalize_session_type(runtime_variables.get("session_type"))
    if not is_tool_allowed(tool_name, session_type):
        return json.dumps(
            _build_tool_envelope(
                tool_name=tool_name,
                ok=False,
                error_code="session_type_forbidden",
                message=(
                    f"Tool {tool_name!r} is unavailable in {session_type!r} "
                    "session type."
                ),
                telemetry={"session_type": session_type},
            )
        )

    if not isinstance(args, dict):
        return json.dumps(
            _build_tool_envelope(
                tool_name=tool_name,
                ok=False,
                error_code="invalid_args",
                message=(
                    f"Error: Tool '{tool_name}' arguments must be an "
                    f"object/dict, got {type(args).__name__}. Please re-issue "
                    "the tool call with JSON object arguments."
                ),
            )
        )

    # Context packaging elides long args of ALREADY-ANSWERED calls in the
    # projected request ({"__stubbed__": true, ...}). A model that copies such
    # a placeholder into a NEW call would otherwise run it as literal input
    # (bash executed "{__stubbed__: ..."). Reject with a resend hint.
    stubbed_key = _find_stub_placeholder(args)
    if stubbed_key is not None:
        return json.dumps(
            _build_tool_envelope(
                tool_name=tool_name,
                ok=False,
                error_code="invalid_args",
                message=(
                    f"Error: argument {stubbed_key!r} is a context-elision placeholder "
                    "(__stubbed__), not real content. Earlier tool-call arguments are "
                    "shortened in your context after they run; there is no size limit "
                    "on new calls. Re-issue the call with the full literal value."
                ),
            )
        )

    # Path-shaped argument keys that must not be empty when provided.
    for key in ("filename", "file", "path"):
        if key in args and (not args[key] or str(args[key]).strip() == ""):
            return json.dumps(
                _build_tool_envelope(
                    tool_name=tool_name,
                    ok=False,
                    error_code="invalid_args",
                    message=_path_arg_error(key),
                )
            )

    handler = _resolve_handler(descriptor)
    if not handler:
        return json.dumps(
            _build_tool_envelope(
                tool_name=tool_name,
                ok=False,
                error_code="not_found",
                message=f"Error: No handler registered for tool '{tool_name}'.",
            )
        )

    context = build_tool_context(
        folder_context,
        ui,
        variables,
        invocation_source=invocation_source,
        session=session,
    )
    try:
        # Bind this session's ResearchState for the handler call, so code that
        # resolves through get_citation_manager() hits THIS session's state and
        # never one shared with a concurrent session.
        _research_ctx = contextlib.nullcontext()
        _manager = getattr(session, "session_manager", None) if session is not None else None
        _session_state = getattr(_manager, "research", None)
        if _session_state is not None:
            from mu.research import ResearchState, use_research_state

            if isinstance(_session_state, ResearchState):
                _research_ctx = use_research_state(_session_state)
        # Same for the session's typed mode states (mu.modes.state).
        _mode_ctx = contextlib.nullcontext()
        _mode_states = getattr(_manager, "mode_state", None)
        if _mode_states is not None:
            from mu.modes.state import ModeStates, use_mode_states

            if isinstance(_mode_states, ModeStates):
                _mode_ctx = use_mode_states(_mode_states)
            else:
                _mode_states = None
        with _research_ctx, _mode_ctx:
            raw_result = handler(args, context)
        envelope = _envelope_from_handler_result(tool_name, raw_result)
        _mode_dirty = _mode_states is not None and _mode_states.consume_dirty()
        if (tool_name in _RESEARCH_SOURCE_TOOLS or _mode_dirty) and session is not None:
            manager = getattr(session, "session_manager", None)
            if manager is not None and getattr(manager, "current_session_name", ""):
                manager.save_history(getattr(session, "folder_context", None))
        if "execution_source" not in envelope.get("telemetry", {}):
            envelope.setdefault("telemetry", {})["execution_source"] = (
                invocation_source
            )
        return json.dumps(envelope, indent=2, sort_keys=True)
    except Exception as exc:
        hint = ""
        if isinstance(exc, AttributeError) and (
            "'str' object has no attribute 'get'" in str(exc)
            or "object has no attribute 'get'" in str(exc)
        ):
            hint = (
                " Hint: the tool received a string where a dict was expected — "
                "the model likely passed a JSON-encoded string instead of a parsed object."
            )
        logger.error(
            "Tool %r failed with %s: %s\n%s",
            tool_name,
            type(exc).__name__,
            exc,
            traceback.format_exc(),
        )
        return json.dumps(
            _build_tool_envelope(
                tool_name=tool_name,
                ok=False,
                error_code="execution_failed",
                message=(
                    f"Error: Tool '{tool_name}' failed with {type(exc).__name__}: {exc}."
                    f"{hint} Please fix arguments and retry."
                ),
                telemetry={"execution_source": invocation_source},
            ),
            indent=2,
        )


def execute_tool(
    tool_name: str,
    args: dict,
    folder_context,
    ui=None,
    variables: dict = None,
    *,
    invocation_source: str = "session",
    session: Any = None,
) -> str:
    """Legacy-compatible alias for `dispatch`."""
    return dispatch(
        tool_name,
        args,
        folder_context,
        ui,
        variables,
        invocation_source=invocation_source,
        session=session,
    )


__all__ = ["TOOL_HANDLERS", "dispatch", "execute_tool"]
