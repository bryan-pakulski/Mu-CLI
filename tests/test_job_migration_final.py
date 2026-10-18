"""Migration invariants across worker, UI commands and durable artifacts."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mu.jobs.core import vm
from mu.jobs.core.artifacts import artifacts_for
from mu.jobs.core.commands import CommandRejected, dispatch
from mu.jobs.core.states import JobState
from mu.jobs.sandbox.runner import StageRestart, check_restart, claim_stage_response
from test_job_core_api import _at_plan_review, _service
from test_job_worker import running_job


def test_approval_and_worker_requeue_do_not_restart_plan_step(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    service = _service(tmp_path)
    job = _at_plan_review(service, tmp_path)
    dispatch(service, {"type":"approvePlan", "job_id":job.id, "version":1})
    assert service.store.core_state(job.id).state == JobState.IMPLEMENTING
    service.transition(job.id, "preparing")
    assert service.store.core_state(job.id).state == JobState.IMPLEMENTING
    assert vm.job_vm(service, job.id)["pipeline"][2]["status"] == "active"


def test_owned_attempt_finish_updates_core_in_same_transaction(tmp_path):
    service, _, job, attempt, _ = running_job(tmp_path)
    assert service.store.finish_attempt_owned(attempt.id, worker_id="worker", status="completed", target_status="verifying")
    assert service.store.core_state(job.id).state == JobState.CHECKING
    assert service.events(job.id)[-1].payload["core_state"]["state"] == "checking"


def test_reopening_repairs_early_v3_completion_but_preserves_core_scheduling(tmp_path, monkeypatch):
    from mu.jobs.store import JobStore
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    service = _service(tmp_path)
    job = _at_plan_review(service, tmp_path)
    dispatch(service, {"type":"approvePlan", "job_id":job.id, "version":1})
    reopened = JobStore(service.store.path)
    assert reopened.core_state(job.id).state == JobState.IMPLEMENTING
    # Early v3 completion committed the legacy status without syncing core.
    with reopened._transaction() as conn:
        conn.execute("UPDATE jobs SET status='verifying', state='implementing' WHERE id=?", (job.id,))
        conn.execute("UPDATE job_events SET payload_json='{}' WHERE job_id=? AND event_type='status_changed'", (job.id,))
    assert JobStore(service.store.path).core_state(job.id).state == JobState.CHECKING


def test_core_rejects_invalid_transition_without_changing_legacy_row(tmp_path):
    service, _, job, _, _ = running_job(tmp_path)
    with pytest.raises(ValueError):
        service.store.transition_core(job.id, JobState.PUSHING)
    assert service.get(job.id).status.value == "running"
    assert service.store.core_state(job.id).state == JobState.IMPLEMENTING


def test_missing_sandbox_is_a_retryable_provisioning_failure(tmp_path):
    service, _, job, _, _ = running_job(tmp_path)
    service.transition(job.id, "failed", reason="Sandbox required: build the shared image",
                       payload={"exception_type":"SandboxUnavailable"})
    assert service.store.core_state(job.id).failed_from == JobState.PROVISIONING
    dispatch(service, {"type":"retry", "job_id":job.id})
    assert service.store.core_state(job.id).state == JobState.PROVISIONING


def test_old_handoffs_are_archived_before_metadata_is_replaced(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    service = _service(tmp_path)
    job = _at_plan_review(service, tmp_path)
    before = dict(job.metadata)
    view = vm.job_vm(service, job.id)
    assert view["plan"]["markdown"]
    updated = service.get(job.id)
    assert "plan" not in updated.metadata
    assert artifacts_for(job).read_json("legacy-handoffs.json")["plan"] == before["plan"]


def test_restart_is_scoped_to_current_stage_and_new_events(tmp_path):
    service, _, job, _, _ = running_job(tmp_path)
    cursor = service.store.event_cursor()
    with pytest.raises(CommandRejected):
        dispatch(service, {"type":"restartStage", "job_id":job.id, "stage":"review"})
    dispatch(service, {"type":"restartStage", "job_id":job.id, "stage":"implement"})
    with pytest.raises(StageRestart):
        check_restart(service, job, "implement", cursor)
    check_restart(service, job, "review", cursor)
    check_restart(service, job, "implement", service.store.event_cursor())


def test_tool_approval_is_separate_from_plan_and_consumed_once_for_exact_args(tmp_path):
    service, _, job, _, _ = running_job(tmp_path)
    target = {"tool_name":"write_file", "tool_args":{"path":"widget.py", "content":"safe"}, "can_approve":True}
    service.require_human(job.id, "approval_required", "Write widget", payload=target)
    view = vm.job_vm(service, job.id)
    assert view["state"] == "needs_input" and view["paused_from"] == "implementing"
    actions = {a["id"]:a for a in view["actions"]}
    assert not actions["approvePlan"]["enabled"]
    # Jobs run unattended: Resume resolves a parked approval automatically.
    assert actions["resume"]["enabled"]
    assert actions["approveTool"]["enabled"]
    assert actions["approveTool"]["primary"]
    dispatch(service, {"type":"approveTool", "job_id":job.id, "args":actions["approveTool"]["args"]})
    payload = {"kind":"approval_required", **target}
    # The human decision is consumed once, for the exact args only; every other request is
    # answered automatically (approve in implement, deny in the read-only review stage).
    other = claim_stage_response(service, job, "implement", {**payload, "tool_args":{"path":"other.py"}})
    assert other["auto"] and other["decision"] == "approve"
    review = claim_stage_response(service, job, "review", payload)
    assert review["auto"] and review["decision"] == "deny"
    human = claim_stage_response(service, job, "implement", payload)
    assert human["decision"] == "approve" and not human.get("auto")
    assert claim_stage_response(service, job, "implement", payload)["auto"]


def test_compatibility_routes_cannot_bypass_core_gates(tmp_path):
    from mu.gui.routers.jobs import router
    service, _, job, _, _ = running_job(tmp_path)
    app = FastAPI(); app.state.job_service = service
    app.include_router(router, prefix="/api/jobs")
    with TestClient(app) as client:
        assert client.post(f"/api/jobs/{job.id}/continue", json={}).status_code == 409
        assert client.post(f"/api/jobs/{job.id}/transition", json={"status":"publishing"}).status_code == 410
    assert service.get(job.id).status.value == "running"


def test_artifact_purge_cannot_bypass_worktree_removal_guard(tmp_path, monkeypatch):
    from mu.jobs import JobSpec
    from mu.jobs.worktree import JobWorktreeManager
    from test_job_worker import repo
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    service = _service(tmp_path)
    job = service.create(JobSpec(title="New layout", repository=str(repo(tmp_path))))
    manager = JobWorktreeManager(service)
    manager.prepare(job)
    job = service.get(job.id)
    art = artifacts_for(job)
    assert job.worktree == art.path("worktree")
    with pytest.raises(OSError, match="worktree remains"):
        art.remove()
    assert manager.remove(job)
    assert art.remove()
