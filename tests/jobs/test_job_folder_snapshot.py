"""Jobs on a folder that is not a git repository run on a managed git snapshot of it; the job's
change comes back as a patch that ``git apply`` applies in the folder (mu/jobs/folder_snapshot.py)."""

from __future__ import annotations

import os
import subprocess

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mu.gui.routers import jobs as jobs_router
from mu.jobs import JobService, JobStore, job_settings
from mu.jobs.folder_snapshot import SnapshotError, ensure_snapshot


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("MUCLI_HOME", str(home))
    import utils.config
    monkeypatch.setattr(utils.config, "HISTORY_DIR", str(home))


def _git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True).stdout


def _tracked(snapshot) -> set:
    return set(_git(snapshot.path, "ls-files").split())


def _folder(tmp_path):
    folder = tmp_path / "notes"
    (folder / "src").mkdir(parents=True)
    (folder / "src" / "app.py").write_text("print('hi')\n")
    (folder / "README.md").write_text("# Notes\n")
    (folder / ".env").write_text("TOKEN=secret\n")
    (folder / "tests" / "fixtures").mkdir(parents=True)
    (folder / "tests" / "fixtures" / "server.pem").write_text("fixture key\n")
    (folder / "node_modules" / "left-pad").mkdir(parents=True)
    (folder / "node_modules" / "left-pad" / "index.js").write_text("module.exports = 1\n")
    (folder / "nested" / ".git").mkdir(parents=True)
    (folder / "nested" / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (folder / "nested" / "lib.txt").write_text("nested\n")
    (folder / ".gitignore").write_text("*.log\n")
    (folder / "debug.log").write_text("noise\n")
    os.symlink("src/app.py", folder / "link-inside")
    os.symlink("/etc/hostname", folder / "link-outside")
    return folder


def test_snapshot_keeps_the_code_and_leaves_out_secrets_dependencies_and_escaping_links(tmp_path):
    folder = _folder(tmp_path)
    snap = ensure_snapshot(str(folder))
    assert snap.created and snap.changed
    assert _tracked(snap) == {".gitignore", "README.md", "src/app.py", "nested/lib.txt", "link-inside",
                              "tests/fixtures/server.pem"}
    assert any(s.startswith(".env ") for s in snap.skipped)
    assert any(s.startswith("node_modules/") for s in snap.skipped)
    assert any(s.startswith("link-outside ") for s in snap.skipped)
    assert snap.base_sha == _git(snap.path, "rev-parse", "HEAD").strip()
    # The folder itself is never touched.
    assert not (folder / ".git").exists() and (folder / ".env").exists()


def test_refresh_commits_only_when_the_folder_changed(tmp_path):
    folder = _folder(tmp_path)
    first = ensure_snapshot(str(folder))
    again = ensure_snapshot(str(folder))
    assert not again.created and not again.changed and again.base_sha == first.base_sha
    (folder / "README.md").write_text("# Notes v2\n")
    (folder / "src" / "app.py").unlink()
    changed = ensure_snapshot(str(folder))
    assert changed.changed and changed.base_sha != first.base_sha
    assert "src/app.py" not in _tracked(changed)
    assert _git(changed.path, "show", "HEAD:README.md") == "# Notes v2\n"


def test_oversized_folders_are_refused(tmp_path):
    folder = tmp_path / "big"
    folder.mkdir()
    (folder / "blob.bin").write_bytes(b"x" * (2 * 1024 * 1024))
    job_settings.save_settings({"jobs": {"folder_snapshot_max_mb": 1}})
    with pytest.raises(SnapshotError, match="more than 1 MB"):
        ensure_snapshot(str(folder))


def test_a_folder_job_runs_on_the_snapshot_and_hands_back_a_patch_that_applies(tmp_path):
    from mu.jobs.sandbox.policy import workspace_precheck
    from mu.jobs.worktree import JobWorktreeManager

    folder = _folder(tmp_path)
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    job = service.create_from_payload({"title": "Greet louder", "source": {"kind": "folder", "path": str(folder)},
                                       "execution": {"provider": "ollama", "model": "small"}})
    snap = job.metadata["folder_snapshot"]
    assert job.repository == snap["snapshot_path"] != str(folder)
    assert snap["source_path"] == str(folder.resolve())
    assert job.metadata["source"] == {"kind": "folder", "path": str(folder)}
    assert (job.base_branch, job.base_sha) == ("main", snap["base_sha"])
    assert job.execution["git_enabled"] is True and job.execution["publish_mode"] == "none"

    JobWorktreeManager(service, root=str(tmp_path / "worktrees")).prepare(job)
    job = service.get(job.id)
    # The worktree a stage would mount passes the sandbox's secret/symlink pre-check.
    workspace_precheck(job.worktree)
    app = os.path.join(job.worktree, "src", "app.py")
    with open(app, "w") as handle:
        handle.write("print('HELLO')\n")
    with open(os.path.join(job.worktree, "src", "new.py"), "w") as handle:
        handle.write("x = 1\n")
    _git(job.worktree, "add", "-A")
    _git(job.worktree, "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-qm", "change")

    api = FastAPI()
    api.include_router(jobs_router.router, prefix="/api/jobs")
    api.state.job_service = service
    with TestClient(api) as client:
        response = client.get(f"/api/jobs/{job.id}/patch")
        assert response.status_code == 200, response.text
        assert response.headers["content-disposition"].startswith("attachment")
        patch = tmp_path / "job.patch"
        patch.write_bytes(response.content)
        vm = client.get(f"/api/jobs/{job.id}").json()["job"]
    assert vm["metadata"]["folder_snapshot"]["source_path"] == str(folder.resolve())
    # Applied in the original (non-git) folder, the patch reproduces the job's change.
    subprocess.run(["git", "apply", str(patch)], cwd=folder, check=True, capture_output=True)
    assert (folder / "src" / "app.py").read_text() == "print('HELLO')\n"
    assert (folder / "src" / "new.py").read_text() == "x = 1\n"


def test_folder_jobs_finish_as_a_patch_instead_of_pushing(tmp_path):
    from mu.jobs.core.actions import JobFacts, available_actions
    from mu.jobs.core.states import CoreState, JobState

    facts = JobFacts(core=CoreState(state=JobState.REVIEW_DONE), has_review=True, verdict="ready",
                     push_check_reason="", folder_snapshot=True)
    push = next(a for a in available_actions(facts) if a.id == "push")
    assert push.label == "Finish (keep as patch)" and push.dialog is None and "git apply" in push.confirm
    assert push.enabled and push.primary
    plain = next(a for a in available_actions(JobFacts(core=CoreState(state=JobState.REVIEW_DONE), has_review=True,
                                                       verdict="ready", push_check_reason="")) if a.id == "push")
    assert plain.label == "Push / open PR" and plain.dialog == "push"


def test_mucli_state_inside_the_folder_is_never_copied(tmp_path, monkeypatch):
    # $MUCLI_HOME (holding the snapshot itself) inside the folder: skipped, not copied into itself.
    folder = tmp_path / "project"
    folder.mkdir()
    (folder / "main.py").write_text("x = 1\n")
    state = folder / ".mucli-home"
    (state / "sessions" / "s1").mkdir(parents=True)
    (state / "sessions" / "s1" / "session.json").write_text("{}")
    monkeypatch.setenv("MUCLI_HOME", str(state))
    snap = ensure_snapshot(str(folder))
    assert snap.path.startswith(str(state))
    assert _tracked(snap) == {"main.py"}
    assert any(s.startswith(".mucli-home/ ") for s in snap.skipped)
    with pytest.raises(SnapshotError, match="state directory"):
        ensure_snapshot(str(state / "sessions"))
