from __future__ import annotations

from pathlib import Path

from mu.jobs import JobService, JobSpec, JobStore, JobStatus
from mu.jobs.diagnostics import build_job_diagnostics


def service(tmp_path):
    return JobService(JobStore(str(tmp_path / "jobs" / "jobs.sqlite3")))


def test_diagnostics_collects_failure_events_and_safe_worker_log_tail(tmp_path):
    svc = service(tmp_path)
    job = svc.create(JobSpec(title="Diagnose me", repository="/repo"))
    svc.transition(job.id, JobStatus.PREPARING, reason="worker preparing isolated workspace")
    svc.store.append_event(
        job.id,
        "worktree_prepare_failed",
        reason="fatal: invalid reference: main",
        payload={
            "stage": "base_resolution",
            "command": ["git", "-C", "/repo", "rev-parse", "main^{commit}"],
            "return_code": 128,
            "stderr": "fatal: invalid reference: main",
        },
    )
    svc.transition(
        job.id,
        JobStatus.ENVIRONMENT_ERROR,
        reason="could not prepare isolated Git worktree",
        payload={"error": "fatal: invalid reference: main"},
    )

    log_dir = Path(svc.store.path).parent / "logs"
    log_dir.mkdir(parents=True)
    log_path = log_dir / f"{job.id}.log"
    log_path.write_text("first line\nworker traceback line\n", encoding="utf-8")

    snapshot = build_job_diagnostics(svc, job.id, log_tail_bytes=4096)

    assert snapshot.status == "environment_error"
    assert snapshot.latest_failure is not None
    assert snapshot.latest_failure["event_type"] == "status_changed"
    assert snapshot.worker_log_path == str(log_path)
    assert snapshot.worker_log_exists is True
    assert snapshot.worker_log_size == log_path.stat().st_size
    assert "worker traceback line" in snapshot.worker_log_tail
    assert snapshot.worker_log_truncated is False
    assert any(event["event_type"] == "worktree_prepare_failed" for event in snapshot.diagnostics)


def test_diagnostics_log_path_is_derived_from_job_id_not_event_payload(tmp_path):
    svc = service(tmp_path)
    job = svc.create(JobSpec(title="Safe logs", repository="/repo"))
    secret = tmp_path / "not-a-job-log.txt"
    secret.write_text("do not expose me", encoding="utf-8")
    svc.store.append_event(
        job.id,
        "worker_process_started",
        payload={"log_path": str(secret), "pid": 12},
    )

    snapshot = build_job_diagnostics(svc, job.id)

    assert snapshot.worker_log_path == str(Path(svc.store.path).parent / "logs" / f"{job.id}.log")
    assert snapshot.worker_log_exists is False
    assert "do not expose me" not in snapshot.worker_log_tail


def test_diagnostics_returns_only_bounded_log_tail(tmp_path):
    svc = service(tmp_path)
    job = svc.create(JobSpec(title="Big log", repository="/repo"))
    log_dir = Path(svc.store.path).parent / "logs"
    log_dir.mkdir(parents=True)
    log_path = log_dir / f"{job.id}.log"
    log_path.write_text("A" * 8000 + "THE-END", encoding="utf-8")

    snapshot = build_job_diagnostics(svc, job.id, log_tail_bytes=2048)

    assert snapshot.worker_log_truncated is True
    assert snapshot.worker_log_size > len(snapshot.worker_log_tail)
    assert snapshot.worker_log_tail.endswith("THE-END")
    assert len(snapshot.worker_log_tail.encode("utf-8")) <= 2048
