from __future__ import annotations

import subprocess

from mu.jobs import JobService, JobSpec, JobStore
from mu.jobs.repository import RepositoryRegistry
from mu.jobs.worktree import JobWorktreeManager


def git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout.strip()


def make_repo(tmp_path, *, branch="main"):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", branch)
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    return repo


def test_primary_and_existing_worktree_share_one_repository_identity(tmp_path):
    repo = make_repo(tmp_path)
    secondary = tmp_path / "secondary"
    git(repo, "worktree", "add", "-b", "secondary", str(secondary), "main")
    store = JobStore(str(tmp_path / "jobs.sqlite3"))
    registry = RepositoryRegistry(store)

    primary_record = registry.register(str(repo))
    secondary_record = registry.register(str(secondary))

    assert primary_record.id == secondary_record.id
    assert primary_record.canonical_path == str(repo)
    assert secondary_record.canonical_path == str(repo)
    assert len(registry.list()) == 1


def test_registry_detects_current_branch_when_origin_head_is_unavailable(tmp_path):
    repo = make_repo(tmp_path, branch="develop")
    registry = RepositoryRegistry(JobStore(str(tmp_path / "jobs.sqlite3")))

    record = registry.register(str(repo))

    assert record.default_branch == "develop"
    assert record.metadata["current_branch"] == "develop"
    assert record.metadata["remote_default_ref"] == ""
    assert record.metadata["head_sha"] == git(repo, "rev-parse", "HEAD")
    assert record.metadata["clean"] is True


def test_worktree_preparation_persists_repository_and_environment_identity(tmp_path):
    repo = make_repo(tmp_path)
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    job = service.create(JobSpec(title="Identity", repository=str(repo)))
    manager = JobWorktreeManager(service, root=str(tmp_path / "managed"))

    info = manager.prepare(job)
    current = service.get(job.id)

    assert current.metadata["repository_id"] == info.repository_id
    assert current.metadata["repository_root"] == str(repo)
    assert current.metadata["resolved_base_ref"] == "main"
    assert current.environment["kind"] == "host_git_worktree"
    assert current.environment["repository_id"] == info.repository_id
    assert current.environment["worktree"] == current.worktree
    assert current.environment["branch"] == current.branch
    assert current.environment["base_ref"] == "main"
