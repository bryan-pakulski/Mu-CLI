from __future__ import annotations

import subprocess

from mu.jobs import JobService, JobSpec, JobStore
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


def make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Test User")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "app.txt").write_text("base\n", encoding="utf-8")
    git(repo, "add", "app.txt")
    git(repo, "commit", "-m", "base")
    return repo


def make_service(tmp_path):
    return JobService(JobStore(str(tmp_path / "jobs.sqlite3")))


def test_prepare_creates_branch_and_worktree_without_touching_primary_checkout(tmp_path):
    repo = make_repo(tmp_path)
    service = make_service(tmp_path)
    job = service.create(JobSpec(title="Change greeting", repository=str(repo)))
    manager = JobWorktreeManager(service, root=str(tmp_path / "worktrees"))

    info = manager.prepare(job)
    current = service.get(job.id)

    assert info.repository == str(repo)
    assert current.worktree == info.worktree
    assert current.branch.startswith(f"mu/job-{job.id[:10]}-")
    assert current.base_sha == git(repo, "rev-parse", "main^{commit}")
    assert git(current.worktree, "rev-parse", "--show-toplevel") == current.worktree
    assert git(current.worktree, "branch", "--show-current") == current.branch
    assert (repo / "app.txt").read_text(encoding="utf-8") == "base\n"


def test_checkpoint_commits_job_changes_only_on_managed_branch(tmp_path):
    repo = make_repo(tmp_path)
    service = make_service(tmp_path)
    job = service.create(JobSpec(title="Checkpoint me", repository=str(repo)))
    manager = JobWorktreeManager(service, root=str(tmp_path / "worktrees"))
    manager.prepare(job)
    current = service.get(job.id)

    (tmp_path / "worktrees" / job.id / "app.txt").write_text("job change\n", encoding="utf-8")
    sha = manager.checkpoint(current, label="attempt-1-implementation")

    assert sha
    assert git(current.worktree, "rev-parse", "HEAD") == sha
    assert git(current.worktree, "status", "--porcelain") == ""
    assert (repo / "app.txt").read_text(encoding="utf-8") == "base\n"
    assert git(repo, "rev-parse", "main^{commit}") == current.base_sha
    events = service.events(job.id)
    assert any(event.event_type == "checkpoint_created" and event.payload["sha"] == sha for event in events)


def test_prepare_is_idempotent_for_resumed_job(tmp_path):
    repo = make_repo(tmp_path)
    service = make_service(tmp_path)
    job = service.create(JobSpec(title="Resume", repository=str(repo)))
    manager = JobWorktreeManager(service, root=str(tmp_path / "worktrees"))

    first = manager.prepare(job)
    (tmp_path / "worktrees" / job.id / "resume.txt").write_text("saved\n", encoding="utf-8")
    checkpoint = manager.checkpoint(service.get(job.id), label="blocked")
    second = manager.prepare(service.get(job.id))

    assert second == first
    assert checkpoint == git(second.worktree, "rev-parse", "HEAD")
    assert (tmp_path / "worktrees" / job.id / "resume.txt").read_text(encoding="utf-8") == "saved\n"


def test_two_jobs_get_separate_worktrees_and_branches(tmp_path):
    repo = make_repo(tmp_path)
    service = make_service(tmp_path)
    manager = JobWorktreeManager(service, root=str(tmp_path / "worktrees"))
    a = service.create(JobSpec(title="Ticket A", repository=str(repo)))
    b = service.create(JobSpec(title="Ticket B", repository=str(repo)))

    wa = manager.prepare(a)
    wb = manager.prepare(b)
    assert wa.worktree != wb.worktree
    assert wa.branch != wb.branch
    assert wa.base_sha == wb.base_sha

    (tmp_path / "worktrees" / a.id / "app.txt").write_text("A\n", encoding="utf-8")
    (tmp_path / "worktrees" / b.id / "app.txt").write_text("B\n", encoding="utf-8")
    sha_a = manager.checkpoint(service.get(a.id), label="A")
    sha_b = manager.checkpoint(service.get(b.id), label="B")

    assert sha_a != sha_b
    assert (repo / "app.txt").read_text(encoding="utf-8") == "base\n"
    assert (tmp_path / "worktrees" / a.id / "app.txt").read_text(encoding="utf-8") == "A\n"
    assert (tmp_path / "worktrees" / b.id / "app.txt").read_text(encoding="utf-8") == "B\n"
