from __future__ import annotations

import json
import subprocess

from mu.jobs import JobService, JobSpec, JobStatus, JobStore
from mu.jobs.receipt import JobReceiptBuilder
from mu.jobs.verification import VerificationRun, VerificationStore, changed_files
from mu.jobs.worktree import JobWorktreeManager


def git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout.strip()


def make_ready_job(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "feature.txt").write_text("base\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")

    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    job = service.create(JobSpec(
        title="Receipt feature",
        description="Implement the receipt feature.",
        repository=str(repo),
        acceptance_criteria=["feature output is correct"],
        validation_commands=["test -f feature.txt"],
        max_cost_usd=5,
        execution={"provider": "openai", "model": "test", "session_type": "workspace"},
    ))
    manager = JobWorktreeManager(service, root=str(tmp_path / "worktrees"))
    manager.prepare(job)
    current = service.get(job.id)
    execution_worktree = current.worktree
    (tmp_path / "worktrees" / job.id / "feature.txt").write_text("implemented\n", encoding="utf-8")
    checkpoint = manager.checkpoint(current, label="implementation")
    service.transition(job.id, JobStatus.PREPARING)
    service.transition(job.id, JobStatus.RUNNING)
    attempt = service.start_attempt(job.id, worker_id="worker", session_name="job-session")
    service.finish_attempt(
        attempt.id,
        status="completed",
        cost_usd=1.25,
        metadata={
            "checkpoint": checkpoint,
            "agent_result": {
                "status": "completed",
                "tokens": {"input": 1000, "output": 200, "total": 1200},
            },
        },
    )
    service.store.update_runtime_fields(job.id, cost_usd=1.25)
    service.store.append_event(job.id, "tool_call_ui", payload={"tool_name": "write_file"})
    service.store.append_event(job.id, "agent_message", payload={"role": "assistant", "text": "done"})
    service.transition(job.id, JobStatus.VERIFYING)
    # Persist a passing check run the way the checks stage does, then follow
    # the live path to a pushed job: review -> human push -> publish.
    verifying = service.get(job.id)
    changed, additions, deletions = changed_files(execution_worktree, verifying.base_sha, checkpoint)
    run = VerificationStore(service.store, evidence_root=str(tmp_path / "evidence")).save(VerificationRun(
        id="run-1", job_id=job.id, status="passed", passed=True, started_at=1.0, finished_at=2.0,
        duration_ms=1000, base_sha=verifying.base_sha, head_sha=checkpoint, branch=verifying.branch,
        worktree=execution_worktree, changed_files=changed, additions=additions, deletions=deletions,
    ))
    for status in (JobStatus.REVIEWING, JobStatus.PUBLISHING, JobStatus.READY_FOR_REVIEW):
        service.transition(job.id, status)
    return service, service.get(job.id), run, execution_worktree


def test_receipt_answers_outcome_cost_git_verification_and_activity(tmp_path):
    service, job, verification, execution_worktree = make_ready_job(tmp_path)
    builder = JobReceiptBuilder(service, root=str(tmp_path / "evidence"))
    receipt = builder.build(job.id)

    assert receipt["schema_version"] == 3
    assert receipt["job"]["status"] == "ready_for_review"
    assert receipt["outcome"]["ready_for_review"] is True
    assert receipt["outcome"]["review_artifact"] == "branch"
    assert receipt["outcome"]["attempts"] == 1
    assert receipt["outcome"]["cost_usd"] == 1.25
    # Historical attempts without a pricing record retain their number but are
    # explicitly labelled legacy/incomplete instead of pretending the rate
    # provenance is known.
    assert receipt["outcome"]["cost_status"] == "legacy"
    assert receipt["outcome"]["cost_complete"] is False
    assert receipt["usage"]["model_api"]["api_cost_usd"] == 1.25
    assert receipt["git"]["branch"] == job.branch
    assert receipt["git"]["review_artifact"] == "branch"
    assert receipt["git"]["review_branch"] == job.branch
    assert receipt["git"]["review_head_sha"] == verification.head_sha
    assert receipt["git"]["worktree"] == execution_worktree
    assert receipt["git"]["execution_worktree"] == execution_worktree
    assert receipt["git"]["retired_worktree"] == ""
    assert receipt["git"]["base_sha"] == job.base_sha
    assert receipt["git"]["head_sha"] == verification.head_sha
    assert receipt["git"]["changed_files"][0]["path"] == "feature.txt"
    assert receipt["verification"]["id"] == verification.id
    assert receipt["verification"]["passed"] is True
    assert receipt["usage"]["tokens"]["input"] == 1000
    assert receipt["usage"]["tokens"]["output"] == 200
    assert receipt["activity"]["tool_calls"] == 1
    assert receipt["activity"]["agent_messages"] == 1
    assert receipt["ticket"]["acceptance_criteria"] == ["feature output is correct"]


def test_receipt_can_be_written_as_stable_job_evidence(tmp_path):
    service, job, verification, execution_worktree = make_ready_job(tmp_path)
    builder = JobReceiptBuilder(service, root=str(tmp_path / "evidence"))
    path = builder.write(job.id)

    with open(path, "r", encoding="utf-8") as fh:
        saved = json.load(fh)
    assert saved["job"]["id"] == job.id
    assert saved["verification"]["id"] == verification.id
    assert saved["outcome"]["ready_for_review"] is True
    assert saved["outcome"]["review_artifact"] == "branch"
    assert saved["git"]["review_branch"] == job.branch
    assert saved["git"]["worktree"] == execution_worktree
    assert any(event.event_type == "work_receipt_updated" for event in service.events(job.id))
