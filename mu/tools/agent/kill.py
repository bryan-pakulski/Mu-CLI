"""`kill_subagent` — cancel an async sub-agent run.

Sets the cooperative cancel flag (`child._subagent_cancelled`) that the
child's agentic loop checks at the next iteration boundary (same mechanism
as `_hook_abort_requested`), waits briefly for the run thread to exit, and
returns the partial findings captured from the child's history. Safe to
call on an already-finished child (returns its current snapshot).
"""

from __future__ import annotations

from typing import Any, Dict

from mu.tools import tool


def _envelope(*, ok: bool, message: str, error_code=None, data=None) -> Dict[str, Any]:
    return {
        "ok": ok,
        "error_code": error_code,
        "message": message,
        "data": data or {},
        "artifacts": [],
        "telemetry": {"tool_name": "kill_subagent"},
    }


@tool(
    name="kill_subagent",
    description=(
        "Cancel an async sub-agent dispatched by `spawn_agent`. The child "
        "exits at its next iteration boundary and its accumulated partial "
        "findings are returned. Use when a child is stuck, looping, or no "
        "longer needed. Idempotent on finished children."
    ),
    parameters={
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The task_id returned by spawn_agent.",
            },
        },
        "required": ["task_id"],
    },
    requires_approval=False,
    execution_kind="mutate",
    result_mode="json",
)
def kill_subagent(args: Dict[str, Any], context) -> Dict[str, Any]:
    task_id = str(args.get("task_id") or "").strip()
    if not task_id:
        return _envelope(
            ok=False,
            error_code="invalid_args",
            message="kill_subagent requires non-empty 'task_id'.",
        )

    parent = getattr(context, "session", None)
    if parent is None:
        return _envelope(
            ok=False,
            error_code="no_session",
            message="kill_subagent requires a parent session.",
        )

    registry = getattr(parent, "_subagent_registry", None)
    if registry is None:
        return _envelope(
            ok=False,
            error_code="no_registry",
            message="No sub-agent registry on this session.",
        )

    snap = registry.cancel(task_id)
    status = str(snap.get("status", "missing"))
    if status == "missing":
        return _envelope(
            ok=False,
            error_code="not_found",
            message=f"No sub-agent with task_id={task_id}.",
            data=snap,
        )

    summary = snap.get("summary") or "(no partial findings recovered)"
    return _envelope(
        ok=True,
        message=f"Sub-agent {task_id} killed (status={status}). Partial findings: {summary}",
        data=snap,
    )


__all__ = ["kill_subagent"]