"""Shared helpers for the jobs tests (imported as a plain module from tests/helpers/)."""

from __future__ import annotations

import subprocess

from mu.jobs import JobService, JobSpec, JobStatus, JobStore
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
    # Outcomes are lease-conditioned: the test worker holds the job's lease.
    assert service.acquire(job.id, "worker", ttl_seconds=600)
    return service, manager, service.get(job.id), attempt, repository
