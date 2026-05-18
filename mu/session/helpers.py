"""Shared session helpers — extracted to break circular imports.

The agent loop body (`mu/agent/loop_body.py`), session manager
(`mu/session/manager.py`), history mixin (`mu/session/history.py`),
message-assembly helpers (`mu/session/messages.py`), tools glue
(`mu/session/tools_glue.py`), and provider retry wrapper
(`mu/agent/retry.py`) all need these primitives. Hosting them here
(no Session/SessionManager imports) lets every caller import them
directly without `_bind_helpers` / `_bind_session_symbols`
indirection or duplicated copies.

`Session` and `SessionManager` re-export these from `mu/session/session.py`
for backward compatibility with `mucli` and tests.
"""

from __future__ import annotations

import os
import re


# ============================================================ log truncation


def _sanitize_for_log(data):
    """Truncates large data for logging."""
    if isinstance(data, str) and len(data) > 1000:
        return f"{data[:500]}... [TRUNCATED {len(data)-1000} chars] ...{data[-500:]}"
    return data


def _shorten_tool_args(args: dict) -> dict:
    """Shortens long string arguments (like 'content' or 'diff') for display."""
    if not args:
        return {}
    if not isinstance(args, dict):
        return {"_raw_args": str(args)}
    shortened = args.copy()
    for key in ["content", "diff"]:
        if (
            key in shortened
            and isinstance(shortened[key], str)
            and len(shortened[key]) > 100
        ):
            shortened[key] = f"({len(shortened[key])} chars)"
    return shortened


# ============================================================ feature plan


def _safe_feature_path_prefix(path: str) -> str:
    normalized = os.path.abspath(path)
    return normalized if normalized.endswith(os.sep) else f"{normalized}{os.sep}"


def _slugify_feature_id(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return slug or "feature"


def derive_feature_state_status(feature_plan: dict | None) -> str:
    """Derive the feature status from the feature plan summary dict.

    Canonical state machine for feature status:
      * awaiting_approval — not yet approved
      * in_progress — approved and has active tasks
      * running — approved but no task activity detected
      * completed — all phases done / tasks completed/archived / review_status == "completed"
    """
    if not isinstance(feature_plan, dict):
        return "running"
    if not feature_plan.get("approved", False):
        return "awaiting_approval"
    if feature_plan.get("review_status") == "completed":
        return "completed"
    tasks = feature_plan.get("tasks") or []
    if isinstance(tasks, list):
        for task in tasks:
            if isinstance(task, dict) and task.get("status") in ("in_progress", "blocked"):
                return "in_progress"
    if (
        feature_plan.get("phases_completed")
        and feature_plan.get("next_phase") is None
    ):
        return "completed"
    active_tasks = [
        t for t in tasks if isinstance(t, dict) and t.get("status") not in ("archived", None)
    ]
    if active_tasks:
        return "in_progress"
    return "running"


# ============================================================ hook abort


class _HookAbort(Exception):
    """Raised by `_provider_generate_with_retry` when a `pre_provider_call`
    hook returns `HookResult(action="abort")`. Bypasses the retry wrapper
    so the iteration loop can break out cleanly with the abort flag set."""

    def __init__(self, reason: str | None = None):
        super().__init__(reason or "Hook requested abort")
        self.reason = reason


def _hook_abort_envelope(tool_name: str, reason: str | None) -> dict:
    """Synthetic tool-result envelope returned when a `pre_tool` hook
    aborts. The model sees a clear refusal so it doesn't retry the tool
    in a tight loop; the session's `_hook_abort_requested` flag causes
    the agentic loop to exit cleanly after this iteration."""
    return {
        "ok": False,
        "error_code": "hook_aborted",
        "message": (
            f"Tool '{tool_name}' was aborted by a hook: "
            f"{reason or 'hook requested abort'}. The agent loop is exiting."
        ),
        "data": {"tool_name": tool_name, "reason": reason or ""},
        "artifacts": [],
        "telemetry": {"tool_name": tool_name, "hook_aborted": True},
    }


__all__ = [
    "_HookAbort",
    "_hook_abort_envelope",
    "_safe_feature_path_prefix",
    "_sanitize_for_log",
    "_shorten_tool_args",
    "_slugify_feature_id",
    "derive_feature_state_status",
]
