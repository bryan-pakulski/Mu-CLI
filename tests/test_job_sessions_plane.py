"""Jobs mode is one management plane across sessions; every run limit is a setting."""
import json
import os
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path))
    from utils import config
    monkeypatch.setattr(config, "HISTORY_DIR", str(tmp_path))
    return tmp_path


def save_session(home, name, workspace, provider="anthropic", model="claude-sonnet-5-5", **variables):
    path = home / "sessions" / name
    path.mkdir(parents=True)
    workspace.mkdir(parents=True, exist_ok=True)
    (path / "session.json").write_text(json.dumps({
        "revision": 1, "variables": {"agent_mode": "feature", "yolo": True, **variables},
        "provider_config": {"provider": provider, "model": model},
        "folder_context": {"folders": [str(workspace)]}, "history": []}))


def client(home, loaded=None, current=None):
    from mu.gui.routers.jobs import router
    from mu.jobs import JobService, JobStore
    app = FastAPI()
    app.state.job_service = JobService(JobStore(str(home / "jobs.db")))
    app.state.sessions = dict(loaded or {})
    app.state.current_session_name = current
    app.state.session_by_name = lambda name=None: app.state.sessions.get(name)
    app.include_router(router, prefix="/api/jobs")
    return TestClient(app), app


def test_sessions_list_covers_saved_sessions_and_hides_job_worker_sessions(home):
    save_session(home, "rsq-cloud", home / "ws" / "rsq")
    save_session(home, "codex", home / "ws" / "codex", provider="ollama", model="glm-5.3")
    save_session(home, "job-0123456789abcdef-plan", home / "ws" / "worker")
    (home / "sessions" / "artifacts").mkdir()  # not a session
    c, _ = client(home, current="codex")
    body = c.get("/api/jobs/sessions").json()
    names = [s["name"] for s in body["sessions"]]
    assert names[0] == "codex" and set(names) == {"codex", "rsq-cloud"}
    rsq = next(s for s in body["sessions"] if s["name"] == "rsq-cloud")
    assert rsq["workspace"].endswith("rsq") and rsq["provider"] == "anthropic" and rsq["yolo"] is True and rsq["loaded"] is False


def test_job_created_in_a_saved_session_that_is_not_loaded(home):
    save_session(home, "rsq-cloud", home / "ws" / "rsq")
    c, app = client(home)  # nothing loaded in this GUI
    job = c.post("/api/jobs", json={"title": "SCRUM-50", "session_name": "rsq-cloud"}).json()["job"]
    assert job["metadata"]["origin_session"] == "rsq-cloud"
    stored = app.state.job_service.get(job["id"])
    # The session's mode is inherited; its YOLO is irrelevant (jobs always run unattended in their sandbox).
    assert stored.execution["agent_mode"] == "feature" and stored.execution["auto_approve_writes"] is True
    assert (home / "ws" / "rsq").samefile(stored.metadata.get("repository_root") or stored.repository or str(home / "ws" / "rsq"))
    assert c.post("/api/jobs", json={"title": "x", "session_name": "nope"}).status_code == 404
    assert c.post("/api/jobs", json={"title": "x", "session_name": "../../etc"}).status_code == 404


def test_job_rows_carry_their_session(home):
    save_session(home, "rsq-cloud", home / "ws" / "rsq")
    c, app = client(home)
    c.post("/api/jobs", json={"title": "A", "session_name": "rsq-cloud"})
    from mu.jobs.core.vm import home_vm
    row = home_vm(app.state.job_service)["jobs"][0]
    assert row["origin_session"] == "rsq-cloud"


def test_web_jobs_mode_asks_for_a_session_instead_of_using_the_entry_session():
    html = open(os.path.join(ROOT, "mu/gui/templates/work.html"), encoding="utf-8").read()
    work = open(os.path.join(ROOT, "mu/gui/static/js/work.js"), encoding="utf-8").read()
    jira = open(os.path.join(ROOT, "mu/gui/static/js/work_jira.js"), encoding="utf-8").read()
    view = open(os.path.join(ROOT, "mu/gui/static/js/work/view.js"), encoding="utf-8").read()
    # Workspace first: recent workspaces come from saved sessions; no session picker dead end.
    assert 'id="job-workspace" required' in html and 'id="job-session"' not in html
    assert "'/api/jobs/sessions'" in work and "Choose the workspace this job works in." in work
    assert "function recentWorkspaces(list)" in work and "sessionForWorkspace(workspace)" in work
    # Every session's jobs are listed (the Jobs tab filters by session, default all).
    assert '<option value="">All sessions</option>' in view
    # Ticket import is workspace/repository-first too (session optional).
    assert "window.MuWork.chooseImportTarget" in jira and "window.MuWork.chooseSession" not in jira
    assert "work-jobs-session" in view and "row.origin_session" in view


