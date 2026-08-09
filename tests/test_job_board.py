from __future__ import annotations

from mu.jobs import AttentionReason, JobService, JobSpec, JobStatus, JobStore
from mu.jobs.board import build_job_board, bucket_for


def service(tmp_path):
    return JobService(JobStore(str(tmp_path / "jobs.sqlite3")))


def make_job(svc, title):
    return svc.create(JobSpec(title=title))


def test_shared_board_buckets_jobs_consistently_for_all_control_planes(tmp_path):
    svc = service(tmp_path)
    queued = make_job(svc, "Queued")
    running = make_job(svc, "Running")
    ready = make_job(svc, "Ready")
    failed = make_job(svc, "Failed")
    needs = make_job(svc, "Needs you")
    done = make_job(svc, "Done")

    svc.transition(running.id, JobStatus.PREPARING)
    svc.transition(running.id, JobStatus.RUNNING)

    svc.transition(ready.id, JobStatus.PREPARING)
    svc.transition(ready.id, JobStatus.RUNNING)
    svc.transition(ready.id, JobStatus.VERIFYING)
    svc.transition(ready.id, JobStatus.READY_FOR_REVIEW)

    svc.transition(failed.id, JobStatus.PREPARING)
    svc.transition(failed.id, JobStatus.FAILED)

    svc.transition(needs.id, JobStatus.PREPARING)
    svc.transition(needs.id, JobStatus.RUNNING)
    svc.require_human(needs.id, AttentionReason.QUESTION, "Choose behavior")

    svc.cancel(done.id)

    board = build_job_board(svc)
    assert board.counts == {
        "needs_you": 1,
        "running": 1,
        "queued": 1,
        "ready": 1,
        "failed": 1,
        "done": 1,
    }
    assert board.needs_you[0].id == needs.id
    assert board.running[0].id == running.id
    assert board.queued[0].id == queued.id
    assert board.ready[0].id == ready.id
    assert board.failed[0].id == failed.id
    assert board.done[0].id == done.id


def test_conflicted_is_always_attention_not_generic_failure(tmp_path):
    svc = service(tmp_path)
    job = make_job(svc, "Conflict")
    svc.transition(job.id, JobStatus.PREPARING)
    svc.transition(job.id, JobStatus.RUNNING)
    svc.transition(job.id, JobStatus.VERIFYING)
    svc.transition(job.id, JobStatus.CONFLICTED)
    assert bucket_for(svc.get(job.id)) == "needs_you"
