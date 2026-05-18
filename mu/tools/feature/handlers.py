"""Feature-mode `@tool` registrations.

Each tool's body still lives in `core/tools.py` (close to the
`core/feature_mode.py` engine it drives); this module pulls them into
the new registry through the `@tool` decorator. Migration is
intentionally descriptor-only — moving 18 handler bodies in one shot is
high-risk, and the underlying `feature_mode` module itself stays in
`core/` until the Phase 6 namespace rename.
"""

from typing import Any, Dict

from mu.tools import tool


def _legacy(handler_name: str):
    """Look up the legacy `_handle_<tool>` function on `core.tools`.

    Resolution happens at call time so the decorator can register at
    import without forcing `core.tools` to be fully loaded yet (it's
    big and slow). On call, `getattr` raises a clear AttributeError if
    a handler has been deleted out from under us — easier to debug
    than a stale closure."""

    from core import tools as _legacy_tools

    handler = getattr(_legacy_tools, handler_name, None)
    if handler is None:
        raise RuntimeError(
            f"Legacy feature handler {handler_name!r} missing from core.tools; "
            "this is a refactor bug — the descriptor moved but the body did not."
        )
    return handler


# ---------------------------------------------------------------- planning


@tool(
    name="create_feature",
    description=(
        "Creates (or upserts) a feature shell from a confirmed design "
        "plan. Stage 1 of feature mode planning."
    ),
    parameters={
        "type": "object",
        "properties": {
            "feature_name": {"type": "string"},
            "feature_request": {"type": "string"},
            "feature_id": {"type": "string"},
            "design_plan": {"type": "string"},
        },
        "required": ["feature_name", "feature_request"],
    },
    requires_approval=False,
)
def create_feature(args: Dict[str, Any], context) -> str:
    return _legacy("_handle_create_feature")(args, context)


@tool(
    name="create_phases",
    description=(
        "Creates or replaces phases/epics for an active feature. Stage 2 "
        "of feature mode planning."
    ),
    parameters={
        "type": "object",
        "properties": {
            "feature_id": {"type": "string"},
            "replace_existing": {"type": "boolean", "default": True},
            "phases": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "title": {"type": "string"},
                        "goal": {"type": "string"},
                        "order": {"type": "integer"},
                        "status": {"type": "string"},
                    },
                    "required": ["title", "goal"],
                },
            },
        },
        "required": ["phases"],
    },
    requires_approval=False,
)
def create_phases(args: Dict[str, Any], context) -> str:
    return _legacy("_handle_create_phases")(args, context)


@tool(
    name="create_task",
    description=(
        "Creates a single task/ticket for an active feature phase. "
        "Stage 3 of feature mode planning."
    ),
    parameters={
        "type": "object",
        "properties": {
            "feature_id": {"type": "string"},
            "phase_id": {"type": "integer"},
            "title": {"type": "string"},
            "overview": {"type": "string"},
            "design": {"type": "array", "items": {"type": "string"}},
            "exit_criteria": {"type": "array", "items": {"type": "string"}},
            "notes": {"type": "string"},
        },
        "required": ["title", "exit_criteria"],
    },
    requires_approval=False,
)
def create_task(args: Dict[str, Any], context) -> str:
    return _legacy("_handle_create_task")(args, context)


@tool(
    name="get_execution_state",
    description=(
        "Returns the phase/task execution cursor, including blocked "
        "tasks and next actionable work item."
    ),
    parameters={
        "type": "object",
        "properties": {"feature_id": {"type": "string"}},
    },
    requires_approval=False,
)
def get_execution_state(args: Dict[str, Any], context) -> str:
    return _legacy("_handle_get_execution_state")(args, context)


# ---------------------------------------------------------------- status


@tool(
    name="block_task",
    description=(
        "Moves a task to blocked with an explicit reason and optional "
        "user input request."
    ),
    parameters={
        "type": "object",
        "properties": {
            "feature_id": {"type": "string"},
            "task_id": {"type": "integer"},
            "reason": {"type": "string"},
            "requested_input": {"type": "string"},
        },
        "required": ["task_id", "reason"],
    },
    requires_approval=False,
)
def block_task(args: Dict[str, Any], context) -> str:
    return _legacy("_handle_block_task")(args, context)


