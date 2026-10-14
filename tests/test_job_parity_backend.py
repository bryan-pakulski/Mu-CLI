"""Backend for the jobs feature-parity contract (deny reason, push dialog,
per-test checks, viewed marks, activity, action flow fields)."""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mu.gui.routers import jobs_v2
from mu.jobs import JobService, JobStore
from mu.jobs.checks import ChecksStage, parse_junit
from mu.jobs.core import activity as activity_mod
from mu.jobs.core import vm
from mu.jobs.core.actions import JobFacts, available_actions
from mu.jobs.core.artifacts import artifacts_for, diff_file_hashes
from mu.jobs.core.changes import ChangeBus
from mu.jobs.core.commands import CommandError, CommandRejected, dispatch
from mu.jobs.core.states import CoreState, JobState
from mu.jobs.models import AttentionReason, JobStatus
from mu.jobs.publish import open_draft_pr, publish_job
from mu.jobs.sandbox.runner import claim_stage_response
from mu.jobs.stages import apply_review_verdict
from mu.jobs.ui import JobUI
from test_job_worker import git, running_job


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    activity_mod.reset_cache()
    yield
    activity_mod.reset_cache()


def _client(service):
    app = FastAPI()
    app.state.job_service = service
    app.state.job_change_bus = ChangeBus(window_s=0)
    app.include_router(jobs_v2.router, prefix="/api/jobs")
    return TestClient(app)


# ------------------------------------------------------------------ §1 deny
TARGET = {"tool_name": "write_file", "tool_args": {"path": "widget.py", "content": "x"}, "can_approve": True}


def _awaiting_tool(tmp_path):
    service, _, job, _, _ = running_job(tmp_path)
    service.require_human(job.id, "approval_required", "Write widget", payload=TARGET)
    actions = {a["id"]: a for a in vm.job_vm(service, job.id)["actions"]}
    return service, job, actions


def test_deny_reason_reaches_the_agent_and_the_audit_trail(tmp_path):
    service, job, actions = _awaiting_tool(tmp_path)
    assert actions["denyTool"]["input"] == {"label": "Reason for the agent (optional)", "optional": True,
                                            "multiline": True}
    result = dispatch(service, {"type": "denyTool", "job_id": job.id,
                                "args": {**actions["denyTool"]["args"], "message": "  use the helper instead  "}})
    assert result.data == {"message": "use the helper instead"}
    human = [e for e in service.events(job.id) if e.event_type == "human_response"][-1]
    assert human.payload["detail"] == "use the helper instead"
    # The sandbox relay hands the host-side response to the stage UI...
    response = claim_stage_response(service, job, "implement", {"kind": "approval_required", **TARGET})
    assert response["decision"] == "deny" and response["detail"] == "use the helper instead"


def test_job_ui_turns_deny_reason_into_agent_explanation(tmp_path):
    service, job, actions = _awaiting_tool(tmp_path)
    dispatch(service, {"type": "denyTool", "job_id": job.id,
                       "args": {**actions["denyTool"]["args"], "message": "not that file"}})
    # loop_body maps ("e", reason) to "User denied this tool call. Reason: <reason>".
    assert JobUI(service, job.id).request_tool_approval(tool_name="write_file") == ("e", "not that file")


def test_deny_without_reason_stays_a_plain_deny(tmp_path):
    service, job, actions = _awaiting_tool(tmp_path)
    dispatch(service, {"type": "denyTool", "job_id": job.id, "args": actions["denyTool"]["args"]})
    assert JobUI(service, job.id).request_tool_approval(tool_name="write_file") == ("n", None)


def test_deny_reason_is_bounded(tmp_path):
    service, job, actions = _awaiting_tool(tmp_path)
    with pytest.raises(CommandError):
        dispatch(service, {"type": "denyTool", "job_id": job.id,
                           "args": {**actions["denyTool"]["args"], "message": "x" * 2001}})
    assert service.store.core_state(job.id).state == JobState.NEEDS_INPUT


