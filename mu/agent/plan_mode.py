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
    # Feature-mode mutators (plan files, task state, command runs).
    "propose_feature_plan",
    "revise_feature_plan",
    "start_task",
    "check_criterion",
    "cite_evidence",
    "complete_task",
    "block_task",
    "review_task",
    "run_feature_suite",
    "propose_task_diff",
    "request_feature_completion",
    # Security-mode mutators (the PoC verifications execute shell commands
    # against the workspace; in plan mode that's a write-side effect even
    # if the verification itself is read-only-ish).
    "create_security_report",
    "open_security_report",
    "set_security_scope",
    "scan_attack_surface",
    "review_surface_item",
    "add_observation",
    "add_security_finding",
    "attach_security_proof",
    "attach_remediation_patch",
    "confirm_security_severity",
    "approve_security_finding",
    "refute_security_finding",
    "security_report",
    "verify_security_proof",
    "verify_remediation",
    # Teacher-mode mutators (course state, file-system artifact writes,
    # subprocess grading invocations).
    "create_course",
    "record_diagnostic",
    "update_learner_profile",
    "propose_curriculum",
    "start_lesson",
    "assign_exercise",
    "submit_assignment",
    "grade_assignment",
    "decide_next",
    "record_dialog_turn",
    "close_dialog",
    "complete_module",
    "finalize_course",
    "complete_review",
    "teach_check",
    "assess_response",
    "skip_lecture",
    "teach_sources",
    # Dual-presentation write tools (author lecture.md + exercise files
    # to disk; blocked in plan mode like other teacher mutators).
    "write_lecture_transcript",
    "register_exercise_file",
    # Memory lifecycle mutators (status transitions mutate store state;
    # blocked in plan mode consistent with save_memory behavior).
    "update_memory_status",
    "supersede_memory",
    "retire_memory",
    "reactivate_memory",
    "archive_memory",
    # Research export writes the ledger's supporting files to disk.
    "research_export",
    # Debug-mode runs execute commands (run_check refuses them too); the case,
    # hypotheses and suspects stay editable in plan mode.
    "record_repro",
    "verify_fix",
    "record_regression_test",
    "bisect_repro",
    "loop_check_done",
}


def _build_envelope(tool_name: str) -> dict:
    """Build a tool-result envelope that mimics what `_handle_*` returns
    when a tool is denied. The schema matches `_build_tool_envelope` in
    `mu/tools/_envelope.py` so the agent loop's structured-result wrapping does
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


def set_plan_mode(session, enabled: bool, *, persist: bool = True, announce: bool = True) -> bool:
    """Set ``session.variables['plan_mode']`` and propagate it to every UI.

    Shared by ``/plan`` and the GUI ``POST /api/variables/plan_mode`` route so
    both paths have identical side effects: the input handler is refreshed
    (terminal prompt badge), a ``plan_mode_changed`` event is published for
    web/mobile indicators, and — for terminal UIs — a one-line banner is shown.
    ``persist`` saves the session (the GUI route persists under its own lock).
    """
    value = bool(enabled)
    variables = getattr(session, "variables", None)
    if variables is None:
        return value
    variables["plan_mode"] = value

    if persist and hasattr(session, "session_manager") and hasattr(session, "folder_context"):
        try:
            session.session_manager.save_history(session.folder_context)
        except Exception:
            pass

    ui = getattr(session, "ui", None)
    if ui is not None and hasattr(ui, "set_variables"):
        try:
            ui.set_variables(variables)
        except Exception:
            pass

    publish = getattr(ui, "publish_event", None) if ui is not None else None
    if callable(publish):
        # Web/mobile clients render their own persistent indicator; Rich
        # markup banners would show up as raw text there.
        try:
            publish({"kind": "plan_mode_changed", "plan_mode": value})
        except Exception:
            pass
    elif announce and ui is not None and hasattr(ui, "show_info"):
        try:
            from mu.ui.markup import show_notice

            if value:
                show_notice(
                    ui,
                    "plan",
                    "PLAN MODE ENABLED · Write-side tools are blocked. /plan off to disable.",
                )
            else:
                show_notice(
                    ui,
                    "ok",
                    "PLAN MODE DISABLED · Write-side tools are unblocked.",
                )
        except Exception:
            pass
    return value


# Auto-install on import. Tests can `remove("plan_mode_block_writes")` to
# disable, then re-install.
install()


__all__ = ["WRITE_TOOLS", "install", "is_plan_mode", "set_plan_mode"]