@tool(
    name="resume_task",
    description=(
        "Moves a blocked task back to in_progress after required user "
        "input has been provided."
    ),
    parameters={
        "type": "object",
        "properties": {
            "feature_id": {"type": "string"},
            "task_id": {"type": "integer"},
            "notes": {"type": "string"},
        },
        "required": ["task_id"],
    },
    requires_approval=False,
)
def resume_task(args: Dict[str, Any], context) -> str:
    return _legacy("_handle_resume_task")(args, context)


@tool(
    name="archive_task",
    description=(
        "Archives an archive-ready task after review and diff decisions "
        "are complete."
    ),
    parameters={
        "type": "object",
        "properties": {
            "feature_id": {"type": "string"},
            "task_id": {"type": "integer"},
        },
        "required": ["task_id"],
    },
    requires_approval=False,
)
def archive_task(args: Dict[str, Any], context) -> str:
    return _legacy("_handle_archive_task")(args, context)


@tool(
    name="update_task_status",
    description=(
        "Updates the status of a specific task. Provide "
        "verified_exit_criteria incrementally as criteria are met; set "
        "status='completed' only after all task exit_criteria are verified."
    ),
    parameters={
        "type": "object",
        "properties": {
            "task_id": {"type": "integer"},
            "status": {
                "type": "string",
                "enum": [
                    "pending",
                    "not_started",
                    "in_progress",
                    "blocked",
                    "completed",
                    "archived",
                ],
            },
            "notes": {"type": "string"},
            "verified_exit_criteria": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Exit criteria already verified for this task. Update "
                    "incrementally as work progresses; must include every "
                    "task exit criterion before completion."
                ),
            },
            "directory": {"type": "string"},
        },
        "required": ["task_id", "status"],
    },
    requires_approval=False,
)
def update_task_status(args: Dict[str, Any], context) -> str:
    return _legacy("_handle_update_task_status")(args, context)


# ---------------------------------------------------------------- review


@tool(
    name="review_completed_tasks",
    description=(
        "Creates structured review records for completed tasks with "
        "categorized issues (bug/risk/enhancement)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "feature_id": {"type": "string"},
            "task_id": {"type": "integer"},
            "summary": {"type": "string"},
            "limitations": {"type": "array", "items": {"type": "string"}},
            "issues": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "title": {"type": "string"},
                        "category": {
                            "type": "string",
                            "enum": ["bug", "risk", "enhancement"],
                        },
                        "details": {"type": "string"},
                    },
                    "required": ["title", "category"],
                },
            },
        },
        "required": ["task_id", "summary"],
    },
    requires_approval=False,
)
def review_completed_tasks(args: Dict[str, Any], context) -> str:
    return _legacy("_handle_review_completed_tasks")(args, context)


@tool(
    name="review_all_completed_tasks",
    description=(
        "Auto-creates baseline review records for every completed task "
        "that does not yet have one."
    ),
    parameters={
        "type": "object",
        "properties": {"feature_id": {"type": "string"}},
    },
    requires_approval=False,
)
def review_all_completed_tasks(args: Dict[str, Any], context) -> str:
    return _legacy("_handle_review_all_completed_tasks")(args, context)


@tool(
    name="propose_task_diff",
    description=(
        "Creates a diff proposal for a review issue, requiring later "
        "user decision."
    ),
    parameters={
        "type": "object",
        "properties": {
            "feature_id": {"type": "string"},
            "review_id": {"type": "string"},
            "issue_id": {"type": "string"},
            "diff": {"type": "string"},
        },
        "required": ["review_id", "issue_id", "diff"],
    },
    requires_approval=False,
)
def propose_task_diff(args: Dict[str, Any], context) -> str:
    return _legacy("_handle_propose_task_diff")(args, context)


@tool(
    name="decide_task_diff",
    description="Stores user decision (approved/denied) for a proposed task diff.",
    parameters={
        "type": "object",
        "properties": {
            "feature_id": {"type": "string"},
            "proposal_id": {"type": "string"},
            "decision": {"type": "string", "enum": ["approved", "denied"]},
            "reason": {"type": "string"},
        },
        "required": ["proposal_id", "decision"],
    },
    requires_approval=False,
)
def decide_task_diff(args: Dict[str, Any], context) -> str:
    return _legacy("_handle_decide_task_diff")(args, context)


