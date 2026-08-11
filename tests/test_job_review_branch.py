from __future__ import annotations

import os
import subprocess

from mu.jobs import JobService, JobSpec, JobStatus, JobStore
from mu.jobs.review import JobReviewService, build_job_diff
from mu.jobs.review_branch import materialize_review_branch
from mu.jobs.worktree import JobWorktreeManager


def git(path, *args):
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return result.stdout.strip()


def test_review_changes_recreate_temporary_worktree_on_same_branch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "app.txt").write_text("base\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")

    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    job = service.create(JobSpec(title="Branch review", repository=str(repo)))
    manager = JobWorktreeManager(service, root=str(tmp_path / "worktrees"))
    manager.prepare(job)
    running = service.get(job.id)
    branch = running.branch
    worktree = running.worktree

    (tmp_path / "worktrees" / job.id / "app.txt").write_text("first implementation\n", encoding="utf-8")
    first_head = manager.checkpoint(running, label="implementation")
    assert first_head

    service.transition(job.id, JobStatus.PREPARING)
    service.transition(job.id, JobStatus.RUNNING)
    service.transition(job.id, JobStatus.VERIFYING)
    materialized = materialize_review_branch(service, job.id)
    ready = service.transition(job.id, JobStatus.READY_FOR_REVIEW)

    assert materialized.branch == branch
    assert materialized.head_sha == first_head
    assert ready.worktree == ""
    assert not os.path.exists(worktree)
    assert git(repo, "rev-parse", f"refs/heads/{branch}^{{commit}}") == first_head
    assert worktree not in git(repo, "worktree", "list", "--porcelain")
    assert "first implementation" in build_job_diff(service, job.id).patch

    queued = JobReviewService(service).request_changes(job.id, "Change wording")
    assert queued.status == JobStatus.QUEUED
    assert queued.branch == branch
    assert queued.worktree == ""

    recreated = manager.prepare(queued)
    resumed = service.get(job.id)
    assert recreated.branch == branch
    assert resumed.worktree == worktree
    assert os.path.isdir(worktree)
    assert git(worktree, "branch", "--show-current") == branch
    assert git(worktree, "rev-parse", "HEAD") == first_head
    assert (tmp_path / "worktrees" / job.id / "app.txt").read_text(encoding="utf-8") == "first implementation\n"
