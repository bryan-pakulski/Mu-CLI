from __future__ import annotations

from pathlib import Path

from mu.jobs import AttentionReason, JobService, JobSpec, JobStore, JobStatus
from mu.jobs.analysis import build_job_analysis, compare_job_analyses
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


def test_diagnostics_uses_newest_events_when_event_limit_is_bounded(tmp_path):
    svc = service(tmp_path)
    job = svc.create(JobSpec(title="Long trace", repository="/repo"))
    for index in range(25):
        svc.store.append_event(job.id, "agent_message", payload={"text": f"noise {index}"})
    svc.store.append_event(
        job.id,
        "worktree_prepare_failed",
        reason="latest worktree failure",
        payload={"stage": "worktree_add_new_branch", "stderr": "boom"},
    )

    snapshot = build_job_diagnostics(svc, job.id, event_limit=5)

    assert snapshot.latest_failure is not None
    assert snapshot.latest_failure["event_type"] == "worktree_prepare_failed"
    assert snapshot.latest_failure["reason"] == "latest worktree failure"
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


def test_debug_bundle_contains_durable_trace_and_worker_log(tmp_path):
    from io import BytesIO
    from zipfile import ZipFile
    from mu.jobs.management import JobManagementService

    svc = service(tmp_path)
    job = svc.create(JobSpec(title="Bundle telemetry", repository="/repo"))
    svc.store.append_event(job.id, "runtime_error", reason="boom", payload={"error": "boom"})
    log_dir = Path(svc.store.path).parent / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / f"{job.id}.log").write_text("worker output\n", encoding="utf-8")

    bundle = JobManagementService(svc).debug_bundle(job.id)
    with ZipFile(BytesIO(bundle), "r") as archive:
        names = set(archive.namelist())
        assert {"manifest.json", "job.json", "events.json", "events.ndjson", "diagnostics.json", "worker.log", "README.txt"} <= names
        assert b"worker output" in archive.read("worker.log")
        assert b"runtime_error" in archive.read("events.ndjson")


class _AnalysisClock:
    def __init__(self, value=1000.0):
        self.value = float(value)
    def __call__(self):
        return self.value
    def advance(self, seconds):
        self.value += float(seconds)


def test_job_trace_analysis_attributes_time_attempts_tools_and_human_gates(tmp_path):
    clock = _AnalysisClock()
    svc = JobService(JobStore(str(tmp_path / "analysis.sqlite3"), clock=clock))
    job = svc.create(JobSpec(title="Analyse me", repository="/repo"))

    clock.advance(10)
    svc.transition(job.id, JobStatus.PREPARING, reason="prepare")
    clock.advance(5)
    svc.transition(job.id, JobStatus.RUNNING, reason="run")
    attempt = svc.start_attempt(job.id, worker_id="w", session_name="s")
    svc.store.append_event(job.id, "agent_message", payload={"role": "assistant", "text": "working", "model": "m"})
    svc.store.append_event(job.id, "tool_call_ui", payload={"tool_name": "read_file"})
    svc.store.append_event(job.id, "tool_call_ui", payload={"tool_name": "read_file"})
    svc.store.append_event(job.id, "tool_call_ui", payload={"tool_name": "shell"})
    clock.advance(20)
    svc.finish_attempt(
        attempt.id,
        status="completed",
        cost_usd=1.25,
        metadata={"agent_result": {"tokens": {"input": 1000, "output": 200}}},
    )
    svc.store.update_runtime_fields(job.id, cost_usd=1.25)
    svc.transition(job.id, JobStatus.VERIFYING, reason="verify")
    clock.advance(5)
    svc.require_human(job.id, AttentionReason.VERIFICATION_REQUIRED, "Need validation")
    clock.advance(10)
    svc.resume(job.id, detail="pytest")
    clock.advance(2)
    svc.cancel(job.id, reason="test complete")

    analysis = build_job_analysis(svc, job.id)
    summary = analysis["summary"]

    assert summary["elapsed_seconds"] == 52
    assert summary["attempts"] == 1
    assert summary["cost_usd"] == 1.25
    assert summary["tool_calls"] == 3
    assert summary["unique_tools"] == 2
    assert summary["human_gates"] == 1
    assert summary["tokens"]["input"] == 1000
    assert analysis["tools"][0] == {"name": "read_file", "count": 2, "share": 2 / 3}
    assert any(row["status"] == "running" and row["seconds"] == 20 for row in analysis["phase_breakdown"])
    assert analysis["attempts"][0]["tool_calls"] == 3
    assert any(event["category"] == "human" for event in analysis["timeline"])

    other = svc.create(JobSpec(title="Reference", repository="/repo"))
    clock.advance(1)
    svc.cancel(other.id, reason="done")
    comparison = compare_job_analyses(analysis, build_job_analysis(svc, other.id))
    cost_row = next(row for row in comparison["metrics"] if row["key"] == "cost_usd")
    assert cost_row["primary"] == 1.25
    assert cost_row["comparison"] == 0.0
    assert cost_row["delta"] == 1.25
