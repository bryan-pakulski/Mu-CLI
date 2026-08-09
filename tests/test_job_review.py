from __future__ import annotations

import os
import subprocess

import pytest

from mu.jobs import AttentionReason, JobService, JobSpec, JobStatus, JobStore
from mu.jobs.review import JobReviewError, JobReviewService, build_job_diff
from mu.jobs.ui import JobUI


def _git(path, *args, env=None):
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _service(tmp_path):
    return JobService(JobStore(str(tmp_path / "jobs.sqlite3")))


def _ready_job(service):
    job = service.create(JobSpec(title="Review me"))
    service.transition(job.id, JobStatus.PREPARING)
    service.transition(job.id, JobStatus.RUNNING)
    service.transition(job.id, JobStatus.VERIFYING)
    return service.transition(job.id, JobStatus.READY_FOR_REVIEW)


def test_review_feedback_requeues_same_job_and_persists_for_next_attempt(tmp_path):
    service = _service(tmp_path)
    job = _ready_job(service)

    updated = JobReviewService(service).request_changes(job.id, "Keep the old API alias.")

    assert updated.id == job.id
    assert updated.status == JobStatus.QUEUED
    events = service.events(job.id)
    assert any(event.event_type == "review_feedback" for event in events)
    assert any(
        event.event_type == "human_response"
        and event.payload.get("detail") == "Keep the old API alias."
        for event in events
    )


def test_approval_response_is_consumed_exactly_once_by_job_ui(tmp_path):
    service = _service(tmp_path)
    job = service.create(JobSpec(title="Approval"))
    service.transition(job.id, JobStatus.PREPARING)
    service.transition(job.id, JobStatus.RUNNING)
    service.require_human(
        job.id,
        AttentionReason.APPROVAL_REQUIRED,
        "Approval required for write_file",
        payload={"tool_name": "write_file", "can_approve": True},
    )

    review = JobReviewService(service)
    queued = review.respond(job.id, decision="approve", detail="Approved")
    assert queued.status == JobStatus.QUEUED

    ui = JobUI(service, job.id)
    assert ui.request_tool_approval(tool_name="write_file") == ("y", None)
    with pytest.raises(Exception):
        ui.request_tool_approval(tool_name="write_file")

    consumed = [e for e in service.events(job.id) if e.event_type == "interaction_response_consumed"]
    assert len(consumed) == 1


def test_question_response_value_is_consumed(tmp_path):
    service = _service(tmp_path)
    job = service.create(JobSpec(title="Question"))
    service.transition(job.id, JobStatus.PREPARING)
    service.transition(job.id, JobStatus.RUNNING)
    service.require_human(
        job.id,
        AttentionReason.QUESTION,
        "Which mode?",
        payload={"shape": "choices", "choices": ["safe", "fast"]},
    )
    JobReviewService(service).respond(job.id, value="safe")

    ui = JobUI(service, job.id)
    assert ui.prompt_choices("Which mode?", ["safe", "fast"], default="safe") == "safe"


def test_continue_failed_job_retries_and_discard_cancels(tmp_path):
    service = _service(tmp_path)
    job = service.create(JobSpec(title="Failed"))
    service.transition(job.id, JobStatus.PREPARING)
    service.transition(job.id, JobStatus.FAILED)

    queued = JobReviewService(service).continue_job(job.id, "Try the alternate parser")
    assert queued.status == JobStatus.QUEUED
    discarded = JobReviewService(service).discard(job.id, "No longer needed")
    assert discarded.status == JobStatus.CANCELLED


def test_review_diff_is_against_captured_base_and_does_not_touch_main_checkout(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    env = os.environ.copy()
    env.update({
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    })
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-m", "base", env=env)
    base = _git(repo, "rev-parse", "HEAD")

    worktree = tmp_path / "worktree"
    _git(repo, "worktree", "add", "-b", "mu/job-review", str(worktree), base)
    (worktree / "app.py").write_text("value = 2\n", encoding="utf-8")
    _git(worktree, "add", "app.py")
    _git(worktree, "commit", "-m", "change", env=env)

    service = _service(tmp_path)
    job = service.create(JobSpec(title="Diff", repository=str(repo), base_sha=base))
    service.store.update_runtime_fields(job.id, branch="mu/job-review", worktree=str(worktree))
    diff = build_job_diff(service, job.id)

    assert diff.base_sha == base
    assert "app.py" in diff.files
    assert "-value = 1" in diff.patch
    assert "+value = 2" in diff.patch
    assert (repo / "app.py").read_text(encoding="utf-8") == "value = 1\n"


def test_request_changes_requires_ready_state_and_feedback(tmp_path):
    service = _service(tmp_path)
    job = service.create(JobSpec(title="Not ready"))
    review = JobReviewService(service)
    with pytest.raises(JobReviewError):
        review.request_changes(job.id, "change it")

    ready = _ready_job(service)
    with pytest.raises(JobReviewError):
        review.request_changes(ready.id, "")
