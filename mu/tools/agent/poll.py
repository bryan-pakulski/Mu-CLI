"""`poll_subagent` — check the status of an async sub-agent run.

Returns a snapshot of one sub-agent (by `task_id`): lifecycle state
(running / done / killed / error / missing), tool count + diversity, last
tool, elapsed, stuck/stall flags, and — once finished — the final summary,
token usage, and kill reason. Non-blocking: safe to call while the child
runs. The parent orchestrator calls this to decide whether to wait,
continue other work, kill, or consolidate.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from mu.tools import tool


logger = logging.getLogger("mucli")


def _envelope(*, ok: bool, message: str, error_code=None, data=None) -> Dict[str, Any]:
    return {
        "ok": ok,
        "error_code": error_code,
        "message": message,
        "data": data or {},
        "artifacts": [],
        "telemetry": {"tool_name": "poll_subagent"},
    }


@tool(
    name="poll_subagent",
    description=(
        "Poll an async sub-agent dispatched by `spawn_agent`. Returns its "
        "current status (running|done|killed|error|missing), tool count + "
        "diversity, last tool, elapsed, stuck/stall flags, and — once done "
        "or killed — the final summary, token usage, and kill reason. "
        "Non-blocking. Use this to retrieve sub-agent results instead of "
        "waiting synchronously."
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
    execution_kind="read",
    result_mode="json",
)
def poll_subagent(args: Dict[str, Any], context) -> Dict[str, Any]:
    task_id = str(args.get("task_id") or "").strip()
    if not task_id:
        return _envelope(
            ok=False,
            error_code="invalid_args",
            message="poll_subagent requires non-empty 'task_id'.",
        )

    parent = getattr(context, "session", None)
    if parent is None:
        return _envelope(
            ok=False,
            error_code="no_session",
            message="poll_subagent requires a parent session.",
        )

    registry = getattr(parent, "_subagent_registry", None)
    if registry is None:
        return _envelope(
            ok=False,
            error_code="no_registry",
            message="No sub-agent registry on this session.",
        )

    snap = registry.snapshot(task_id)
    status = str(snap.get("status", "missing"))

    # Render an on-demand progress snapshot to the UI when something is
    # active (the parent explicitly polled, so a panel refresh is wanted).
    # In headless mode (no UI), emit a structured JSON event to the log
    # instead so sub-agent progress is still observable.
    ui = getattr(parent, "ui", None)
    if ui is not None and hasattr(ui, "show_info") and registry.has_active():
        try:
            ui.show_info(registry.tracker.render_panel())
        except Exception:  # noqa: BLE001
            pass
    elif ui is None:
        try:
            logger.info("subagent progress: %s", registry.tracker.emit_structured_event())
        except Exception:  # noqa: BLE001
            pass

    if status == "missing":
        return _envelope(
            ok=False,
            error_code="not_found",
            message=f"No sub-agent with task_id={task_id}.",
            data=snap,
        )

    ok = status in ("done", "killed")
    message = snap.get("summary") or f"sub-agent status: {status}"
    if snap.get("kill_reason"):
        message = f"[{snap['kill_reason']}] {message}"
    return _envelope(
        ok=ok,
        message=message,
        error_code=None if ok else ("subagent_" + status if status != "done" else None),
        data=snap,
    )


__all__ = ["poll_subagent"]