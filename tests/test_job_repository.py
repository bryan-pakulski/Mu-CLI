from __future__ import annotations

import subprocess

import pytest

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


def test_inspect_keeps_primary_identity_but_reports_submitted_worktree_head(tmp_path):
    repo = make_repo(tmp_path)
    secondary = tmp_path / "secondary"
    git(repo, "worktree", "add", "-b", "feature/test", str(secondary), "main")
    (secondary / "feature.txt").write_text("feature\n", encoding="utf-8")
    git(secondary, "add", "feature.txt")
    git(secondary, "commit", "-m", "feature worktree commit")

    info = RepositoryRegistry.inspect(str(secondary))

    assert info["canonical_path"] == str(repo)
    assert info["submitted_path"] == str(secondary)
    assert info["current_branch"] == "feature/test"
    assert info["head_sha"] == git(secondary, "rev-parse", "HEAD")
    assert info["head_sha"] != git(repo, "rev-parse", "HEAD")
    assert info["clean"] is True


def test_payload_creation_snapshots_current_repository_head_instead_of_inventing_main(tmp_path):
    repo = make_repo(tmp_path, branch="develop")
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))

    job = service.create_from_payload({
        "title": "Queue from GUI",
        "repository": str(repo),
        "execution": {
            "provider": "ollama",
            "model": "test-model",
            "session_type": "workspace",
        },
    })

    assert job.base_branch == "develop"
    assert job.base_sha == git(repo, "rev-parse", "HEAD")
    preflight = job.metadata["submission_repository_preflight"]
    assert preflight["canonical_path"] == str(repo)
    assert preflight["current_branch"] == "develop"
    assert preflight["head_sha"] == job.base_sha
    assert preflight["clean"] is True


def test_payload_creation_from_existing_worktree_snapshots_that_worktree(tmp_path):
    repo = make_repo(tmp_path)
    secondary = tmp_path / "secondary"
    git(repo, "worktree", "add", "-b", "feature/delegated", str(secondary), "main")
    (secondary / "delegated.txt").write_text("delegated\n", encoding="utf-8")
    git(secondary, "add", "delegated.txt")
    git(secondary, "commit", "-m", "delegated base")
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))

    job = service.create_from_payload({
        "title": "Queue from attached worktree",
        "repository": str(secondary),
        "execution": {
            "provider": "ollama",
            "model": "test-model",
            "session_type": "workspace",
        },
    })

    assert job.base_branch == "feature/delegated"
    assert job.base_sha == git(secondary, "rev-parse", "HEAD")
    assert job.base_sha != git(repo, "rev-parse", "HEAD")
    preflight = job.metadata["submission_repository_preflight"]
    assert preflight["canonical_path"] == str(repo)
    assert preflight["current_branch"] == "feature/delegated"


def test_payload_creation_rejects_invalid_repository_before_queueing(tmp_path):
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    missing = tmp_path / "does-not-exist"

    with pytest.raises(ValueError, match="Repository preflight failed"):
        service.create_from_payload({
            "title": "Bad workspace",
            "repository": str(missing),
            "execution": {
                "provider": "ollama",
                "model": "test-model",
                "session_type": "workspace",
            },
        })

    assert service.list() == []


def test_explicit_base_branch_is_preserved_for_worker_resolution(tmp_path):
    repo = make_repo(tmp_path, branch="develop")
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))

    job = service.create_from_payload({
        "title": "Release job",
        "repository": str(repo),
        "base_branch": "release",
        "execution": {
            "provider": "ollama",
            "model": "test-model",
            "session_type": "workspace",
        },
    })

    assert job.base_branch == "release"
    assert job.base_sha == ""
    assert "submission_repository_preflight" not in job.metadata


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
