from __future__ import annotations

from mu.jobs import AttentionReason, JobService, JobSpec, JobStatus, JobStore
from mu.jobs.controller import JobController
from mu.jobs.runner import JobRunOutcome


class FakeRunner:
    def __init__(self, outcome: JobRunOutcome):
        self.outcome = outcome
        self.calls = []

    @staticmethod
    def session_name(job):
        return f"job-{job.id[:20]}"

    def run(self, job, attempt):
        self.calls.append((job, attempt))
        return self.outcome


def service(tmp_path):
    return JobService(JobStore(str(tmp_path / "jobs.sqlite3")))


def queued_job(svc, title="Ticket"):
    return svc.create(JobSpec(
        title=title,
        repository=str(title),
        execution={
            "provider": "openai",
            "model": "test-model",
            "agent_mode": "default",
            "session_type": "workspace",
        },
    ))


def test_completed_attempt_stops_at_verifying_until_real_verifier_exists(tmp_path):
    svc = service(tmp_path)
    job = queued_job(svc)
    runner = FakeRunner(JobRunOutcome(kind="completed", status="completed", cost_usd=1.25))
    controller = JobController(svc, runner, max_workers=1)

    controller._execute_job(job.id)

    current = svc.get(job.id)
    assert current.status == JobStatus.VERIFYING
    assert current.cost_usd == 1.25
    assert current.worker_id == ""
    attempts = svc.attempts(job.id)
    assert len(attempts) == 1
    assert attempts[0].status == "completed"
    assert attempts[0].cost_usd == 1.25
    assert any(event.event_type == "verification_pending" for event in svc.events(job.id))
    assert not any(event.to_status == JobStatus.READY_FOR_REVIEW for event in svc.events(job.id))


def test_human_gate_is_durable_and_resume_requeues_same_job(tmp_path):
    svc = service(tmp_path)
    job = queued_job(svc)
    runner = FakeRunner(JobRunOutcome(
        kind="needs_human",
        status="needs_human",
        cost_usd=0.3,
        attention_reason=AttentionReason.APPROVAL_REQUIRED,
        attention_detail="Approval required for write_file",
        attention_payload={"tool_name": "write_file"},
    ))
    controller = JobController(svc, runner, max_workers=1)

    controller._execute_job(job.id)
    blocked = svc.get(job.id)
    assert blocked.status == JobStatus.NEEDS_HUMAN
    assert blocked.attention_reason == AttentionReason.APPROVAL_REQUIRED
    assert blocked.cost_usd == 0.3
    assert svc.attempts(job.id)[0].status == "needs_human"

    resumed = svc.resume(job.id, detail="Approved; continue")
    assert resumed.status == JobStatus.QUEUED
    assert resumed.attention_reason == AttentionReason.NONE
    assert any(event.event_type == "human_response" for event in svc.events(job.id))


def test_environment_failure_is_not_reported_as_ready(tmp_path):
    svc = service(tmp_path)
    job = queued_job(svc)
    runner = FakeRunner(JobRunOutcome(
        kind="failed",
        status="environment_error",
        error="workspace missing",
    ))
    controller = JobController(svc, runner, max_workers=1)

    controller._execute_job(job.id)

    current = svc.get(job.id)
    assert current.status == JobStatus.ENVIRONMENT_ERROR
    assert svc.attempts(job.id)[0].status == "failed"
    assert current.worker_id == ""


def test_recovering_job_can_be_released_to_new_attempt(tmp_path):
    svc = service(tmp_path)
    job = queued_job(svc)
    svc.transition(job.id, JobStatus.PREPARING)
    svc.transition(job.id, JobStatus.RUNNING)
    assert svc.acquire(job.id, "dead-worker", ttl_seconds=5)
    # Model crash recovery by releasing stale ownership then moving to RECOVERING.
    svc.release(job.id, "dead-worker", reason="test crash")
    svc.transition(job.id, JobStatus.RECOVERING, reason="worker lost")

    runner = FakeRunner(JobRunOutcome(kind="completed", status="completed"))
    controller = JobController(svc, runner, max_workers=1)
    controller._execute_job(job.id)

    assert svc.get(job.id).status == JobStatus.VERIFYING
    assert len(svc.attempts(job.id)) == 1


def test_controller_snapshot_advertises_serial_milestone_one_boundary(tmp_path):
    svc = service(tmp_path)
    runner = FakeRunner(JobRunOutcome(kind="completed"))
    controller = JobController(svc, runner, max_workers=1)
    snapshot = controller.snapshot()
    assert snapshot["max_workers"] == 1
    assert snapshot["active_jobs"] == []
