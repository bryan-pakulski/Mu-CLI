from __future__ import annotations

import os
import shlex
import subprocess
import sys

from mu.jobs import AttentionReason, JobService, JobSpec, JobStatus, JobStore
from mu.jobs.verification import DeterministicVerifier, VerificationStore
from mu.jobs.verify_worker import apply_verification_result
from mu.jobs.worktree import JobWorktreeManager


def git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout.strip()


def make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "code.txt").write_text("base\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    return repo


def python_command(code: str) -> str:
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"


def verifying_job(tmp_path, *, commands, max_retries=2, attempts=1):
    repo = make_repo(tmp_path)
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    job = service.create(JobSpec(
        title="Verify me",
        repository=str(repo),
        validation_commands=list(commands),
        max_retries=max_retries,
        execution={"provider": "openai", "model": "test", "session_type": "workspace"},
    ))
    manager = JobWorktreeManager(service, root=str(tmp_path / "worktrees"))
    manager.prepare(job)
    current = service.get(job.id)
    (tmp_path / "worktrees" / job.id / "code.txt").write_text("implemented\n", encoding="utf-8")
    manager.checkpoint(current, label="implementation")
    service.transition(job.id, JobStatus.PREPARING)
    service.transition(job.id, JobStatus.RUNNING)
    for number in range(attempts):
        attempt = service.start_attempt(job.id, worker_id=f"worker-{number}", session_name="job-session")
        service.finish_attempt(attempt.id, status="completed")
    service.transition(job.id, JobStatus.VERIFYING)
    return service, service.get(job.id), repo


def test_passing_commands_create_evidence_and_unlock_ready_for_review(tmp_path):
    command = python_command(
        "from pathlib import Path; assert Path('code.txt').read_text() == 'implemented\\n'"
    )
    service, job, primary = verifying_job(tmp_path, commands=[command])
    store = VerificationStore(service.store, evidence_root=str(tmp_path / "evidence"))
    run = DeterministicVerifier(service, store=store).verify(job)

    assert run.passed is True
    assert run.status == "passed"
    assert run.checks[0].passed is True
    assert run.head_sha == git(job.worktree, "rev-parse", "HEAD")
    assert run.base_sha == job.base_sha
    assert run.changed_files[0]["path"] == "code.txt"
    assert os.path.exists(run.manifest_path)
    assert (primary / "code.txt").read_text(encoding="utf-8") == "base\n"
    assert store.latest(job.id).id == run.id

    assert apply_verification_result(service, job.id, run) == 0
    ready = service.get(job.id)
    assert ready.status == JobStatus.READY_FOR_REVIEW
    event = [e for e in service.events(job.id) if e.to_status == JobStatus.READY_FOR_REVIEW][-1]
    assert event.payload["verification_id"] == run.id


def test_failed_verification_requeues_agent_while_retry_budget_remains(tmp_path):
    service, job, _ = verifying_job(
        tmp_path,
        commands=[python_command("raise SystemExit(3)")],
        max_retries=2,
        attempts=1,
    )
    run = DeterministicVerifier(
        service,
        store=VerificationStore(service.store, evidence_root=str(tmp_path / "evidence")),
    ).verify(job)
    assert run.passed is False
    assert run.status == "failed"

    assert apply_verification_result(service, job.id, run) == 10
    current = service.get(job.id)
    assert current.status == JobStatus.QUEUED
    feedback = [e for e in service.events(job.id) if e.event_type == "verification_failed"][-1]
    assert feedback.payload["verification_id"] == run.id
    assert feedback.payload["failed_checks"][0]["return_code"] == 3


def test_failed_verification_exhausts_retries_into_human_gate(tmp_path):
    service, job, _ = verifying_job(
        tmp_path,
        commands=[python_command("raise SystemExit(2)")],
        max_retries=2,
        attempts=3,
    )
    run = DeterministicVerifier(
        service,
        store=VerificationStore(service.store, evidence_root=str(tmp_path / "evidence")),
    ).verify(job)

    assert apply_verification_result(service, job.id, run) == 22
    current = service.get(job.id)
    assert current.status == JobStatus.NEEDS_HUMAN
    assert current.attention_reason == AttentionReason.TEST_FAILURE
    assert current.attention_detail


def test_missing_verification_contract_never_becomes_ready(tmp_path):
    service, job, _ = verifying_job(tmp_path, commands=[])
    run = DeterministicVerifier(
        service,
        store=VerificationStore(service.store, evidence_root=str(tmp_path / "evidence")),
    ).verify(job)
    assert run.status == "missing_contract"
    assert run.passed is False

    assert apply_verification_result(service, job.id, run) == 21
    current = service.get(job.id)
    assert current.status == JobStatus.NEEDS_HUMAN
    assert current.attention_reason == AttentionReason.VERIFICATION_REQUIRED


def test_validation_that_leaves_tracked_changes_is_not_merge_ready(tmp_path):
    service, job, _ = verifying_job(
        tmp_path,
        commands=[python_command("from pathlib import Path; Path('code.txt').write_text('mutated by test\\n')")],
        max_retries=2,
    )
    run = DeterministicVerifier(
        service,
        store=VerificationStore(service.store, evidence_root=str(tmp_path / "evidence")),
    ).verify(job)
    assert run.checks[0].passed is True
    assert run.dirty is True
    assert run.status == "dirty_worktree"
    assert run.passed is False
    assert apply_verification_result(service, job.id, run) == 10
    assert service.get(job.id).status == JobStatus.QUEUED
