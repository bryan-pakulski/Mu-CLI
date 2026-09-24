"""Milestone 5: base-drift detection -> CONFLICTED, and conflict resolution loop."""

from __future__ import annotations

import subprocess

import pytest

from mu.jobs import AttentionReason, JobService, JobSpec, JobStatus, JobStore
from mu.jobs.base_drift import BaseDriftError, apply_base_drift, assess_base_drift
from mu.jobs.controller import JobController
from mu.jobs.review import JobReviewService
from mu.jobs.review_branch import materialize_review_branch
from mu.jobs.worktree import JobWorktreeManager


def git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args], text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=True,
    ).stdout.strip()


def _ready_job(tmp_path, *, edit="app.txt", content="job change\n"):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "app.txt").write_text("base\n", encoding="utf-8")
    (repo / "other.txt").write_text("other\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")

    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    job = service.create(JobSpec(title="Drift", repository=str(repo), validation_commands=["true"]))
    manager = JobWorktreeManager(service, root=str(tmp_path / "worktrees"))
    manager.prepare(job)
    running = service.get(job.id)
    (tmp_path / "worktrees" / job.id / edit).write_text(content, encoding="utf-8")
    manager.checkpoint(running, label="implementation")
    service.transition(job.id, JobStatus.PREPARING)
    service.transition(job.id, JobStatus.RUNNING)
    service.transition(job.id, JobStatus.VERIFYING)
    materialize_review_branch(service, job.id)
    service.transition(job.id, JobStatus.READY_FOR_REVIEW)
    return repo, service, service.get(job.id)


def _advance_main(repo, path, content, message="main moves"):
    (repo / path).write_text(content, encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", message)


def test_no_drift_when_base_unchanged(tmp_path):
    repo, service, job = _ready_job(tmp_path)
    report = assess_base_drift(service, job.id)
    assert report.drifted is False and report.mergeable is True
    assert report.commits_behind == 0 and report.conflicted_files == []
    assert report.current_base_sha == job.base_sha


def test_drift_without_conflict_keeps_ready(tmp_path):
    repo, service, job = _ready_job(tmp_path)
    _advance_main(repo, "other.txt", "changed elsewhere\n")
    report = apply_base_drift(service, job.id)
    assert report.drifted is True and report.mergeable is True and report.commits_behind == 1
    current = service.get(job.id)
    assert current.status == JobStatus.READY_FOR_REVIEW
    assert current.metadata["base_drift"]["mergeable"] is True
    assert any(e.event_type == "base_drift_assessed" for e in service.events(job.id))


def test_conflicting_drift_moves_ready_job_to_conflicted_with_reason(tmp_path):
    repo, service, job = _ready_job(tmp_path)
    _advance_main(repo, "app.txt", "conflicting main edit\n")
    report = apply_base_drift(service, job.id, source="controller")
    assert report.mergeable is False
    assert report.conflicted_files == ["app.txt"]
    current = service.get(job.id)
    assert current.status == JobStatus.CONFLICTED
    assert current.attention_reason == AttentionReason.MERGE_CONFLICT
    assert "1 commit(s)" in current.attention_detail
    assert current.needs_attention
    # Shows up in the attention summary (pushed to surfaces).
    assert service.attention_summary()["count"] == 1
    # Receipt exposes the drift block.
    from mu.jobs.receipt import JobReceiptBuilder

    receipt = JobReceiptBuilder(service).build(job.id)
    assert receipt["base_drift"]["conflicted_files"] == ["app.txt"]


def test_conflict_resolution_requeues_with_rebase_instruction(tmp_path):
    repo, service, job = _ready_job(tmp_path)
    _advance_main(repo, "app.txt", "conflicting main edit\n")
    apply_base_drift(service, job.id)
    queued = JobReviewService(service).continue_job(job.id)
    assert queued.status == JobStatus.QUEUED
    assert queued.attention_reason == AttentionReason.NONE
    responses = [e for e in service.events(job.id) if e.event_type == "human_response"]
    assert responses and "app.txt" in responses[-1].payload["detail"]
    assert "rebase" in responses[-1].payload["detail"].lower()


def test_controller_drift_watch_flips_ready_to_conflicted(tmp_path, monkeypatch):
    repo, service, job = _ready_job(tmp_path)
    events = []
    ctl = JobController(
        service, max_workers=1, process_factory=lambda *a, **k: None,
        project_root=str(tmp_path), python_executable="python-test",
        notify=events.append, drift_check_interval_seconds=60,
    )
    mono = [1000.0]
    monkeypatch.setattr("mu.jobs.controller.time.monotonic", lambda: mono[0])
    ctl.tick()
    assert service.get(job.id).status == JobStatus.READY_FOR_REVIEW
    _advance_main(repo, "app.txt", "conflict\n")
    ctl.tick()  # interval not elapsed
    assert service.get(job.id).status == JobStatus.READY_FOR_REVIEW
    mono[0] += 61
    ctl.tick()
    assert service.get(job.id).status == JobStatus.CONFLICTED
    # Attention push fired for the flip.
    assert events and events[-1]["count"] == 1 and events[-1]["entered"][0]["id"] == job.id


def test_drift_requires_branch_and_base(tmp_path):
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    job = service.create(JobSpec(title="no branch", repository=str(tmp_path)))
    with pytest.raises(BaseDriftError):
        assess_base_drift(service, job.id)


def test_missing_base_ref_reports_unknown_mergeability(tmp_path):
    repo, service, job = _ready_job(tmp_path)
    git(repo, "checkout", "-q", "-b", "elsewhere")
    git(repo, "branch", "-D", "main")
    report = apply_base_drift(service, job.id)
    assert report.drifted is True and report.mergeable is None
    # Unknown never flips READY -> CONFLICTED.
    assert service.get(job.id).status == JobStatus.READY_FOR_REVIEW
