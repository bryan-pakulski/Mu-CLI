from __future__ import annotations

from mu.jobs import JobService, JobSpec, JobStatus, JobStore
from mu.jobs.management import JobManagementService
from mu.jobs.performance import build_job_performance
from mu.jobs.verification import VerificationRun, VerificationStore


class Clock:
    def __init__(self, value: float = 1000.0):
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def make_service(tmp_path, clock=None):
    return JobService(JobStore(str(tmp_path / "jobs.sqlite3"), clock=clock or Clock()))


def save_verification(store, job_id, *, run_id, started, passed):
    return store.save(VerificationRun(
        id=run_id,
        job_id=job_id,
        status="passed" if passed else "failed",
        passed=passed,
        started_at=started,
        finished_at=started + 1,
        duration_ms=1000,
        base_sha="base",
        head_sha="head",
        branch="mu/test",
        worktree="/tmp/worktree",
        checks=[],
        changed_files=[],
        additions=0,
        deletions=0,
        diff_stat="",
        dirty=False,
        dirty_status="",
        summary={},
    ))


def test_performance_reads_archive_state_from_management_table(tmp_path):
    service = make_service(tmp_path)
    job = service.create(JobSpec(title="Archived analysis"))
    service.cancel(job.id, reason="complete")
    JobManagementService(service).archive(job.id, reason="historic")

    analysis = build_job_performance(service, job.id)

    assert analysis["job"]["archived"] is True
    assert analysis["job"]["archived_at"] is not None
    assert analysis["job"]["archived_reason"] == "historic"


def test_first_pass_verification_tracks_original_run_not_total_run_count(tmp_path):
    service = make_service(tmp_path)
    job = service.create(JobSpec(title="Verification history"))
    verification_store = VerificationStore(service.store)
    save_verification(verification_store, job.id, run_id="first", started=100, passed=True)
    save_verification(verification_store, job.id, run_id="second", started=200, passed=False)

    analysis = build_job_performance(service, job.id)

    assert [run["id"] for run in analysis["verifications"]] == ["first", "second"]
    assert analysis["summary"]["first_pass_verification"] is True


def test_environment_error_residence_is_stopped_not_active_execution(tmp_path):
    clock = Clock()
    service = make_service(tmp_path, clock)
    job = service.create(JobSpec(title="Worktree failure"))

    clock.advance(10)
    service.transition(job.id, JobStatus.PREPARING, reason="worker preparing isolated workspace")
    clock.advance(5)
    service.transition(
        job.id,
        JobStatus.ENVIRONMENT_ERROR,
        reason="could not prepare isolated Git worktree",
        payload={"stage": "worktree_add", "error": "fatal: path already registered"},
    )
    # The job remains stopped for 37 minutes. This is state residence, not
    # 37 minutes of active environment-error work.
    clock.advance(37 * 60)
    service.retry(job.id, reason="operator retry")
    clock.advance(1)
    service.cancel(job.id, reason="test complete")

    analysis = build_job_performance(service, job.id)
    interval = next(item for item in analysis["phase_intervals"] if item["status"] == "environment_error")

    assert interval["classification"] == "stopped"
    assert interval["active_execution"] is False
    assert interval["passive_residence"] is True
    assert interval["duration_seconds"] == 37 * 60
    assert interval["agent_event_count"] == 0
    assert "not agent execution time" in interval["interpretation"] or "no worker/agent activity" in interval["interpretation"]
    assert interval["entry_event"]["summary"].startswith("preparing → environment_error")
    assert interval["exit_event"]["summary"].startswith("environment_error → queued")
    assert analysis["summary"]["stopped_seconds"] == 37 * 60
    assert analysis["summary"]["passive_seconds"] >= 37 * 60


def test_current_environment_error_continues_accruing_passive_residence(tmp_path):
    clock = Clock()
    service = make_service(tmp_path, clock)
    job = service.create(JobSpec(title="Current worktree failure"))

    clock.advance(4)
    service.transition(job.id, JobStatus.PREPARING, reason="prepare")
    clock.advance(2)
    service.transition(job.id, JobStatus.ENVIRONMENT_ERROR, reason="git failed")
    clock.advance(30 * 60)

    analysis = build_job_performance(service, job.id)
    interval = analysis["phase_intervals"][-1]

    assert service.get(job.id).terminal is False
    assert interval["status"] == "environment_error"
    assert interval["classification"] == "stopped"
    assert interval["duration_seconds"] == 30 * 60
    assert analysis["summary"]["stopped_seconds"] == 30 * 60
    assert analysis["summary"]["elapsed_seconds"] == 4 + 2 + 30 * 60


def test_old_job_reports_missing_harness_trace_instead_of_inventing_detail(tmp_path):
    service = make_service(tmp_path)
    job = service.create(JobSpec(title="Old job"))
    service.cancel(job.id, reason="done")

    analysis = build_job_performance(service, job.id)

    assert analysis["runtime_trace"]["available"] is False
    assert "never reached" in analysis["runtime_trace"]["reason"].lower()
