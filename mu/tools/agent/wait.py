"""`await_subagent` — block until a sub-agent finishes or a timer fires.

The blocking counterpart to ``poll_subagent``. Instead of the parent
busy-polling ``poll_subagent`` every iteration (which trips loop
detection after ``loop_detection_repeat_threshold`` identical calls),
the parent issues a single ``await_subagent`` tool call and blocks here
until one of two conditions **the parent chose** is met:

  * **the sub-agent finishes** — the child's run thread reached a
    terminal state (done / killed / error) and signalled the registry's
    per-child ``done_event``; or
  * **the timer fires** — ``timeout`` seconds elapsed with the child
    still running; the snapshot is returned with ``status == "running"``
    and ``error_code == "timeout"`` so the parent can re-await, kill, or
    continue other work.

Blocking is safe: the agent-loop thread is never the asyncio event loop
(GUI runs a turn via ``asyncio.to_thread``; CLI runs on the main thread
with no UI to freeze), and tool dispatch has no per-tool timeout that
would abort a long-blocking call. While the parent is blocked it issues
no provider calls, so its iteration count does not advance — it
genuinely sleeps. ``await_subagent`` is exempt from loop detection
(see ``_BOOKKEEPING_TOOLS``) so repeated awaits after timeouts do not
themselves trip the detector.

``timeout`` is clamped to ``[0, MAX_TIMEOUT]``. ``timeout=0`` is a
non-blocking probe. ``timeout=None`` is accepted and means "block until
the child finishes" (no timer) — the parent always wakes on the
sub-agent-finishes condition; callers that want a guaranteed wake should
pass a finite timeout.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from mu.tools import tool


logger = logging.getLogger("mucli")

# Hard cap so a single await can never block the agent thread for more
# than ~10 minutes. The parent can always re-await to wait longer.
MAX_TIMEOUT = 600.0
# Sensible default when the model omits timeout: long enough to let a
# real child finish, short enough that the parent always re-wakes to
# reconsider (kill / extend / continue other work).
DEFAULT_TIMEOUT = 120.0

_TERMINAL = ("done", "killed", "error")


def _envelope(*, ok: bool, message: str, error_code=None, data=None, artifacts=None) -> Dict[str, Any]:
    # MUCLI_SUBAGENT_DURABLE_RESULTS_V1: await_subagent
    return {
        "ok": ok,
        "error_code": error_code,
        "message": message,
        "data": data or {},
        "artifacts": artifacts or [],
        "telemetry": {"tool_name": "await_subagent"},
    }


def _clamp_timeout(value: Any) -> float:
    """Coerce the timeout arg into a finite float in [0, MAX_TIMEOUT].

    ``None`` means "block until the child finishes" — returned as-is so
    ``Event.wait`` blocks indefinitely. Non-numeric / negative values fall
    back to ``DEFAULT_TIMEOUT``.
    """
    if value is None:
        return None  # type: ignore[return-value]
    try:
        t = float(value)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    if t < 0 or t != t:  # NaN guard
        return DEFAULT_TIMEOUT
    return min(t, MAX_TIMEOUT)


@tool(
    name="await_subagent",
    description=(
        "Block until an async sub-agent (dispatched by `spawn_agent`) "
        "finishes, or until `timeout` seconds elapse — whichever comes "
        "first. This is the blocking counterpart to `poll_subagent`: use "
        "it to wait without burning parent iterations on a poll loop "
        "(which would trip loop detection). Returns the sub-agent's "
        "snapshot (status, summary, tokens, etc.). On finish the status "
        "is terminal (done|killed|error); on timeout the status is still "
        "`running` with error_code=`timeout` — then decide whether to "
        "re-await, kill, or continue other work. `timeout=0` is a "
        "non-blocking probe. Prefer this over repeated `poll_subagent` "
        "calls when you have nothing else to do but wait."
    ),
    parameters={
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The task_id returned by spawn_agent.",
            },
            "timeout": {
                "type": "number",
                "description": (
                    "Max seconds to block. 0 = non-blocking probe. "
                    "Omit / null = block until the sub-agent finishes "
                    "(no timer). Clamped to 600s."
                ),
            },
        },
        "required": ["task_id"],
    },
    requires_approval=False,
    execution_kind="read",
    result_mode="json",
)
def await_subagent(args: Dict[str, Any], context) -> Dict[str, Any]:
    task_id = str(args.get("task_id") or "").strip()
    if not task_id:
        return _envelope(
            ok=False,
            error_code="invalid_args",
            message="await_subagent requires non-empty 'task_id'.",
        )

    parent = getattr(context, "session", None)
    if parent is None:
        return _envelope(
            ok=False,
            error_code="no_session",
            message="await_subagent requires a parent session.",
        )

    registry = getattr(parent, "_subagent_registry", None)
    if registry is None:
        return _envelope(
            ok=False,
            error_code="no_registry",
            message="No sub-agent registry on this session.",
        )

    timeout = _clamp_timeout(args.get("timeout"))

    # First peek without blocking: a terminal / missing child should
    # return immediately rather than entering the event wait.
    snap = registry.snapshot(task_id)
    status = str(snap.get("status", "missing"))

    if status == "missing":
        return _envelope(
            ok=False,
            error_code="not_found",
            message=f"No sub-agent with task_id={task_id}.",
            data=snap,
            artifacts=[snap["artifact"]] if isinstance(snap.get("artifact"), dict) else [],
        )

    if status in _TERMINAL:
        ok = status in ("done", "killed")
        message = snap.get("summary") or f"sub-agent status: {status}"
        if snap.get("kill_reason"):
            message = f"[{snap['kill_reason']}] {message}"
        return _envelope(
            ok=ok,
            message=message,
            error_code=None if ok else ("subagent_" + status if status != "done" else None),
            data=snap,
            artifacts=[snap["artifact"]] if isinstance(snap.get("artifact"), dict) else [],
        )

    # Still running — block on the per-child done_event until finish or
    # timeout. The parent agent thread sleeps here; its iteration count
    # does not advance, so no poll-loop fingerprint accumulates.
    blocked = timeout is None
    snap = registry.wait(task_id, timeout=timeout)
    status = str(snap.get("status", "running"))

    # Surface a progress panel refresh on wake so the user sees why the
    # parent resumed (mirrors poll_subagent's on-demand render).
    ui = getattr(parent, "ui", None)
    if ui is not None and hasattr(ui, "show_info") and registry.has_active():
        try:
            ui.show_info(registry.tracker.render_panel())
        except Exception:  # noqa: BLE001
            pass
    elif ui is None:
        try:
            logger.info("subagent await woke: %s", registry.tracker.emit_structured_event())
        except Exception:  # noqa: BLE001
            pass

    if status == "running":
        # Timer fired first. Tell the parent it must decide; do not
        # pretend success.
        tmsg = "indefinitely" if blocked else f"{timeout:g}s"
        return _envelope(
            ok=False,
            error_code="timeout",
            message=(
                f"await timed out after {tmsg}; sub-agent {task_id} "
                "still running. Re-await, kill_subagent, or continue "
                "other work."
            ),
            data=snap,
            artifacts=[snap["artifact"]] if isinstance(snap.get("artifact"), dict) else [],
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
        artifacts=[snap["artifact"]] if isinstance(snap.get("artifact"), dict) else [],
    )


__all__ = ["await_subagent"]