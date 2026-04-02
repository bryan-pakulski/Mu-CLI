from core.feature_mode import (
    STATUS_BLOCKED,
    STATUS_COMPLETED,
    STATUS_IN_PROGRESS,
    create_feature_plan,
    create_feature_phases,
    create_feature_task,
    feature_execution_snapshot,
    next_actionable_task,
    next_pending_phase,
    recalculate_phase_statuses,
    transition_task_status,
)


def test_execution_snapshot_tracks_next_phase_and_task(tmp_path):
    plan = create_feature_plan(
        feature_name="Execution",
        feature_request="Execution engine",
        tasks_data=[],
        metadata_path=str(tmp_path / "feature_plan.json"),
        feature_id="execution",
    )
    plan = create_feature_phases(
        plan.metadata_path,
        [{"id": 1, "title": "Phase 1", "goal": "Do work", "order": 1}],
    )
    plan, _ = create_feature_task(
        plan.metadata_path,
        {
            "phase_id": 1,
            "title": "Task A",
            "objectives": ["Do A"],
            "action_points": ["Implement"],
            "exit_criteria": ["Done"],
        },
    )

    snapshot = feature_execution_snapshot(plan)
    assert snapshot["next_phase"]["id"] == 1
    assert snapshot["next_task"]["title"] == "Task A"


def test_phase_status_rolls_forward_from_task_updates(tmp_path):
    plan = create_feature_plan(
        feature_name="Status",
        feature_request="Phase status",
        tasks_data=[],
        metadata_path=str(tmp_path / "feature_plan.json"),
        feature_id="status",
    )
    plan = create_feature_phases(
        plan.metadata_path,
        [{"id": 1, "title": "Phase 1", "goal": "Goal", "order": 1}],
    )
    plan, task = create_feature_task(
        plan.metadata_path,
        {
            "phase_id": 1,
            "title": "Task A",
            "exit_criteria": ["Done"],
        },
    )

    transition_task_status(plan, task_id=task.id, to_status=STATUS_IN_PROGRESS)
    recalculate_phase_statuses(plan)
    assert next_pending_phase(plan).status == STATUS_IN_PROGRESS

    transition_task_status(plan, task_id=task.id, to_status=STATUS_BLOCKED)
    recalculate_phase_statuses(plan)
    assert next_pending_phase(plan).status == STATUS_BLOCKED

    transition_task_status(plan, task_id=task.id, to_status=STATUS_IN_PROGRESS)
    transition_task_status(plan, task_id=task.id, to_status=STATUS_COMPLETED)
    recalculate_phase_statuses(plan)
    assert next_pending_phase(plan) is None
    assert next_actionable_task(plan) is None
