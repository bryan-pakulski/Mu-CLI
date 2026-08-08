"""`poll_subagent` — non-blocking delegation status/result lookup."""
from __future__ import annotations

import logging
from typing import Any, Dict

from mu.tools import tool

logger = logging.getLogger("mucli")


def _envelope(*, ok: bool, message: str, error_code=None, data=None, artifacts=None) -> Dict[str, Any]:
    return {"ok": ok, "error_code": error_code, "message": message, "data": data or {}, "artifacts": artifacts or [], "telemetry": {"tool_name": "poll_subagent"}}


@tool(
    name="poll_subagent",
    description=("Non-blocking status check for one delegation returned by spawn_agent. Use occasionally while doing other work; do not busy-poll. Terminal results returned here are acknowledged so the same completion is not later duplicated through the specialist mailbox."),
    parameters={"type": "object", "properties": {"task_id": {"type": "string", "description": "task_id returned by spawn_agent."}}, "required": ["task_id"]},
    requires_approval=False,
    execution_kind="read",
    result_mode="json",
)
def poll_subagent(args: Dict[str, Any], context) -> Dict[str, Any]:
    task_id = str(args.get("task_id") or "").strip()
    if not task_id:
        return _envelope(ok=False, error_code="invalid_args", message="poll_subagent requires non-empty 'task_id'.")
    parent = getattr(context, "session", None)
    if parent is None:
        return _envelope(ok=False, error_code="no_session", message="poll_subagent requires a parent session.")
    registry = getattr(parent, "_subagent_registry", None)
    if registry is None:
        return _envelope(ok=False, error_code="no_registry", message="No sub-agent registry on this session.")

    snap = registry.snapshot(task_id)
    status = str(snap.get("status", "missing"))
    ui = getattr(parent, "ui", None)
    if ui is not None and hasattr(ui, "show_info") and registry.has_active():
        try:
            ui.show_info(registry.tracker.render_panel())
        except Exception:
            pass
    elif ui is None:
        try:
            logger.info("subagent progress: %s", registry.tracker.emit_structured_event())
        except Exception:
            pass

    artifacts = [snap["artifact"]] if isinstance(snap.get("artifact"), dict) else []
    if status == "missing":
        return _envelope(ok=False, error_code="not_found", message=f"No sub-agent with task_id={task_id}.", data=snap, artifacts=artifacts)
    if status != "running":
        try:
            registry.acknowledge_completion(task_id)
        except Exception:
            pass
    ok = status in ("done", "killed")
    message = snap.get("summary") or f"sub-agent status: {status}"
    if snap.get("kill_reason"):
        message = f"[{snap['kill_reason']}] {message}"
    return _envelope(ok=ok, message=message, error_code=None if ok else "subagent_" + status, data=snap, artifacts=artifacts)


__all__ = ["poll_subagent"]
