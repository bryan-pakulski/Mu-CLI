"""Cross-surface / API-contract fixes in job mode.

* ticket import resolves ``defaults.repository`` (git URL) like POST /api/jobs;
* the job cost field: blank = Settings default, explicit 0 = no cap;
* terminal ``approve-tool`` / ``deny-tool`` / ``approve-environment`` target the pending request;
* ``/job jira pull --force`` and the "Run again" surfaces;
* ticket import is workspace/repository-first on the web;
* untrusted ticket markers never reach receipts or History search;
* the skills loader does not 412 on the Work page;
* the generated job-settings table matches RULES_SCHEMA.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mu.commands import job as terminal
from mu.gui.routers import jobs as jobs_router
from mu.integrations import jira
from mu.jobs import JobService, JobSpec, JobStore, tracker_sync
from mu.jobs.core import vm
from mu.jobs.models import AttentionReason, JobStatus

ROOT = Path(__file__).resolve().parents[1]
EXECUTION = {"provider": "ollama", "model": "m", "session_type": "workspace"}


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def home(tmp_path, monkeypatch):
    from utils import config

    root = tmp_path / "home"
    monkeypatch.setenv("MUCLI_HOME", str(root))
    monkeypatch.setattr(config, "HISTORY_DIR", str(root))
    for key in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(key, "T")
    for key in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(key, "t@example.com")
    return root


def _remote(tmp_path):
    work = tmp_path / "src" / "proj"
    work.mkdir(parents=True)
    git(work, "init", "-q", "-b", "main")
    (work / "README.md").write_text("hello\n")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "init")
    bare = tmp_path / "remotes" / "proj.git"
    bare.parent.mkdir(parents=True)
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
    return bare, work


def _service(tmp_path):
    return JobService(JobStore(str(tmp_path / "jobs.sqlite3")))


def _client(service):
    app = FastAPI()
    app.include_router(jobs_router.router, prefix="/api/jobs")
    app.state.job_service = service
    return TestClient(app)


# ------------------------------------------------------------------ 1. import + repository URL


def test_jira_import_clones_defaults_repository(tmp_path, home, monkeypatch):
    bare, work = _remote(tmp_path)
    seen = []

    def _import(svc, client, *, keys=(), jql="", defaults=None, max_results=20, force=False):
        seen.append({"defaults": dict(defaults or {}), "force": force})
        return {"created": [], "skipped": [], "errors": []}

    monkeypatch.setattr(jira, "import_issues", _import)
    monkeypatch.setattr(jobs_router, "_jira_client", lambda: object())
    with _client(_service(tmp_path)) as client:
        resp = client.post("/api/jobs/jira/import", json={
            "keys": ["PROJ-1"], "force": True,
            "defaults": {"repository": f"file://{bare}", "execution": dict(EXECUTION)}})
    assert resp.status_code == 200, resp.text
    defaults = seen[-1]["defaults"]
    assert defaults["workspace_root"].startswith(str(home / "repos"))
    assert "repository" not in defaults
    assert defaults["metadata"]["repository_url"] == f"file://{bare}"
    assert defaults["base_sha"] == git(work, "rev-parse", "HEAD")
    assert seen[-1]["force"] is True


def test_tracker_import_clones_defaults_repository(tmp_path, home, monkeypatch):
    bare, _ = _remote(tmp_path)
    seen = []

    def _import(svc, tracker, *, keys=(), query="", defaults=None, max_results=20, force=False):
        seen.append(dict(defaults or {}))
        return {"created": [], "skipped": [], "errors": []}

    monkeypatch.setattr(tracker_sync, "tracker_for", lambda name: object())
    monkeypatch.setattr(tracker_sync, "import_tickets", _import)
    with _client(_service(tmp_path)) as client:
        resp = client.post("/api/jobs/tracker/import", json={
            "tracker": "github", "keys": ["acme/api#7"], "defaults": {"repository": f"file://{bare}"}})
    assert resp.status_code == 200, resp.text
    assert seen[-1]["workspace_root"].startswith(str(home / "repos"))


def test_import_clone_errors_and_missing_workspace_are_400(tmp_path, home, monkeypatch):
    monkeypatch.setattr(jobs_router, "_jira_client", lambda: object())
    with _client(_service(tmp_path)) as client:
        bad = client.post("/api/jobs/jira/import", json={
            "keys": ["PROJ-1"], "defaults": {"repository": "https://u:tok@example.com/a/b.git"}})
        missing = client.post("/api/jobs/jira/import", json={"keys": ["PROJ-1"]})
    assert bad.status_code == 400
    assert bad.json()["error"] == {"kind": "credentials_in_url", "host": "example.com"}
    assert "tok" not in bad.json()["detail"]
    assert missing.status_code == 400
    assert missing.json()["detail"] == "Choose a workspace or repository for the imported jobs."


# ------------------------------------------------------------------ 2. cost field


def test_cost_zero_is_unlimited_and_blank_uses_settings_default(tmp_path, home):
    service = _service(tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    base = {"title": "T", "workspace_root": str(ws), "execution": dict(EXECUTION)}
    assert service.create_from_payload({**base, "max_cost_usd": 0}).max_cost_usd is None
    assert service.create_from_payload(dict(base)).max_cost_usd == 100.0
    assert service.create_from_payload({**base, "max_cost_usd": 7.5}).max_cost_usd == 7.5


def test_web_cost_field_sends_zero_and_names_the_settings_default():
    html, js = _read("mu/gui/templates/work.html"), _read("mu/gui/static/js/work.js")
    assert 'id="job-cost" type="number" min="0"' in html and 'placeholder="no limit"' not in html.split('id="job-cost"')[1][:120]
    assert "Settings default ($100)" in html and "0 = no cap" in html
    assert "if (cost !== undefined) common.max_cost_usd = cost;" in js
    assert "if (cost > 0) common.max_cost_usd" not in js
    assert "costPlaceholder(values['defaults.max_cost_usd'])" in js


# ------------------------------------------------------------------ 3. terminal tool / environment approvals


class UI:
    def __init__(self):
        self.info, self.errors = [], []

    def show_info(self, text):
        self.info.append(text)

    def show_error(self, text):
        self.errors.append(text)


@pytest.fixture
def term(monkeypatch):
    def bind(service):
        monkeypatch.setattr(terminal, "get_default_job_service", lambda: service)
        monkeypatch.setattr(terminal, "ensure_controller_daemon", lambda: SimpleNamespace(running=True, detail=""))
        return SimpleNamespace(ui=UI())
    return bind


def _running(tmp_path):
    from test_job_worker import running_job

    service, _, job, _, _ = running_job(tmp_path)
    return service, job


def test_terminal_approve_tool_targets_the_pending_request(tmp_path, home, term):
    from mu.jobs.sandbox.runner import claim_stage_response

    service, job = _running(tmp_path)
    session = term(service)
    assert not terminal.job_cmd(session, f"approve-tool {job.id[:10]}", allow_prompt=False).ok  # nothing pending
    target = {"tool_name": "write_file", "tool_args": {"path": "w.py", "content": "x"}, "can_approve": True}
    service.require_human(job.id, "approval_required", "Write w.py", payload=target)
    result = terminal.job_cmd(session, f"approve-tool {job.id[:10]}", allow_prompt=False)
    assert result.ok, result.message
    assert claim_stage_response(service, job, "implement", {"kind": "approval_required", **target})["decision"] == "approve"


def test_terminal_deny_tool_passes_the_reason(tmp_path, home, term):
    service, job = _running(tmp_path)
    session = term(service)
    target = {"tool_name": "bash", "tool_args": {"command": "rm -rf /"}, "can_approve": True}
    service.require_human(job.id, "approval_required", "Run bash", payload=target)
    result = terminal.job_cmd(session, f"deny-tool {job.id[:10]} too dangerous", allow_prompt=False)
    assert result.ok, result.message
    assert result.data["data"] == {"message": "too dangerous"}
    assert vm.facts_for(service, service.get(job.id)).pending_approval is None


def _env_blocked(service, tmp_path):
    repo = tmp_path / "envrepo"
    repo.mkdir()
    job = service.create(JobSpec(title="Env", repository=str(repo), execution=dict(EXECUTION)))
    service.transition(job.id, JobStatus.PREPARING)
    service.transition(job.id, JobStatus.RUNNING)
    approval = {"repository": str(repo), "fingerprint": "image:node:20", "kind": "image", "ref": "node:20",
                "label": "image node:20", "origin": "repo-image", "ignored_features": []}
    service.store.append_event(job.id, "build_environment_approval_required", reason="Approve it", payload=approval)
    service.require_human(job.id, AttentionReason.ENVIRONMENT_FAILURE, "Approve it")
    return service.get(job.id), approval


def test_terminal_approve_environment_fills_the_fingerprint(tmp_path, home, term):
    from mu.jobs.sandbox.devcontainer import is_approved

    service = _service(tmp_path)
    job, approval = _env_blocked(service, tmp_path)
    session = term(service)
    assert terminal._action_kind("approve-environment") == "approveEnvironment"
    assert terminal._action_kind("reject-environment") == "rejectEnvironment"
    result = terminal.job_cmd(session, f"approve-environment {job.id[:10]}", allow_prompt=False)
    assert result.ok, result.message
    assert is_approved(approval["repository"], approval["fingerprint"])
    assert service.get(job.id).status == JobStatus.QUEUED


def test_terminal_reject_environment_records_reason(tmp_path, home, term):
    service = _service(tmp_path)
    job, _ = _env_blocked(service, tmp_path)
    result = terminal.job_cmd(term(service), f"reject-environment {job.id[:10]} not that image", allow_prompt=False)
    assert result.ok, result.message
    events = [e for e in service.events(job.id) if e.event_type == "build_environment_rejected"]
    assert events and events[-1].reason == "not that image"


# ------------------------------------------------------------------ 6. run again / --force


def test_terminal_jira_pull_force(tmp_path, home, term, monkeypatch):
    seen = []

    def fake_import(svc, *, keys, jql, defaults, force=False):
        seen.append((list(keys), force))
        return {"created": [], "errors": [],
                "skipped": [{"key": keys[0], "job_id": "old1234567", "reason": "latest job is done (x)"}]}

    monkeypatch.setattr(terminal.commands, "import_jira", fake_import)
    ws = tmp_path / "repo"
    ws.mkdir()
    session = term(_service(tmp_path))
    plain = terminal.job_cmd(session, f"jira pull PROJ-7 --in {ws}", allow_prompt=False)
    assert seen[-1] == (["PROJ-7"], False) and "--force" in plain.message
    terminal.job_cmd(session, f"jira pull PROJ-7 --in {ws} --force", allow_prompt=False)
    assert seen[-1] == (["PROJ-7"], True)
    terminal.job_cmd(session, f"PROJ-8 --force --in {ws}", allow_prompt=False)  # one-shot
    assert seen[-1] == (["PROJ-8"], True)


def test_done_ticket_skip_reason_names_run_again():
    import inspect

    src = inspect.getsource(tracker_sync.duplicate_of)
    assert "latest job is done" in src and "Run again" in src and "use force" not in src


def test_web_tickets_import_is_workspace_first_with_run_again():
    js, jira_js = _read("mu/gui/static/js/work.js"), _read("mu/gui/static/js/work_jira.js")
    assert "async function chooseImportTarget(" in js and "chooseImportTarget, loadSessions" in js
    assert "Session (optional)" in js and 'id="work-import-force"' in js
    assert "isRepoUrl(path) ? { repository: path } : { workspace_root: path }" in js
    assert "body.defaults = { ...target.defaults };" in jira_js and "body.force = true" in jira_js
    assert "function offerRunAgain(keys)" in jira_js and "latest job is done" in jira_js
    assert "Tickets page, #/boards" in jira_js.splitlines()[0]
    assert "paste a git URL" in js


# ------------------------------------------------------------------ 8. untrusted ticket markers


def test_receipt_and_history_strip_untrusted_ticket_frame(tmp_path, home):
    from mu.jobs.management import JobManagementService
    from mu.jobs.receipt import JobReceiptBuilder

    service = _service(tmp_path)
    framed = jira.frame_untrusted("Fix the login crash", tracker="jira", key="PROJ-1")
    assert jira.UNTRUSTED_OPEN in framed
    job = service.create(JobSpec(title="PROJ-1: Login", description=framed, repository=str(tmp_path)))
    receipt = JobReceiptBuilder(service, root=str(tmp_path / "evidence")).build(job.id)
    assert receipt["job"]["description"] == "Fix the login crash"
    assert receipt["acceptance_review"] is None  # retired field, still read tolerantly
    management = JobManagementService(service)
    assert management.query_jobs(q="UNTRUSTED", scope="all")["total"] == 0
    hit = management.query_jobs(q="login crash", scope="all")
    assert hit["total"] == 1 and hit["jobs"][0]["description"] == "Fix the login crash"


def test_summary_no_longer_mentions_acceptance_review():
    assert "acceptance_review" not in _read("mu/commands/job.py")


# ------------------------------------------------------------------ 9. skills loader on /work


def test_skills_loader_skips_the_work_page():
    app = _read("mu/gui/static/js/app.js")
    load = app.split('Alpine.store("skills", {', 1)[1].split("enabledCount()", 1)[0]
    assert 'if (document.getElementById("work-app")) return;' in load
    assert load.index('getElementById("work-app")') < load.index('fetch("/api/skills")')
    assert 'id="work-app"' in _read("mu/gui/templates/work.html")


# ------------------------------------------------------------------ 10. generated settings table


def test_configuration_job_settings_table_matches_schema():
    from mu.jobs import settings_doc
    from mu.jobs.job_settings import FIELD_BY_PATH

    text = _read("documentation/configuration.md")
    assert settings_doc.current_block(text) == settings_doc.markdown_table(), \
        "run: python3 -m mu.jobs.settings_doc --write"
    for path in FIELD_BY_PATH:
        assert f"| `{path}` |" in text, path
    assert settings_doc.render_document(text) == text