# ------------------------------------------------------------- §7 actions
def test_action_flow_fields():
    acts = {a.id: a.to_dict() for a in available_actions(JobFacts(core=CoreState(JobState.REVIEW_DONE),
                                                                   push_check_reason=""))}
    for cid in ("tone", "confirm", "input", "dialog", "hidden"):
        assert cid in acts["push"]
    assert acts["push"]["tone"] == "ok" and acts["push"]["dialog"] == "push"
    assert acts["rejectPlan"]["tone"] == "danger" and acts["rejectPlan"]["input"]["optional"] is False
    assert acts["approveTool"]["tone"] == "warn" and acts["denyTool"]["tone"] == "danger"
    assert {acts[c]["tone"] for c in ("retry", "restartStage", "resume")} == {"warn"}
    assert acts["discard"]["confirm"] and acts["delete"]["confirm"] and acts["restartStage"]["confirm"]
    assert acts["approvePlan"]["confirm"] is None and acts["approvePlan"]["tone"] == "ok"
    assert acts["message"]["input"]["optional"] is False
    assert acts["resume"]["input"]["optional"] and acts["reprompt"]["input"]["optional"]
    assert acts["markFindings"]["hidden"] and acts["markViewed"]["hidden"] and not acts["push"]["hidden"]


# ---------------------------------------------------------------- §2 push
EXE = {"provider": "ollama", "model": "m", "session_type": "workspace"}