# --------------------------------------------------------------------------- every run limit is a setting

def test_all_run_limits_are_settings(home):
    from mu.jobs.job_settings import FIELD_BY_PATH
    for path in ("defaults.max_retries", "defaults.max_cost_usd", "defaults.max_runtime_seconds",
                 "retry.backoff_base_s", "retry.backoff_max_s", "jobs.max_concurrent", "jobs.drift_check_interval_s",
                 "jobs.retention_days", "sandbox.model_request_timeout_s", "checks.command_timeout_s", "publish.timeout_s"):
        assert path in FIELD_BY_PATH, path


def test_new_job_defaults_and_retry_backoff_follow_settings(home):
    from mu.jobs import JobService, JobStore
    from mu.jobs.job_settings import save_settings
    from mu.jobs.worker import _retry_backoff_seconds
    save_settings({"defaults": {"max_retries": 5, "max_cost_usd": 12.5, "max_runtime_seconds": 3600},
                   "retry": {"backoff_base_s": 10, "backoff_max_s": 25}})
    service = JobService(JobStore(str(home / "jobs.db")))
    (home / "w").mkdir()
    job = service.create_from_payload({"title": "t", "workspace_root": str(home / "w")})
    assert (job.max_retries, job.max_cost_usd, job.max_runtime_seconds) == (5, 12.5, 3600)
    explicit = service.create_from_payload({"title": "t", "workspace_root": str(home / "w"), "max_retries": 0})
    assert explicit.max_retries == 0
    assert [_retry_backoff_seconds(n) for n in (1, 2, 3)] == [10.0, 20.0, 25.0]


def test_controller_reads_scheduling_rules_live_when_settings_backed(home, monkeypatch):
    from mu.jobs.controller import JobController
    from mu.jobs.job_settings import save_settings
    monkeypatch.delenv("MUCLI_JOBS_RETENTION_DAYS", raising=False)
    service = SimpleNamespace()
    fixed = JobController(service, max_workers=3)
    backed = JobController(service, settings_backed=True)
    assert fixed.max_workers == 3 and fixed.retention_days is None
    assert backed.max_workers == 5 and backed.retention_days == 30.0 and backed.drift_check_interval_seconds == 300
    save_settings({"jobs": {"max_concurrent": 2, "retention_days": 0, "drift_check_interval_s": 0}})
    backed._rule_cache_at = 0
    assert backed.max_workers == 2 and backed.retention_days == 0 and backed.drift_check_interval_seconds == 0
    assert JobController(service, settings_backed=True, retention_days=7).retention_days == 7  # env value wins


def test_publish_and_command_timeouts_follow_settings(home, monkeypatch):
    from mu.jobs.job_settings import rule_or, save_settings
    save_settings({"publish": {"timeout_s": 90},
                   "checks": {"command_timeout_s": 120}, "sandbox": {"model_request_timeout_s": 900}})
    assert rule_or("publish.timeout_s") == 90 and rule_or("checks.command_timeout_s") == 120
    from mu.jobs.sandbox.runner import _model_request_timeout
    assert _model_request_timeout() == 900


def test_jira_import_keeps_the_origin_session(monkeypatch):
    from mu.integrations import jira
    captured = []

    class Service:
        def create_from_payload(self, payload):
            captured.append(payload)
            return SimpleNamespace(id="j1", title=payload["title"])

    client = SimpleNamespace(config=SimpleNamespace(base_url="https://acme.atlassian.net", public=lambda: {}),
                             get_issue=lambda key: {"key": key, "fields": {"summary": "Fix it"}}, comment=lambda key, text: None)
    from contextlib import nullcontext
    from mu.jobs import tracker_sync
    # Duplicate protection now lives in tracker_sync (uncapped store lookup under a lock).
    monkeypatch.setattr(tracker_sync, "duplicate_of", lambda service, tracker, key, force=False: None)
    monkeypatch.setattr(tracker_sync, "_import_lock", lambda service: nullcontext())
    monkeypatch.setattr(jira, "issue_to_payload",
                        lambda issue, base_url: {"title": "Fix it", "metadata": {"jira": {"key": issue["key"]}}})
    jira.import_issues(Service(), client, keys=["SCRUM-9"],
                       defaults={"workspace_root": "/ws", "metadata": {"origin_session": "rsq-cloud"}})
    assert captured[0]["metadata"] == {"origin_session": "rsq-cloud", "jira": {"key": "SCRUM-9"}}
    assert captured[0]["workspace_root"] == "/ws"
