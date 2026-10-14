"""W10 visibility: live diff, PR merge polling, environment approvals, planner hint, budget header."""

from __future__ import annotations

import json
import subprocess
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mu.jobs import JobService, JobStore
from mu.jobs.core import vm
from mu.jobs.core.actions import action_map
from mu.jobs.core.commands import CommandRejected, dispatch
from mu.jobs.models import AttentionReason, JobStatus

EXE = {"provider": "ollama", "model": "small", "session_type": "workspace"}


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))


def _git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True).stdout.strip()


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "T")
    _git(repo, "config", "user.email", "t@example.com")
    (repo / "a.txt").write_text("a\n")
    (repo / "b.txt").write_text("b\n")
    (repo / ".gitignore").write_text("ignored.txt\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    return repo, _git(repo, "rev-parse", "HEAD")


def _service(tmp_path):
    return JobService(JobStore(str(tmp_path / "jobs.sqlite3")))


def _job(service, tmp_path, **extra):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return service.create_from_payload({"title": "Ticket", "workspace_root": str(ws), "execution": dict(EXE), **extra})


def _pushed(service, tmp_path, pr):
    from mu.jobs.stages import merge_metadata
    job = _job(service, tmp_path)
    for status in (JobStatus.PREPARING, JobStatus.RUNNING, JobStatus.VERIFYING, JobStatus.REVIEWING,
                   JobStatus.PUBLISHING, JobStatus.READY_FOR_REVIEW):
        service.transition(job.id, status, reason="test")
    merge_metadata(service, job.id, pr=pr)
    return service.get(job.id)


# ------------------------------------------------------------------ live diff
def test_live_diff_shows_modified_deleted_and_untracked_without_touching_the_index(tmp_path):
    from mu.jobs.core.artifacts import live_diff
    repo, base = _repo(tmp_path)
    (repo / "a.txt").write_text("a\nchanged\n")
    (repo / "b.txt").unlink()
    (repo / "c.txt").write_text("new file\n")
    (repo / "ignored.txt").write_text("nope\n")
    _git(repo, "add", "a.txt")  # a staged change in the real index must stay staged
    before = _git(repo, "status", "--porcelain")

    class J:
        id, worktree, base_sha = "j1", str(repo), base

    out = live_diff(J, use_cache=False)
    assert out is not None
    assert set(out["files"]) == {"a.txt", "b.txt", "c.txt"}
    assert out["untracked"] == ["c.txt"]
    assert "+changed" in out["patch"] and "+new file" in out["patch"] and "-b" in out["patch"]
    assert "ignored.txt" not in out["patch"]
    assert out["truncated"] is False
    assert _git(repo, "status", "--porcelain") == before


def test_live_diff_is_bounded_and_scrubbed(tmp_path):
    from mu.jobs.core.artifacts import live_diff
    repo, base = _repo(tmp_path)
    (repo / "big.txt").write_text("line of text\n" * 5000)
    (repo / "secret.txt").write_text("key = AKIAABCDEFGHIJKLMNOP\n")

    class J:
        id, worktree, base_sha = "j2", str(repo), base

    out = live_diff(J, max_bytes=10_000, use_cache=False)
    assert out["truncated"] is True and len(out["patch"]) < 12_000
    full = live_diff(J, use_cache=False)
    assert "AKIAABCDEFGHIJKLMNOP" not in full["patch"]


def test_live_diff_none_without_worktree_or_base(tmp_path):
    from mu.jobs.core.artifacts import live_diff

    class J:
        id, worktree, base_sha = "j3", str(tmp_path / "missing"), "a" * 40

    assert live_diff(J) is None
    J.worktree, J.base_sha = str(tmp_path), ""
    assert live_diff(J) is None


def test_job_vm_diff_part_carries_live_diff_while_active(tmp_path):
    repo, base = _repo(tmp_path)
    service = _service(tmp_path)
    job = _job(service, tmp_path)
    service.store.update_runtime_fields(job.id, worktree=str(repo), base_sha=base)
    service.transition(job.id, JobStatus.PREPARING)
    service.transition(job.id, JobStatus.RUNNING)
    (repo / "a.txt").write_text("a\nlive\n")
    out = vm.job_vm(service, job.id, parts=["diff"])
    assert out["diff"]["live"]["files"] == ["a.txt"]
    assert "+live" in out["diff"]["live"]["patch"]


def test_job_vm_diff_live_absent_when_finished(tmp_path):
    repo, base = _repo(tmp_path)
    service = _service(tmp_path)
    job = _pushed(service, tmp_path, {"url": "https://github.com/o/r/pull/1"})
    service.store.update_runtime_fields(job.id, worktree=str(repo), base_sha=base)
    assert vm.job_vm(service, job.id, parts=["diff"])["diff"]["live"] is None


# ------------------------------------------------------------------ PR merge polling
class FakeGh:
    def __init__(self, state, merged_at=""):
        self.out = json.dumps({"state": state, "mergedAt": merged_at})
        self.calls = []

    def __call__(self, argv, cwd):
        self.calls.append(list(argv))
        if argv[:3] == ["gh", "pr", "view"]:
            return 0, self.out, ""
        return 1, "", "unexpected"


def test_check_pr_state_gh_and_rest_providers(tmp_path):
    from mu.jobs.publish import check_pr_state
    service = _service(tmp_path)
    job = _pushed(service, tmp_path, {"provider": "gh", "url": "https://github.com/o/r/pull/7", "number": 7})
    run = FakeGh("MERGED", "2026-10-01T00:00:00Z")
    assert check_pr_state(job, run=run)["state"] == "merged"
    assert run.calls[0][:5] == ["gh", "pr", "view", "https://github.com/o/r/pull/7", "--json"]
    assert check_pr_state(job, run=FakeGh("CLOSED"))["state"] == "closed"
    assert check_pr_state(job, run=FakeGh("OPEN"))["state"] == "open"

    def remote(url):
        return lambda argv, cwd: (0, url + "\n", "") if argv[:3] == ["git", "remote", "get-url"] else (1, "", "")

    gets = []

    def get(url, headers):
        gets.append((url, headers))
        if "merge_requests" in url:
            return {"state": "merged", "merged_at": "t"}
        if "pullrequests" in url:
            return {"state": "DECLINED"}
        return {"state": "closed", "merged": False}

    gl = _pushed(service, tmp_path, {"provider": "gitlab", "url": "https://gitlab.com/g/app/-/merge_requests/3", "number": 3})
    assert check_pr_state(gl, run=remote("git@gitlab.com:g/app.git"), get=get, env={"GITLAB_TOKEN": "t"})["state"] == "merged"
    assert gets[-1][0] == "https://gitlab.com/api/v4/projects/g%2Fapp/merge_requests/3"
    bb = _pushed(service, tmp_path, {"provider": "bitbucket", "url": "https://bitbucket.org/w/app/pull-requests/4"})
    assert check_pr_state(bb, run=remote("git@bitbucket.org:w/app.git"), get=get, env={"BITBUCKET_TOKEN": "t"})["state"] == "closed"
    gh = _pushed(service, tmp_path, {"provider": "github", "url": "https://github.com/o/r/pull/9", "number": 9})
    assert check_pr_state(gh, run=remote("git@github.com:o/r.git"), get=get, env={"GITHUB_TOKEN": "t"})["state"] == "closed"
    # No token: unknown with a reason, no request made.
    count = len(gets)
    result = check_pr_state(gh, run=remote("git@github.com:o/r.git"), get=get, env={})
    assert result["state"] == "unknown" and "GITHUB_TOKEN" in result["detail"] and len(gets) == count


def test_check_pr_state_never_raises(tmp_path):
    from mu.jobs.publish import check_pr_state
    service = _service(tmp_path)
    job = _pushed(service, tmp_path, {"provider": "gh", "url": "https://github.com/o/r/pull/7"})

    def boom(argv, cwd):
        raise RuntimeError("network down")

    assert check_pr_state(job, run=boom)["state"] == "unknown"


def test_poll_merged_moves_job_to_done_and_writes_back(tmp_path, monkeypatch):
    from mu.jobs import tracker_sync
    from mu.jobs.publish import poll_merged_prs
    seen = []
    monkeypatch.setattr(tracker_sync, "on_state_change",
                        lambda store, job, prior, new, reason="": seen.append((prior, new)))
    service = _service(tmp_path)
    job = _pushed(service, tmp_path, {"provider": "gh", "url": "https://github.com/o/r/pull/7"})
    out = poll_merged_prs(service, run=FakeGh("MERGED", "2026-10-01T00:00:00Z"), now=100.0)
    assert out["merged"] == [job.id]
    fresh = service.get(job.id)
    assert fresh.status == JobStatus.MERGED
    assert service.store.core_state(job.id).state.value == "done"
    assert fresh.metadata["pr"]["state"] == "merged" and fresh.metadata["pr"]["checked_at"] == 100.0
    assert ("pushed", "done") in seen
    assert any(e.event_type == "pr_merged" for e in service.store.recent_events(job.id))


def test_poll_closed_pr_flags_needs_you(tmp_path):
    from mu.jobs.publish import poll_merged_prs
    service = _service(tmp_path)
    job = _pushed(service, tmp_path, {"provider": "gh", "url": "https://github.com/o/r/pull/7"})
    out = poll_merged_prs(service, run=FakeGh("CLOSED"))
    assert out["closed"] == [job.id]
    # Parked on a human with a clear reason; the pipeline still shows "pushed".
    fresh = service.get(job.id)
    assert fresh.status == JobStatus.NEEDS_HUMAN
    assert fresh.attention_reason == AttentionReason.PR_CLOSED
    assert service.store.core_state(job.id).state.value == "pushed"
    acts = {a["id"]: a for a in vm.job_vm(service, job.id, parts=["state"])["actions"]}
    assert acts["repush"]["enabled"] and acts["repush"]["primary"]
    assert acts["markMerged"]["enabled"] and acts["markMerged"]["label"] == "Mark done"
    home = vm.home_vm(service)
    row = next(r for r in home["needs_you"] if r["id"] == job.id)
    assert row["attention_detail"] == "PR closed without merging" and row["pr"]["state"] == "closed"
    assert row["primary_action"] == "repush"
    # Flagged jobs are not polled again until a re-push replaces the PR record.
    run = FakeGh("CLOSED")
    poll_merged_prs(service, run=run)
    assert run.calls == []


def _conflict_report(job_id):
    from mu.jobs.base_drift import BaseDriftReport
    return BaseDriftReport(job_id=job_id, base_ref="main", captured_base_sha="a" * 40, current_base_sha="b" * 40,
                           branch="mucli/x", branch_head_sha="c" * 40, drifted=True, commits_behind=1,
                           mergeable=False, detail="Branch conflicts with the current base.")


def test_drift_never_flags_a_job_with_an_open_pr_conflicted(tmp_path, monkeypatch):
    """After a host squash-merge the unsquashed branch no longer test-merges;
    the PR host, not local drift, owns mergeability of a job with an open PR."""
    from mu.jobs import base_drift
    from mu.jobs.controller import JobController
    monkeypatch.setattr(base_drift, "assess_base_drift", lambda service, job_id: _conflict_report(job_id))
    service = _service(tmp_path)
    with_pr = _pushed(service, tmp_path, {"provider": "gh", "url": "https://github.com/o/r/pull/7"})
    without_pr = _pushed(service, tmp_path, {})
    base_drift.apply_base_drift(service, with_pr.id, source="controller")
    assert service.get(with_pr.id).status == JobStatus.READY_FOR_REVIEW
    assert service.get(with_pr.id).metadata["base_drift"]["mergeable"] is False  # still recorded
    assessed = []
    monkeypatch.setattr(base_drift, "apply_base_drift",
                        lambda service, job_id, source="": assessed.append(job_id) or _conflict_report(job_id))
    ctl = JobController(service, process_factory=lambda *a, **k: None)
    ctl._maybe_check_base_drift()
    assert assessed == [without_pr.id]


def test_poll_moves_conflicted_job_with_merged_pr_to_done(tmp_path):
    from mu.jobs.publish import poll_merged_prs
    service = _service(tmp_path)
    job = _pushed(service, tmp_path, {"provider": "gh", "url": "https://github.com/o/r/pull/7"})
    service.transition(job.id, JobStatus.CONFLICTED, reason="drift (before the open-PR guard)")
    out = poll_merged_prs(service, run=FakeGh("MERGED", "2026-10-01T00:00:00Z"), now=5.0)
    assert out["merged"] == [job.id]
    assert service.get(job.id).status == JobStatus.MERGED
    assert service.store.core_state(job.id).state.value == "done"


def test_poll_metadata_write_keeps_concurrent_keys(tmp_path, monkeypatch):
    """The merge poll's pr stamp is an atomic patch: a key written by another
    process between the poll's read and write survives."""
    from mu.jobs import publish
    service = _service(tmp_path)
    job = _pushed(service, tmp_path, {"provider": "gh", "url": "https://github.com/o/r/pull/7"})
    real = publish.check_pr_state

    def racing_check(job_, **kw):
        service.store.patch_metadata(job_.id, {"concurrent": "kept"})
        return real(job_, **kw)

    monkeypatch.setattr(publish, "check_pr_state", racing_check)
    publish.poll_merged_prs(service, run=FakeGh("OPEN"), now=9.0)
    meta = service.get(job.id).metadata
    assert meta["concurrent"] == "kept" and meta["pr"]["checked_at"] == 9.0 and meta["pr"]["state"] == "open"


def test_poll_is_bounded_and_open_pr_stays(tmp_path):
    from mu.jobs.publish import poll_merged_prs
    service = _service(tmp_path)
    jobs = [_pushed(service, tmp_path, {"provider": "gh", "url": f"https://github.com/o/r/pull/{i}"}) for i in range(3)]
    run = FakeGh("OPEN")
    out = poll_merged_prs(service, run=run, limit=2)
    assert len(out["checked"]) == 2 and len(run.calls) == 2
    assert all(service.get(j.id).status == JobStatus.READY_FOR_REVIEW for j in jobs)
    # Least recently checked first: the third job is polled next.
    first = set(out["checked"])
    out = poll_merged_prs(service, run=run, limit=1)
    assert out["checked"] == [next(j.id for j in jobs if j.id not in first)]


def test_controller_merge_poll_runs_off_thread_and_respects_off(tmp_path, monkeypatch):
    from mu.jobs import publish
    from mu.jobs.controller import JobController
    calls = []
    monkeypatch.setattr(publish, "poll_merged_prs", lambda service, limit=10: calls.append(limit) or {})
    service = _service(tmp_path)
    ctl = JobController(service, process_factory=lambda *a, **k: None)
    ctl.merge_poll_interval_seconds = 0
    ctl._maybe_poll_merges()
    ctl._maybe_poll_merges()
    assert calls == []
    ctl.merge_poll_interval_seconds = 0.01
    ctl._maybe_poll_merges()  # first pass only arms the timer
    assert calls == []
    time.sleep(0.02)
    ctl._maybe_poll_merges()
    ctl._merge_poll_thread.join(2)
    assert calls == [ctl.merge_poll_batch]


def test_merge_poll_setting_exists():
    from mu.jobs.job_settings import FIELD_BY_PATH
    field = FIELD_BY_PATH["publish.merge_poll_seconds"]
    assert field["default"] == 300 and field["min"] == 0


# ------------------------------------------------------------------ environment approvals
def _env_blocked(service, tmp_path):
    job = _job(service, tmp_path)
    service.transition(job.id, JobStatus.PREPARING)
    service.transition(job.id, JobStatus.RUNNING)
    approval = {"repository": str(tmp_path / "ws"), "fingerprint": "image:node:20", "kind": "image",
                "ref": "node:20", "label": "image node:20 (.mucli/jobs.toml)", "origin": "repo-image",
                "ignored_features": []}
    service.store.append_event(job.id, "build_environment_approval_required", reason="Approve it", payload=approval)
    service.require_human(job.id, AttentionReason.ENVIRONMENT_FAILURE, "Approve it")
    return service.get(job.id), approval


def test_environment_approval_surfaces_in_vm_and_actions(tmp_path):
    service = _service(tmp_path)
    job, approval = _env_blocked(service, tmp_path)
    out = vm.job_vm(service, job.id, parts=["state"])
    assert out["environment_approval"]["fingerprint"] == "image:node:20"
    assert out["environment_approval"]["ref"] == "node:20"
    actions = {a["id"]: a for a in out["actions"]}
    assert actions["approveEnvironment"]["enabled"] and actions["approveEnvironment"]["primary"]
    assert actions["approveEnvironment"]["args"] == {"fingerprint": "image:node:20"}
    assert actions["rejectEnvironment"]["enabled"]


def test_approve_environment_records_approval_and_requeues(tmp_path):
    from mu.jobs.sandbox.devcontainer import is_approved
    service = _service(tmp_path)
    job, approval = _env_blocked(service, tmp_path)
    with pytest.raises(CommandRejected):
        dispatch(service, {"type": "approveEnvironment", "job_id": job.id, "args": {"fingerprint": "image:other"}})
    assert not is_approved(approval["repository"], approval["fingerprint"])
    dispatch(service, {"type": "approveEnvironment", "job_id": job.id, "args": {"fingerprint": "image:node:20"}})
    assert is_approved(approval["repository"], approval["fingerprint"])
    assert service.get(job.id).status == JobStatus.QUEUED
    assert vm.facts_for(service, service.get(job.id)).environment_approval is None


def test_reject_environment_keeps_job_paused(tmp_path):
    from mu.jobs.sandbox.devcontainer import is_approved
    service = _service(tmp_path)
    job, approval = _env_blocked(service, tmp_path)
    dispatch(service, {"type": "rejectEnvironment", "job_id": job.id,
                       "args": {"fingerprint": "image:node:20", "reason": "use my own"}})
    fresh = service.get(job.id)
    assert fresh.status == JobStatus.NEEDS_HUMAN
    assert not is_approved(approval["repository"], approval["fingerprint"])
    assert vm.facts_for(service, fresh).environment_approval is None
    assert not action_map(vm.facts_for(service, fresh))["approveEnvironment"].enabled


def _egress_offered(service, tmp_path):
    job = _job(service, tmp_path)
    service.transition(job.id, JobStatus.PREPARING)
    service.transition(job.id, JobStatus.RUNNING)
    request = {"repository": str(tmp_path / "ws"), "fingerprint": "egress:abc123", "kind": "egress",
               "ref": "pypi.org, files.pythonhosted.org", "origin": "repo-egress",
               "label": "network egress to pypi.org, files.pythonhosted.org (.mucli/jobs.toml)",
               "ignored_features": [], "domains": ["pypi.org", "files.pythonhosted.org"]}
    service.store.append_event(job.id, "repository_egress_approval_required", reason="Approve egress", payload=request)
    return service.get(job.id), request


def test_repository_egress_approval_is_a_non_blocking_banner_on_a_running_job(tmp_path):
    from mu.jobs.sandbox.devcontainer import is_approved
    service = _service(tmp_path)
    job, request = _egress_offered(service, tmp_path)
    out = vm.job_vm(service, job.id, parts=["state"])
    pending = out["environment_approval"]
    assert pending["fingerprint"] == "egress:abc123" and pending["kind"] == "egress"
    assert pending["ref"] == "pypi.org, files.pythonhosted.org" and pending["blocking"] is False
    actions = {a["id"]: a for a in out["actions"]}
    assert actions["approveEnvironment"]["enabled"] and actions["rejectEnvironment"]["enabled"]
    assert actions["approveEnvironment"]["args"] == {"fingerprint": "egress:abc123"}
    assert not actions["approveEnvironment"]["primary"]  # running job: a banner, not a gate

    result = dispatch(service, {"type": "approveEnvironment", "job_id": job.id,
                                "args": {"fingerprint": "egress:abc123"}})
    assert is_approved(request["repository"], "egress:abc123")
    assert result.data["requeued"] is False
    assert service.get(job.id).status == JobStatus.RUNNING  # never requeued
    assert vm.facts_for(service, service.get(job.id)).environment_approval is None


def test_rejected_repository_egress_banner_goes_away_and_job_keeps_running(tmp_path):
    service = _service(tmp_path)
    job, _ = _egress_offered(service, tmp_path)
    dispatch(service, {"type": "rejectEnvironment", "job_id": job.id, "args": {"fingerprint": "egress:abc123"}})
    fresh = service.get(job.id)
    assert fresh.status == JobStatus.RUNNING
    assert vm.facts_for(service, fresh).environment_approval is None


def test_repository_egress_banner_hidden_on_terminal_jobs(tmp_path):
    service = _service(tmp_path)
    job, _ = _egress_offered(service, tmp_path)
    service.cancel(job.id)
    assert vm.facts_for(service, service.get(job.id)).environment_approval is None


def test_environment_actions_disabled_without_request(tmp_path):
    service = _service(tmp_path)
    job = _job(service, tmp_path)
    facts = vm.facts_for(service, job)
    assert action_map(facts)["approveEnvironment"].reason == "no environment approval pending"


def test_approve_endpoint_only_accepts_requested_fingerprint(tmp_path, monkeypatch):
    from mu.gui.routers import jobs as jobs_router
    from mu.jobs.sandbox import environments
    from mu.jobs.sandbox.devcontainer import is_approved
    repo = str(tmp_path / "repo")
    monkeypatch.setattr(environments, "preview", lambda probe, settings=None: {
        "suggestion": {"approval": {"repository": repo, "fingerprint": "build:abc", "kind": "build",
                                    "ref": "mucli/devcontainer-repo:abc"}}})
    app = FastAPI()
    app.include_router(jobs_router.router, prefix="/api/jobs")
    client = TestClient(app)
    bad = client.post("/api/jobs/environments/approve", json={"repository": repo, "fingerprint": "build:zzz"})
    assert bad.status_code == 409
    ok = client.post("/api/jobs/environments/approve", json={"repository": repo, "fingerprint": "build:abc"})
    assert ok.status_code == 200 and ok.json()["approved"] == "build:abc"
    assert is_approved(repo, "build:abc")


# ------------------------------------------------------------------ planner hint / budget
def test_plan_prompt_includes_planner_hint(tmp_path, monkeypatch):
    from mu.jobs import stages
    from mu.jobs.sandbox import detect
    service = _service(tmp_path)
    job = _job(service, tmp_path)
    monkeypatch.setattr(detect, "planner_hint", lambda j: "Detected toolchains: python (pyproject.toml)")
    assert "Detected toolchains: python (pyproject.toml)" in stages.build_plan_prompt(job)
    monkeypatch.setattr(detect, "planner_hint", lambda j: "")
    assert "Detected toolchains" not in stages.build_plan_prompt(job)

    def boom(j):
        raise RuntimeError("x")

    monkeypatch.setattr(detect, "planner_hint", boom)
    assert "Title: Ticket" in stages.build_plan_prompt(job)


def test_budget_in_job_header(tmp_path):
    service = _service(tmp_path)
    job = _job(service, tmp_path, max_cost_usd=20.0, max_runtime_seconds=3600)
    service.store.update_runtime_fields(job.id, cost_usd=5.0)
    out = vm.job_vm(service, job.id, parts=["state"])
    assert out["budget"]["cost_usd"] == 5.0 and out["budget"]["max_cost_usd"] == 20.0
    assert out["budget"]["cost_fraction"] == 0.25
    assert out["budget"]["max_runtime_seconds"] == 3600
    assert out["budget"]["active_seconds"] >= 0
    assert out["max_cost_usd"] == 20.0