def _review_done_git_job(tmp_path, mode="gh", verdict="approve"):
    from mu.jobs.verification import VerificationRun, VerificationStore

    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    for args in (("init", "-b", "main"), ("config", "user.name", "T"), ("config", "user.email", "t@e.com")):
        git(repo, *args)
    (repo / "README.md").write_text("x\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    git(repo, "remote", "add", "origin", "https://user:secret@github.com/acme/app.git")
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    job = service.create_from_payload({"title": "Ticket", "description": "Do it", "workspace_root": str(repo),
                                       "execution": {**EXE, "publish_mode": mode}})
    service.store.update_runtime_fields(job.id, branch="mucli/job-abc")
    git(repo, "branch", "mucli/job-abc")
    head = git(repo, "rev-parse", "HEAD")
    VerificationStore(service.store).save(VerificationRun(
        id="ok", job_id=job.id, status="passed", passed=True, started_at=1, finished_at=2, duration_ms=1,
        base_sha=head, head_sha=head, branch="mucli/job-abc", worktree=str(repo)))
    for status in (JobStatus.PREPARING, JobStatus.RUNNING, JobStatus.VERIFYING, JobStatus.REVIEWING):
        service.transition(job.id, status, reason="test")
    apply_review_verdict(service, job.id, {"verdict": "approve", "summary": "ok", "feedback": []})
    if verdict != "approve":
        artifacts_for(job).write_json("review.json", {"verdict": verdict, "findings": []})
    assert service.store.core_state(job.id).state == JobState.REVIEW_DONE
    return service, service.get(job.id), head


def test_push_preview_shape(tmp_path):
    service, job, head = _review_done_git_job(tmp_path)
    with _client(service) as client:
        preview = client.get(f"/api/jobs/vm/job/{job.id}/push-preview").json()
        assert client.get("/api/jobs/vm/job/nope/push-preview").status_code == 404
    assert set(preview) == {"title", "body", "branch", "base", "mode", "remote", "draft",
                            "requires_override", "verdict", "head_sha", "reason"}
    assert preview["title"] == "Ticket" and "Do it" in preview["body"]
    assert preview["branch"] == "mucli/job-abc" and preview["base"] == "main"
    assert preview["mode"] == "gh" and preview["draft"] is True and preview["reason"] == ""
    assert preview["head_sha"] == head and "secret" not in preview["remote"]
    assert preview["requires_override"] is False


def test_push_overrides_reach_the_pr_and_ready_for_review(tmp_path):
    service, job, _ = _review_done_git_job(tmp_path)
    actions = {a["id"]: a for a in vm.job_vm(service, job.id)["actions"]}
    assert actions["push"]["enabled"] and actions["push"]["dialog"] == "push"
    with pytest.raises(CommandError):
        dispatch(service, {"type": "push", "job_id": job.id, "args": {"pr_title": "t" * 201}})
    dispatch(service, {"type": "push", "job_id": job.id,
                       "args": {"pr_title": "Better title", "pr_body": "Custom body", "draft": False}})
    assert service.get(job.id).metadata["publish_override"] == {"title": "Better title", "body": "Custom body",
                                                                 "draft": False}
    calls = []

    def run(argv, cwd):
        calls.append(list(argv))
        if argv[:3] == ["git", "remote", "get-url"]:
            return 0, "git@github.com:acme/app.git\n", ""
        return 0, "https://github.com/acme/app/pull/1\n", ""

    assert publish_job(service, job.id, run=run, post=lambda *a: {}, env={}) == 0
    gh = next(c for c in calls if c[:3] == ["gh", "pr", "create"])
    assert "--draft" not in gh
    assert gh[gh.index("--title") + 1] == "Better title" and gh[gh.index("--body") + 1] == "Custom body"
    assert service.get(job.id).metadata["pr"]["draft"] is False


def test_empty_overrides_fall_back_and_rest_providers_honour_draft(tmp_path):
    service, job, head = _review_done_git_job(tmp_path, mode="github")
    job = replace(job, metadata={**job.metadata, "publish_override": {"title": "", "body": "", "draft": False}})
    posted = []

    def run(argv, cwd):
        return (0, "https://github.com/acme/app.git\n", "") if argv[:3] == ["git", "remote", "get-url"] else (0, "", "")

    open_draft_pr(job, run=run, post=lambda url, h, body: posted.append(body) or {"html_url": "u"},
                  env={"GITHUB_TOKEN": "t"}, verified_head=head)
    assert posted[0]["draft"] is False and posted[0]["title"] == "Ticket"
    gl = replace(job, execution={**job.execution, "publish_mode": "gitlab"})
    run_gl = lambda argv, cwd: (0, "https://gitlab.com/g/app.git\n", "") if argv[:3] == ["git", "remote", "get-url"] else (0, "", "")
    open_draft_pr(gl, run=run_gl, post=lambda url, h, body: posted.append(body) or {}, env={"GITLAB_TOKEN": "t"},
                  verified_head=head)
    assert posted[1]["title"] == "Ticket"
    default = replace(gl, metadata={k: v for k, v in gl.metadata.items() if k != "publish_override"})
    open_draft_pr(default, run=run_gl, post=lambda url, h, body: posted.append(body) or {}, env={"GITLAB_TOKEN": "t"},
                  verified_head=head)
    assert posted[2]["title"] == "Draft: Ticket"


def test_do_not_merge_still_requires_override_reason(tmp_path):
    service, job, _ = _review_done_git_job(tmp_path, verdict="do_not_merge")
    assert vm.push_preview_vm(service, job.id)["requires_override"] is True
    with pytest.raises(CommandRejected):
        dispatch(service, {"type": "push", "job_id": job.id, "args": {"pr_title": "x"}})
    assert "publish_override" not in service.get(job.id).metadata
    dispatch(service, {"type": "push", "job_id": job.id, "args": {"override_reason": "hotfix"}})
    assert service.get(job.id).metadata["publish_override"]["draft"] is True


# ---------------------------------------------------------------- §3 checks
JUNIT = ('<testsuites><testsuite name="pkg.suite"><testcase classname="tests.test_a" name="test_ok" time="0.1"/>'
         '<testcase classname="tests.test_a" name="test_bad" time="0.2"><failure message="assert 1 == 2">'
         'Traceback...</failure></testcase><testcase name="test_err"><error message="boom"/></testcase>'
         '</testsuite></testsuites>')


def test_parse_junit_adds_suite_name_and_message():
    cases = parse_junit(JUNIT)["cases"]
    assert [(c["suite"], c["name"], c["status"]) for c in cases] == [
        ("tests.test_a", "test_ok", "passed"), ("tests.test_a", "test_bad", "failed"), ("pkg.suite", "test_err", "failed")]
    assert cases[1]["message"] == "assert 1 == 2\nTraceback..." and cases[1]["id"] == "tests.test_a::test_bad"
    assert cases[2]["message"] == "boom" and "message" not in cases[0]


def test_checks_summary_trigger_durations_and_vm_parts(tmp_path):
    service, _, job, _, _ = running_job(tmp_path)
    job = replace(job, validation_commands=["pytest -q"])
    junit = parse_junit(JUNIT)
    outcomes = iter([{"passed": True, "return_code": 0, "duration_ms": 1500},
                     {"passed": False, "return_code": 1, "junit": junit, "duration_ms": 900},
                     {"passed": True, "return_code": 0}])  # baseline run on base_sha
    stage = ChecksStage(service, run_suite=lambda *a: next(outcomes), settings={})
    stage.verify(job)
    service.store.update_runtime_fields(job.id, metadata_json={**service.get(job.id).metadata,
                                                               "checks_trigger": "recheck"})
    stage.verify(job)
    assert "checks_trigger" not in service.get(job.id).metadata  # consumed by the run
    art = artifacts_for(job)
    first, second = (art.read_json(f"checks/run-{n}/summary.json") for n in (1, 2))
    assert first["trigger"] == "auto" and second["trigger"] == "recheck"
    assert isinstance(second["duration_ms"], int) and second["started_at"] <= second["finished_at"]
    assert second["suites"][0]["duration_ms"] == 900
    # A run written before these fields existed must still render.
    art.write_json("checks/run-0/summary.json", {"run": 0, "passed": True, "suites": [{"suite": "check1", "passed": True}]})
    checks = vm.job_vm(service, job.id, parts=["checks"])["checks"]
    assert [r["run"] for r in checks["runs"]] == [0, 1, 2]
    names = {c["name"]: c for c in checks["failed_cases"]}
    assert set(names) == {"test_bad", "test_err"}
    bad = names["test_bad"]
    assert bad["run"] == 2 and bad["suite"] == "check1" and bad["id"] == "tests.test_a::test_bad"
    assert bad["message"].startswith("assert 1 == 2") and bad["new"] is True  # base_sha triage marks it new
    assert checks["history"]["check1"] == [
        {"run": 0, "passed": True, "duration_ms": None, "finished_at": None},
        {"run": 1, "passed": True, "duration_ms": 1500, "finished_at": first["finished_at"]},
        {"run": 2, "passed": False, "duration_ms": 900, "finished_at": second["finished_at"]},
    ]


def test_preexisting_failed_case_is_not_new(tmp_path):
    service, _, job, _, _ = running_job(tmp_path)
    art = artifacts_for(job).ensure()
    art.next_check_run()
    art.write_json("checks/run-1/summary.json", {"passed": False, "suites": [{
        "suite": "unit", "passed": False, "junit": parse_junit(JUNIT),
        "preexisting_failures": ["tests.test_a::test_bad"], "new_failures": ["::test_err"]}]})
    failed = {c["name"]: c["new"] for c in vm.job_vm(service, job.id, parts=["checks"])["checks"]["failed_cases"]}
    assert failed == {"test_bad": False, "test_err": True}


def test_recheck_and_reprompt_set_the_trigger(tmp_path):
    service, job, _ = _review_done_git_job(tmp_path)
    service.store.update_runtime_fields(job.id, worktree=str(Path(job.repository)))
    dispatch(service, {"type": "recheck", "job_id": job.id})
    assert service.get(job.id).metadata["checks_trigger"] == "recheck"
    service2, job2, _ = _review_done_git_job(tmp_path / "b")
    artifacts_for(job2).write_json("review.json", {"verdict": "needs_work", "findings": [
        {"id": "F-1", "severity": "major", "title": "t", "detail": "d", "disposition": "open"}]})
    dispatch(service2, {"type": "reprompt", "job_id": job2.id, "args": {"finding_ids": ["F-1"]}})
    assert service2.get(job2.id).metadata["checks_trigger"] == "fix"


# ---------------------------------------------------------------- §4 viewed
PATCH = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n+y\n"
         "diff --git a/dir/b c.py b/dir/b c.py\n--- a/dir/b c.py\n+++ b/dir/b c.py\n@@ -1 +1 @@\n-1\n+2\n")


