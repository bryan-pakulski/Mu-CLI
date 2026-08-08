"""Sparse specialist -> parent mailbox publication."""
from __future__ import annotations

from typing import Any, Dict

from mu.tools import tool


def _envelope(*, ok: bool, message: str, error_code=None, data=None) -> Dict[str, Any]:
    return {"ok": ok, "error_code": error_code, "message": message, "data": data or {}, "artifacts": [], "telemetry": {"tool_name": "send_subagent_finding"}}


@tool(
    name="send_subagent_finding",
    description=("Publish one material intermediate finding from a persistent specialist to the parent mailbox. Use sparingly: routine progress belongs in the UI and final results are published automatically at delegation completion."),
    parameters={"type": "object", "properties": {
        "summary": {"type": "string", "description": "Self-contained material finding."},
        "importance": {"type": "string", "enum": ["low", "normal", "high", "critical"]},
        "refs": {"type": "array", "items": {"type": "string"}, "description": "Optional cache/artifact/file references."}
    }, "required": ["summary"]},
    requires_approval=False,
    execution_kind="control",
    result_mode="json",
)
def send_subagent_finding(args: Dict[str, Any], context) -> Dict[str, Any]:
    child = getattr(context, "session", None)
    if child is None or str((getattr(child, "variables", None) or {}).get("session_role", "")).lower() != "child":
        return _envelope(ok=False, error_code="not_subagent", message="send_subagent_finding is only available from a child specialist session.")
    registry = getattr(child, "_parent_registry", None)
    task_id = str(child.variables.get("subagent_parent_task_id") or "").strip()
    summary = str(args.get("summary") or "").strip()
    if registry is None or not task_id:
        return _envelope(ok=False, error_code="no_parent", message="No parent mailbox is attached to this specialist.")
    if not summary:
        return _envelope(ok=False, error_code="invalid_args", message="summary must be non-empty.")
    result = registry.publish_finding(task_id, summary, importance=str(args.get("importance") or "normal"), refs=args.get("refs") or [])
    if not result.get("ok"):
        return _envelope(ok=False, error_code="publish_failed", message=str(result.get("error") or "Unable to publish finding."))
    return _envelope(ok=True, message="Finding published to parent mailbox.", data=result)


__all__ = ["send_subagent_finding"]
