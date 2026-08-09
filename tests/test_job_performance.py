from __future__ import annotations

from mu.jobs import JobService, JobSpec, JobStore
from mu.jobs.management import JobManagementService
from mu.jobs.performance import build_job_performance, compare_job_performance
from mu.jobs.verification import VerificationRun, VerificationStore


def make_service(tmp_path):
    return JobService(JobStore(str(tmp_path / "jobs.sqlite3")))


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

    # The first verification passes. A later reviewer-requested change can
    # legitimately cause another verification run without erasing first-pass
    # success for the original implementation.
    save_verification(verification_store, job.id, run_id="first", started=100, passed=True)
    save_verification(verification_store, job.id, run_id="second", started=200, passed=False)

    analysis = build_job_performance(service, job.id)

    assert [run["id"] for run in analysis["verifications"]] == ["first", "second"]
    assert analysis["summary"]["first_pass_verification"] is True


def test_job_performance_comparison_preserves_signed_deltas(tmp_path):
    service = make_service(tmp_path)
    primary = service.create(JobSpec(title="Primary"))
    reference = service.create(JobSpec(title="Reference"))
    service.cancel(primary.id, reason="done")
    service.cancel(reference.id, reason="done")
    service.store.update_runtime_fields(primary.id, cost_usd=1.0)
    service.store.update_runtime_fields(reference.id, cost_usd=2.5)

    result = compare_job_performance(service, primary.id, reference.id)
    cost = next(item for item in result["comparison"]["metrics"] if item["key"] == "cost_usd")

    assert cost["primary"] == 1.0
    assert cost["comparison"] == 2.5
    assert cost["delta"] == -1.5
    assert cost["lower_is_better"] is True