def test_diff_file_hashes_split_per_file():
    hashes = diff_file_hashes(PATCH)
    assert set(hashes) == {"a.py", "dir/b c.py"}
    assert diff_file_hashes(PATCH.replace("+y", "+z"))["dir/b c.py"] == hashes["dir/b c.py"]
    assert diff_file_hashes(PATCH.replace("+y", "+z"))["a.py"] != hashes["a.py"]


def test_mark_viewed_tracks_hash_and_works_in_terminal_states(tmp_path):
    service, _, job, _, _ = running_job(tmp_path)
    actions = {a["id"]: a for a in vm.job_vm(service, job.id)["actions"]}
    assert not actions["markViewed"]["enabled"] and actions["markViewed"]["reason"] == "no diff to review"
    art = artifacts_for(job).ensure()
    art.write_text("diff.patch", PATCH)
    bus = ChangeBus(window_s=0)
    result = dispatch(service, {"type": "markViewed", "job_id": job.id, "args": {"path": "a.py", "viewed": True}}, bus=bus)
    assert result.parts == ["diff"] and result.data["viewed"] == ["a.py"]
    assert [c.parts for c in bus.flush(force=True) if c.scope == "job"] == [["diff"]]
    assert vm.job_vm(service, job.id, parts=["diff"])["diff"]["viewed"] == ["a.py"]
    with pytest.raises(CommandError):
        dispatch(service, {"type": "markViewed", "job_id": job.id, "args": {"path": "nope.py", "viewed": True}})
    art.write_text("diff.patch", PATCH.replace("+y", "+changed"))
    assert vm.job_vm(service, job.id, parts=["diff"])["diff"]["viewed"] == []
    dispatch(service, {"type": "markViewed", "job_id": job.id, "args": {"path": "dir/b c.py", "viewed": True}})
    dispatch(service, {"type": "discard", "job_id": job.id})
    assert service.store.core_state(job.id).state == JobState.CANCELLED
    dispatch(service, {"type": "markViewed", "job_id": job.id, "args": {"path": "dir/b c.py", "viewed": False}})
    assert vm.job_vm(service, job.id, parts=["diff"])["diff"]["viewed"] == []
    assert json.loads(Path(art.path("viewed.json")).read_text()) == {"a.py": diff_file_hashes(PATCH)["a.py"]}