# ---------------------------------------------------------------- feature-task engine


@tool(
    name="create_feature_task",
    description=(
        "Creates a structured feature implementation plan consisting of "
        "one or more tasks. Each task must include explicit "
        "exit_criteria. Stores metadata internally."
    ),
    parameters={
        "type": "object",
        "properties": {
            "feature_name": {
                "type": "string",
                "description": "Short feature name.",
            },
            "feature_request": {
                "type": "string",
                "description": "Full description of the feature request.",
            },
            "feature_id": {
                "type": "string",
                "description": "Optional stable identifier.",
            },
            "tasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "objectives": {"type": "array", "items": {"type": "string"}},
                        "action_points": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "exit_criteria": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "notes": {"type": "string"},
                    },
                    "required": [
                        "title",
                        "objectives",
                        "action_points",
                        "exit_criteria",
                    ],
                },
            },
        },
        "required": ["feature_name", "feature_request", "tasks"],
    },
    requires_approval=False,
)
def create_feature_task(args: Dict[str, Any], context) -> str:
    return _legacy("_handle_create_feature_task")(args, context)


@tool(
    name="update_feature_task",
    description="Modifies the details of a task before approval.",
    parameters={
        "type": "object",
        "properties": {
            "task_id": {"type": "integer"},
            "title": {"type": "string"},
            "objectives": {"type": "array", "items": {"type": "string"}},
            "action_points": {"type": "array", "items": {"type": "string"}},
            "exit_criteria": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["task_id"],
    },
    requires_approval=True,
)
def update_feature_task(args: Dict[str, Any], context) -> str:
    return _legacy("_handle_update_feature_task")(args, context)


@tool(
    name="approve_feature_task",
    description="Approves the feature plan, allowing implementation to begin.",
    parameters={
        "type": "object",
        "properties": {
            "approved": {"type": "boolean", "default": True},
        },
    },
    requires_approval=True,
)
def approve_feature_task(args: Dict[str, Any], context) -> str:
    return _legacy("_handle_approve_feature_task")(args, context)


@tool(
    name="get_current_task",
    description="Retrieves the currently active task in the feature plan.",
    parameters={"type": "object", "properties": {}},
    requires_approval=False,
    execution_kind="read",
    preview_policy="none",
    result_mode="structured+collated",
)
def get_current_task(args: Dict[str, Any], context) -> str:
    return _legacy("_handle_get_current_task")(args, context)


@tool(
    name="get_tasks",
    description=(
        "Retrieves all tasks in the feature plan (previous, current, "
        "and upcoming)."
    ),
    parameters={"type": "object", "properties": {}},
    requires_approval=False,
    execution_kind="read",
    preview_policy="none",
    result_mode="structured+collated",
)
def get_tasks(args: Dict[str, Any], context) -> str:
    return _legacy("_handle_get_tasks")(args, context)


# ---------------------------------------------------------------- blocker


@tool(
    name="raise_blocker",
    description=(
        "Raises a structured blocker when the feature loop needs user "
        "input or an external decision before it can safely continue."
    ),
    parameters={
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "Short blocker summary shown to the user.",
            },
            "details": {
                "type": "string",
                "description": (
                    "Longer explanation of what is blocked and what has "
                    "already been tried."
                ),
            },
            "requested_input": {
                "type": "string",
                "description": (
                    "Describe the exact information or decision needed from the user."
                ),
            },
            "questions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional focused questions for the user to answer.",
            },
        },
        "required": ["summary", "requested_input"],
    },
    requires_approval=False,
    execution_kind="control",
    preview_policy="none",
    result_mode="structured",
    server_policy="session_only",
    summary_builder="blocker_summary",
)
def raise_blocker(args: Dict[str, Any], context) -> str:
    """`_handle_raise_blocker` uses the legacy 4-arg shape; adapt."""
    legacy_handler = _legacy("_handle_raise_blocker")
    return legacy_handler(
        args, context.folder_context, context.ui, context.variables
    )
