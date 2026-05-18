"""Tool dispatcher.

`dispatch(tool_name, args, ...)` is the canonical entry point for
executing a tool. It performs descriptor lookup, argument-shape
validation, path-emptiness gating, feature-mode review-gate enforcement
for `apply_diff`, then invokes the registered handler and wraps the
result in the canonical envelope.

The function returns a JSON-encoded envelope string (the wire format
the agent loop has used since day one). `mu.tools.execute(...)` parses
that back into a dict for callers that want structured access; the
legacy `core.tools.execute_tool(...)` re-exports `dispatch` so existing
callers keep working.

Several dependencies are still in `core/tools.py` and resolved lazily
to avoid the circular-import problem at module load:
  * `get_tool_descriptor` / `get_tool_definition` / `TOOL_HANDLERS`
  * `build_tool_context`
  * The feature-mode `load_feature_plan` lookup for the apply_diff gate.
"""

from __future__ import annotations

import json
import os
import traceback
from typing import Any, Dict, Optional

from utils.logger import logger

from ._envelope import _build_tool_envelope, _envelope_from_handler_result


def _path_arg_error(key: str) -> str:
    return (
        f"Error: The '{key}' argument is empty. "
        "You must provide a valid file path from the workspace map."
    )


def _apply_diff_review_gate(
    args: Dict[str, Any], session: Any, tool_name: str
) -> Optional[Dict[str, Any]]:
    """If feature-mode review is active, refuse `apply_diff` unless the
    call carries an approved `proposal_id`. Returns a failure envelope
    when the call is blocked, or None to let it through.

    The check is scoped to `apply_diff` only — every other tool returns
    None immediately."""
    if tool_name != "apply_diff" or session is None:
        return None
    if not hasattr(session, "session_manager"):
        return None

    feature_state = session.session_manager.get_feature_state() or {}
    if not isinstance(feature_state, dict):
        return None
    feature_plan = feature_state.get("feature_plan") or {}
    if not isinstance(feature_plan, dict):
        return None

    in_review_mode = bool(feature_plan.get("tasks_completed")) and (
        str(feature_plan.get("review_status", "")).strip().lower() != "completed"
    )
    if not in_review_mode:
        return None

    proposal_id = str(args.get("proposal_id", "") or "").strip()
    if not proposal_id:
        return _build_tool_envelope(
            tool_name=tool_name,
            ok=False,
            error_code="invalid_args",
            message=(
                "Error: apply_diff in review mode requires proposal_id "
                "for an approved diff proposal."
            ),
        )

    metadata_path = str(feature_state.get("metadata_path", "") or "").strip()
    if not metadata_path or not os.path.exists(metadata_path):
        return _build_tool_envelope(
            tool_name=tool_name,
            ok=False,
            error_code="not_found",
            message="Error: Feature metadata not found for review-mode apply_diff.",
        )

    from core.feature_mode import load_feature_plan

    plan = load_feature_plan(metadata_path)
    proposal = next(
        (item for item in plan.diff_proposals if item.id == proposal_id), None
    )
    if proposal is None or proposal.status != "approved":
        return _build_tool_envelope(
            tool_name=tool_name,
            ok=False,
            error_code="access_denied",
            message=(
                "Error: apply_diff blocked in review mode. "
                "proposal_id must reference an approved diff proposal."
            ),
        )
    return None


def _resolve_handler(descriptor) -> Optional[Any]:
    """Resolve the handler for a tool, checking the legacy bridge first
    and falling back to the new `_HANDLERS` registry.

    Lookup order: legacy `core.tools.TOOL_HANDLERS` (bridged @tool
    registrations are mirrored here at decoration time) → new
    `mu.tools._HANDLERS` (canonical registry, populated by the @tool
    decorator). Returns None if the tool has no registered handler in
    either map."""
    from core import tools as _core_tools

    handler = _core_tools.TOOL_HANDLERS.get(descriptor.handler_key)
    if handler is not None:
        return handler

    # New-registry fallback. Use the in-module dicts directly to avoid
    # the lazy-loader running another mirror pass.
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

    Resolves the tool descriptor, validates arguments, enforces the
    feature-mode `apply_diff` review gate, invokes the handler, and
    returns a JSON-encoded envelope string.
    """
    from core.tools import get_tool_descriptor, build_tool_context

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

    gate = _apply_diff_review_gate(args, session, tool_name)
    if gate is not None:
        return json.dumps(gate)

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
        raw_result = handler(args, context)
        envelope = _envelope_from_handler_result(tool_name, raw_result)
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


__all__ = ["dispatch"]