def test_mark_viewed_disabled_when_archived():
    acts = {a.id: a for a in available_actions(JobFacts(core=CoreState(JobState.DONE), archived=True, has_diff=True))}
    assert not acts["markViewed"].enabled
    acts = {a.id: a for a in available_actions(JobFacts(core=CoreState(JobState.DONE), has_diff=True))}
    assert acts["markViewed"].enabled


# --------------------------------------------------------------- §5 activity
class FakeDocker:
    def __init__(self, rows, *, ps_rc=0):
        self.rows, self.ps_rc, self.calls = rows, ps_rc, []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if argv[1] == "ps":
            out = "\n".join(json.dumps(r) for r in self.rows)
            return subprocess.CompletedProcess(argv, self.ps_rc, out, "" if self.ps_rc == 0 else "daemon down")
        if argv[1] == "rm":
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[1] == "logs":
            return subprocess.CompletedProcess(argv, 0, "container line\n", "")
        return subprocess.CompletedProcess(argv, 1, "", "unexpected")


def _ps_row(name, state="running"):
    return {"ID": "abcdef1234567890", "Names": name, "Image": "mucli-job-runtime:latest", "State": state,
            "Status": "Up 2 minutes", "CreatedAt": "2026-10-13 10:00:00 +0000 UTC"}


def _fake_docker(monkeypatch, rows, **kw):
    fake = FakeDocker(rows, **kw)
    monkeypatch.setattr(activity_mod, "_docker", lambda: "/usr/bin/docker")
    monkeypatch.setattr(activity_mod.subprocess, "run", fake)
    monkeypatch.setattr(activity_mod, "_refresh_stats", lambda names: None)
    return fake


def test_activity_without_docker_never_raises(tmp_path, monkeypatch):
    service, _, job, _, _ = running_job(tmp_path)
    monkeypatch.setattr(activity_mod, "_docker", lambda: None)
    data = vm.activity_vm(service)
    assert data["engine"] == "off" and data["orphans"] == []
    assert data["counts"] == {"agents": 1, "checks": 0, "containers": 0, "needs_you": 0}
    row = data["jobs"][0]
    assert row["id"] == job.id and row["kind"] == "agent" and row["stage"] == "implement"
    assert row["containers"] == [] and row["worker_id"] == "worker" and row["since"] > 0
    _fake_docker(monkeypatch, [], ps_rc=1)
    activity_mod.reset_cache()
    assert vm.activity_vm(service)["engine"] == "down"


def test_activity_attributes_containers_and_lists_orphans(tmp_path, monkeypatch):
    service, _, job, _, _ = running_job(tmp_path)
    service.store.append_event(job.id, "sandbox_event", payload={"stage": "implement", "event": "tool_call_ui",
                                                                 "payload": {"tool_name": "read_file"}})
    mine = f"mucli-job-{job.id[:12]}-implement-1a2b3c4d"
    orphan = "mucli-job-0123456789ab-checks-deadbeef"
    fake = _fake_docker(monkeypatch, [_ps_row(mine), _ps_row(orphan, "exited"), _ps_row("other-container")])
    data = vm.activity_vm(service)
    assert data["engine"] == "up" and data["counts"]["containers"] == 2
    row = data["jobs"][0]
    assert row["last"] == "tool: read_file"
    (container,) = row["containers"]
    assert container == {"name": mine, "id": "abcdef123456", "image": "mucli-job-runtime:latest", "status": "running",
                         "status_text": "Up 2 minutes", "stage": "implement", "started_at": container["started_at"],
                         "role": "stage", "binding": "", "gpus": "",
                         "cpu_percent": None, "mem_bytes": None, "mem_limit": None}
    assert abs(container["started_at"] - 1791885600.0) < 1
    assert [o["name"] for o in data["orphans"]] == [orphan] and data["orphans"][0]["stage"] == "checks"
    vm.activity_vm(service)
    assert sum(c[1] == "ps" for c in fake.calls) == 1  # cached for >= 2 s


