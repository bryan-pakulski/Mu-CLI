"""Backward-compatible re-export shim.

The body of this module moved to `mu/feature/engine.py` during the
Phase 6 namespace rename. New code should import from ``mu.feature.engine``.
"""

from mu.feature.engine import *  # noqa: F401,F403
from mu.feature.engine import _workspace_root  # noqa: F401 — used by core.tools
from mu.feature.engine import (  # noqa: F401
    ALLOWED_TASK_TRANSITIONS,
    STATUS_ARCHIVED,
    STATUS_BLOCKED,
    STATUS_COMPLETED,
    STATUS_IN_PROGRESS,
    STATUS_NOT_STARTED,
    STATUS_PENDING,
    STATUS_REVIEW_PENDING,
    VALID_TASK_STATUSES,
    DiffProposal,
    FeatureEvent,
    FeaturePhase,
    FeaturePlan,
    FeatureTask,
    TaskReviewRecord,
    archive_task,
    build_phase_execution_prompt,
    build_review_prompt,
    create_diff_proposal,
    create_feature_phases,
    create_feature_plan,
    create_feature_shell,
    create_feature_task,
    create_task_review_record,
    decide_diff_proposal,
    feature_execution_snapshot,
    is_valid_task_transition,
    load_feature_plan,
    next_actionable_task,
    next_pending_phase,
    normalize_task_status,
    recalculate_phase_statuses,
    refresh_and_persist_feature_plan,
    review_all_completed_tasks,
    save_feature_plan,
    summarize_feature_plan,
    task_archive_ready,
    transition_task_status,
    update_feature_plan_metadata,
    update_task_content,
    update_task_status,
)
