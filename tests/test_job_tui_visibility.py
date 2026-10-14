"""Terminal job dashboard: live diff, environment approval box, cost/time/PR header (W10)."""

from __future__ import annotations

import subprocess
from typing import List

import pytest

from mu.jobs import JobService, JobStore
from mu.jobs.models import AttentionReason, JobStatus
from mu.ui.jobs import content, views
from mu.ui.jobs.app import JobsApp
from mu.ui.jobs.data import JobsData
from mu.ui.jobs.primitives import to_plain

EXE = {"provider": "ollama", "model": "small", "session_type": "workspace"}


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))


def _git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True).stdout.strip()


class Recording(JobsData):
    def __init__(self, service):
        super().__init__(service)
        self.sent: List[tuple] = []
        self.fetches: List[tuple] = []

    def job(self, job_id, parts=None):
        self.fetches.append((job_id, tuple(parts or ())))
        return super().job(job_id, parts)

    def dispatch(self, job_id, kind, args=None):
        self.sent.append((job_id, kind, dict(args or {})))
        return {"ok": True}


def _service(tmp_path):
    return JobService(JobStore(str(tmp_path / "jobs.sqlite3")))


def _job(service, tmp_path, **extra):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return service.create_from_payload({"title": "Ticket", "workspace_root": str(ws), "execution": dict(EXE), **extra})


def _text(app, w=120, h=40):
    return to_plain(views.render(app, w, h))


def _implementing_with_worktree(service, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "T")
    _git(repo, "config", "user.email", "t@example.com")
    (repo / "a.txt").write_text("a\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    job = _job(service, tmp_path, max_cost_usd=20.0)
    service.store.update_runtime_fields(job.id, worktree=str(repo), base_sha=_git(repo, "rev-parse", "HEAD"),
                                        cost_usd=5.0)
    service.transition(job.id, JobStatus.PREPARING)
    service.transition(job.id, JobStatus.RUNNING)
    (repo / "a.txt").write_text("a\nlive edit\n")
    (repo / "new.txt").write_text("brand new\n")
    return job, repo


def test_diff_tab_shows_live_worktree_diff_with_marker(tmp_path):
    service = _service(tmp_path)
    job, _ = _implementing_with_worktree(service, tmp_path)
    app = JobsApp(Recording(service))
    app.refresh()
    app.open_job(job.id)
    app.handle_key("3")
    text = _text(app)
    assert "● live · 2 files · 1 untracked" in text
    assert "a.txt" in text and "new.txt" in text
    # viewed marks are for the checkpointed diff only: no dispatch while live.
    app.handle_key("v")
    assert app.data.sent == []


def test_live_diff_refreshes_on_interval_only_on_diff_tab(tmp_path):
    service = _service(tmp_path)
    job, repo = _implementing_with_worktree(service, tmp_path)
    data = Recording(service)
    app = JobsApp(data)
    app.refresh()
    app.open_job(job.id)
    app.job_state(job.id).tab = "session"
    assert app.maybe_refresh_live_diff(100.0) is False
    app.job_state(job.id).tab = "diff"
    data.fetches.clear()
    assert app.maybe_refresh_live_diff(100.0) is True
    assert data.fetches == [(job.id, ("diff",))]
    assert app.maybe_refresh_live_diff(102.0) is False  # within the interval
    (repo / "third.txt").write_text("x\n")
    import mu.jobs.core.artifacts as artifacts
    artifacts._LIVE_CACHE.clear()
    assert app.maybe_refresh_live_diff(106.0) is True
    assert "third.txt" in (app.jobs[job.id]["diff"]["live"]["files"])


def test_finished_job_diff_uses_checkpoint_patch():
    vm = {"diff": {"patch": "diff --git a/x b/x\n", "live": None}}
    assert content.job_patch(vm) == ("diff --git a/x b/x\n", None)
    live = {"patch": "diff --git a/y b/y\n", "files": ["y"], "untracked": [], "truncated": True}
    assert content.job_patch({"diff": {"patch": "p", "live": live}})[1] is live
    assert content.live_diff_label(live) == "● live · 1 files · truncated"


def test_header_shows_cost_vs_cap_active_time_and_pr(tmp_path):
    service = _service(tmp_path)
    job, _ = _implementing_with_worktree(service, tmp_path)
    app = JobsApp(Recording(service))
    app.refresh()
    app.open_job(job.id)
    text = _text(app)
    assert "$5.00 / $20.00 cap" in text and "active " in text
    assert content.budget_line({"budget": {"cost_usd": 1, "max_cost_usd": None, "active_seconds": 125}}) == \
        "$1.00 · no cap · active 2m"
    assert content.budget_line({"pr": {"state": "closed", "number": 7}}) == "PR closed #7"


def _env_blocked(service, tmp_path):
    job = _job(service, tmp_path)
    service.transition(job.id, JobStatus.PREPARING)
    service.transition(job.id, JobStatus.RUNNING)
    approval = {"repository": str(tmp_path / "ws"), "fingerprint": "image:node:20", "kind": "image",
                "ref": "node:20", "label": "image node:20 (.mucli/jobs.toml)", "origin": "repo-image"}
    service.store.append_event(job.id, "build_environment_approval_required", reason="Approve it please",
                               payload=approval)
    service.require_human(job.id, AttentionReason.ENVIRONMENT_FAILURE, "Approve it please")
    return service.get(job.id)


def test_environment_approval_box_and_keys(tmp_path):
    service = _service(tmp_path)
    job = _env_blocked(service, tmp_path)
    app = JobsApp(Recording(service))
    app.refresh()
    app.open_job(job.id)
    text = _text(app, 120, 44)
    assert "Build environment needs approval" in text
    assert "kind image · ref node:20" in text and "fingerprint image:node:20" in text
    app.handle_key("A")
    assert app.mode and app.mode["kind"] == "confirm"  # approve shows its confirm text
    app.mode["run"]()
    assert app.data.sent[-1] == (job.id, "approveEnvironment", {"fingerprint": "image:node:20"})
    app.mode = None
    app.handle_key("X")
    assert app.mode["kind"] == "prompt" and app.mode["optional"]
    for ch in "mine":
        app.handle_key(ch)
    app.handle_key("enter")
    assert app.data.sent[-1] == (job.id, "rejectEnvironment", {"fingerprint": "image:node:20", "reason": "mine"})


def test_environment_actions_in_palette(tmp_path):
    service = _service(tmp_path)
    job = _env_blocked(service, tmp_path)
    app = JobsApp(Recording(service))
    app.refresh()
    app.open_job(job.id)
    labels = {i["label"]: i for i in app.palette_items()}
    assert labels["Approve environment"]["enabled"] and labels["Reject environment"]["enabled"]