def test_activity_uses_sampled_stats(tmp_path, monkeypatch):
    service, _, job, _, _ = running_job(tmp_path)
    mine = f"mucli-job-{job.id[:12]}-implement-1a2b3c4d"
    _fake_docker(monkeypatch, [_ps_row(mine)])

    def sample(names):
        with activity_mod._CACHE.lock:
            activity_mod._CACHE.stats = {n: {"cpu_percent": 12.5, "mem_bytes": 1000, "mem_limit": 4000} for n in names}
            activity_mod._CACHE.stats_at = time.time()
    monkeypatch.setattr(activity_mod, "_refresh_stats", sample)
    (container,) = vm.activity_vm(service)["jobs"][0]["containers"]
    assert (container["cpu_percent"], container["mem_bytes"], container["mem_limit"]) == (12.5, 1000, 4000)


def test_remove_container_guards_active_jobs(tmp_path, monkeypatch):
    service, _, job, _, _ = running_job(tmp_path)
    mine = f"mucli-job-{job.id[:12]}-implement-1a2b3c4d"
    fake = _fake_docker(monkeypatch, [_ps_row(mine)])
    with _client(service) as client:
        assert client.post(f"/api/jobs/activity/containers/{mine}/remove").status_code == 409
        assert client.post("/api/jobs/activity/containers/mucli-session-x/remove").status_code == 400
        assert client.post("/api/jobs/activity/containers/mucli-job-0123456789ab-checks-1/remove").json() == {"ok": True}
        assert ["/usr/bin/docker", "rm", "-f", "mucli-job-0123456789ab-checks-1"] in fake.calls
        dispatch(service, {"type": "discard", "job_id": job.id})
        assert client.post(f"/api/jobs/activity/containers/{mine}/remove").status_code == 200


def test_status_and_logs_endpoints(tmp_path, monkeypatch):
    service, _, job, _, _ = running_job(tmp_path)
    monkeypatch.setattr(activity_mod, "_docker", lambda: None)
    log_dir = Path(service.store.path).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    (log_dir / f"{job.id}.log").write_text("x" * 5000 + "\nlast worker line\n")
    with _client(service) as client:
        assert client.get("/api/jobs/vm/status").json() == {"agents": 1, "checks": 0, "needs_you": 0, "engine": "off"}
        logs = client.get(f"/api/jobs/vm/activity/logs/{job.id}?tail_bytes=1024").json()
        assert logs["source"] == "worker" and logs["truncated"] is True and logs["text"].endswith("last worker line\n")
        assert client.get("/api/jobs/vm/activity/logs/missing").status_code == 404
        activity = client.get("/api/jobs/vm/activity").json()
        assert set(activity) == {"generated_at", "engine", "counts", "jobs", "orphans"}
    (log_dir / f"{job.id}.log").unlink()
    _fake_docker(monkeypatch, [_ps_row(f"mucli-job-{job.id[:12]}-implement-1a2b3c4d")])
    assert vm.activity_logs_vm(service, job.id) == {"text": "container line\n", "truncated": False, "source": "container"}


def test_waiting_job_with_container_is_listed(tmp_path, monkeypatch):
    service, job, _ = _awaiting_tool(tmp_path)
    _fake_docker(monkeypatch, [])
    assert vm.activity_vm(service)["jobs"] == []
    activity_mod.reset_cache()
    _fake_docker(monkeypatch, [_ps_row(f"mucli-job-{job.id[:12]}-implement-1a2b3c4d", "exited")])
    data = vm.activity_vm(service)
    assert data["jobs"][0]["kind"] == "waiting" and data["jobs"][0]["stage"] == "implement"
    assert data["counts"]["needs_you"] == 1 and data["orphans"] == []
