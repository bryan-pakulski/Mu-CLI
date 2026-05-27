"""Plan mode: a read-only enforcement layer for tool dispatch.

When `session.variables['plan_mode']` is True, any tool whose name appears
in `WRITE_TOOLS` (or whose descriptor has `requires_approval=True` for the
write-bucket policies) is short-circuited via a `pre_tool` hook. The model
receives a clear refusal envelope explaining that plan-mode is on and which
tool was blocked.

Plan mode is global per-session — it is a switch, not a fine-grained
permission. Use `/plan` to toggle.
"""

from __future__ import annotations

from typing import Optional, Set

from .hooks import HookContext, HookRegistry, HookResult, default_registry


# Tools that modify state outside the harness (filesystem, shell, feature
# plan mutators). Read-only tools (read_file, search, list_dir,
# get_workspace_details, etc.) are NOT blocked.
#
# Git operations are no longer wrapped as standalone tools — the model
# uses `bash` for git, so blocking `bash` covers them.
WRITE_TOOLS: Set[str] = {
    "write_file",
    "apply_diff",
    "search_and_replace_file",
    "bash",
    "bash_background",
    "bash_kill",
    # Sub-agents inherit YOLO and can write freely — block at the spawn site.
    "spawn_agent",
    # Feature-mode mutators
    "create_feature",
    "create_phases",
    "create_task",
    "update_task_status",
    "block_task",
    "resume_task",
    "archive_task",
    "propose_task_diff",
    "decide_task_diff",
    "create_feature_task",
    "update_feature_task",
    "approve_feature_task",
    # Security-mode mutators (the PoC verifications execute shell commands
    # against the workspace; in plan mode that's a write-side effect even
    # if the verification itself is read-only-ish).
    "verify_security_proof",
    "verify_remediation",
    # Teacher-mode mutators (course state, file-system artifact writes,
    # subprocess grading invocations).
    "create_course",
    "record_diagnostic",
    "update_learner_profile",
    "propose_curriculum",
    "approve_curriculum",
    "start_lesson",
    "assign_exercise",
    "submit_assignment",
    "grade_assignment",
    "decide_next",
    "record_dialog_turn",
    "close_dialog",
    "complete_module",
    "finalize_course",
    "schedule_review",
    "complete_review",
}


def _build_envelope(tool_name: str) -> dict:
    """Build a tool-result envelope that mimics what `_handle_*` returns
    when a tool is denied. The schema matches `_build_tool_envelope` in
    `core/tools.py` so the agent loop's structured-result wrapping does
    not need a special case.
    """
    return {
        "ok": False,
        "error_code": "plan_mode_blocked",
        "message": (
            f"Plan mode is active — '{tool_name}' is a write-side tool and "
            "was blocked. Use read-only tools to gather information, propose "
            "an explicit plan, and ask the user to disable plan mode with "
            "/plan before performing write operations."
        ),
        "data": {"tool_name": tool_name, "plan_mode": True},
        "artifacts": [],
        "telemetry": {
            "tool_name": tool_name,
            "execution_source": "plan_mode_block",
        },
    }


def is_plan_mode(ctx: HookContext) -> bool:
    if ctx.variables and ctx.variables.get("plan_mode"):
        return True
    session = ctx.session
    if session is not None:
        vars_ = getattr(session, "variables", None) or {}
        if vars_.get("plan_mode"):
            return True
    return False


def _block_write_tools(ctx: HookContext) -> Optional[HookResult]:
    if not is_plan_mode(ctx):
        return None
    tool_name = ctx.tool_name or ""
    if tool_name not in WRITE_TOOLS:
        return None
    return HookResult(
        action="short_circuit",
        payload=_build_envelope(tool_name),
        data={"reason": "plan_mode"},
    )


def install(registry: Optional[HookRegistry] = None) -> None:
    """Register the plan-mode hook on a registry.

    Idempotent — calling twice does not duplicate the registration. Tests
    that want a clean registry should construct their own and pass it in.
    """
    reg = registry or default_registry
    reg.remove("plan_mode_block_writes")
    reg.add_via_register = True  # marker for diagnostics
    from .hooks import HookSpec

    reg.add(
        HookSpec(
            name="plan_mode_block_writes",
            point="pre_tool",
            priority=10,  # run before other pre_tool hooks
            handler=_block_write_tools,
        )
    )


# Auto-install on import. Tests can `remove("plan_mode_block_writes")` to
# disable, then re-install.
install()


__all__ = ["WRITE_TOOLS", "install", "is_plan_mode"]
