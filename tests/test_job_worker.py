from __future__ import annotations

import subprocess

from mu.jobs import AttentionReason, JobService, JobSpec, JobStatus, JobStore
from mu.jobs.runner import JobRunOutcome
from mu.jobs.worker import _apply_outcome
from mu.jobs.worktree import JobWorktreeManager


def git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout.strip()


def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.com")
    (path / "code.txt").write_text("base\n", encoding="utf-8")
    git(path, "add", ".")
    git(path, "commit", "-m", "base")
    return path


def running_job(tmp_path):
    repository = repo(tmp_path)
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    job = service.create(JobSpec(
        title="Worker outcome",
        repository=str(repository),
        execution={"provider": "openai", "model": "test", "session_type": "workspace"},
    ))
    manager = JobWorktreeManager(service, root=str(tmp_path / "worktrees"))
    manager.prepare(job)
    service.transition(job.id, JobStatus.PREPARING)
    attempt = service.start_attempt(job.id, worker_id="worker", session_name="job-session")
    service.transition(job.id, JobStatus.RUNNING)
    return service, manager, service.get(job.id), attempt, repository


def test_completed_worker_checkpoints_then_stops_at_verifying(tmp_path):
    service, manager, job, attempt, primary = running_job(tmp_path)
    (tmp_path / "worktrees" / job.id / "code.txt").write_text("implemented\n", encoding="utf-8")

    code = _apply_outcome(
        service,
        manager,
        job.id,
        attempt.id,
        attempt.number,
        JobRunOutcome(kind="completed", status="completed", cost_usd=1.2),
    )

    current = service.get(job.id)
    assert code == 0
    assert current.status == JobStatus.VERIFYING
    assert current.cost_usd == 1.2
    assert (primary / "code.txt").read_text(encoding="utf-8") == "base\n"
    assert git(current.worktree, "status", "--porcelain") == ""
    assert git(current.worktree, "rev-parse", "HEAD") != current.base_sha
    attempt_record = service.attempts(job.id)[0]
    assert attempt_record.status == "completed"
    assert attempt_record.metadata["checkpoint"] == git(current.worktree, "rev-parse", "HEAD")
    assert not any(event.to_status == JobStatus.READY_FOR_REVIEW for event in service.events(job.id))


def test_human_gate_checkpoints_progress_and_persists_attention(tmp_path):
    service, manager, job, attempt, primary = running_job(tmp_path)
    (tmp_path / "worktrees" / job.id / "code.txt").write_text("partial\n", encoding="utf-8")

    code = _apply_outcome(
        service,
        manager,
        job.id,
        attempt.id,
        attempt.number,
        JobRunOutcome(
            kind="needs_human",
            status="needs_human",
            cost_usd=0.4,
            attention_reason=AttentionReason.APPROVAL_REQUIRED,
            attention_detail="Approve dependency update",
            attention_payload={"tool_name": "write_file"},
        ),
    )

    current = service.get(job.id)
    assert code == 20
    assert current.status == JobStatus.NEEDS_HUMAN
    assert current.attention_reason == AttentionReason.APPROVAL_REQUIRED
    assert current.cost_usd == 0.4
    assert (primary / "code.txt").read_text(encoding="utf-8") == "base\n"
    checkpoint = service.attempts(job.id)[0].metadata["checkpoint"]
    assert checkpoint == git(current.worktree, "rev-parse", "HEAD")
    status_event = [e for e in service.events(job.id) if e.to_status == JobStatus.NEEDS_HUMAN][-1]
    assert status_event.payload["checkpoint"] == checkpoint


def test_failed_worker_preserves_debuggable_checkpoint(tmp_path):
    service, manager, job, attempt, _primary = running_job(tmp_path)
    (tmp_path / "worktrees" / job.id / "debug.txt").write_text("repro state\n", encoding="utf-8")

    code = _apply_outcome(
        service,
        manager,
        job.id,
        attempt.id,
        attempt.number,
        JobRunOutcome(kind="failed", status="error", error="provider failed", cost_usd=0.2),
    )

    current = service.get(job.id)
    assert code == 1
    assert current.status == JobStatus.FAILED
    checkpoint = service.attempts(job.id)[0].metadata["checkpoint"]
    assert checkpoint == git(current.worktree, "rev-parse", "HEAD")
    assert (tmp_path / "worktrees" / job.id / "debug.txt").exists()
