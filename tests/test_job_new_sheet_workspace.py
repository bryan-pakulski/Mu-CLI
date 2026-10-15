"""New job sheet backend contract: workspace-first creation (no session required) and the explicit
"Run unattended in sandbox" flag (Settings default off, never inherited from session YOLO)."""
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path))
    from utils import config
    monkeypatch.setattr(config, "HISTORY_DIR", str(tmp_path))
    return tmp_path


def save_session(home, name, workspace, provider="anthropic", model="claude-x", **variables):
    path = home / "sessions" / name
    path.mkdir(parents=True)
    workspace.mkdir(parents=True, exist_ok=True)
    (path / "session.json").write_text(json.dumps({
        "revision": 1, "variables": {"agent_mode": "feature", "yolo": True, **variables},
        "provider_config": {"provider": provider, "model": model},
        "folder_context": {"folders": [str(workspace)]}, "history": []}))


def client(home):
    from mu.gui.routers.jobs import router
    from mu.jobs import JobService, JobStore
    app = FastAPI()
    app.state.job_service = JobService(JobStore(str(home / "jobs.db")))
    app.state.sessions = {}
    app.state.current_session_name = None
    app.state.session_by_name = lambda name=None: app.state.sessions.get(name)
    app.include_router(router, prefix="/api/jobs")
    return TestClient(app), app


def test_workspace_only_reuses_the_session_on_that_workspace(home):
    save_session(home, "rsq", home / "ws" / "rsq")
    c, app = client(home)
    res = c.post("/api/jobs", json={"title": "Fix", "workspace_root": str(home / "ws" / "rsq")})
    assert res.status_code == 200, res.text
    stored = app.state.job_service.get(res.json()["job"]["id"])
    assert stored.metadata["origin_session"] == "rsq"
    assert stored.execution["stages"]["plan"] == {"provider": "anthropic", "model": "claude-x"}
    # Session YOLO is on but never inherited.
    assert stored.execution["auto_approve_writes"] is False


def test_workspace_without_any_saved_session_is_not_a_dead_end(home):
    ws = home / "ws" / "fresh"
    ws.mkdir(parents=True)
    c, app = client(home)
    res = c.post("/api/jobs", json={"title": "Fix", "workspace_root": str(ws)})
    assert res.status_code == 200, res.text
    stored = app.state.job_service.get(res.json()["job"]["id"])
    assert "origin_session" not in (stored.metadata or {})
    assert stored.execution["auto_approve_writes"] is False


def test_run_unattended_follows_the_settings_default_and_the_explicit_flag(home):
    from mu.jobs.job_settings import FIELD_BY_PATH, save_settings
    assert FIELD_BY_PATH["defaults.run_unattended"]["default"] is False
    save_session(home, "rsq", home / "ws" / "rsq", yolo=False)
    c, app = client(home)
    save_settings({"defaults": {"run_unattended": True}})
    on = c.post("/api/jobs", json={"title": "A", "session_name": "rsq"}).json()["job"]
    assert app.state.job_service.get(on["id"]).execution["auto_approve_writes"] is True
    off = c.post("/api/jobs", json={"title": "B", "session_name": "rsq",
                                    "execution": {"auto_approve_writes": False}}).json()["job"]
    assert app.state.job_service.get(off["id"]).execution["auto_approve_writes"] is False


def test_terminal_session_defaults_do_not_inherit_yolo(home):
    from mu.jobs.core.vm import session_defaults
    out = session_defaults(workspace="/w", provider="p", model="m", variables={"yolo": True})
    assert out["execution"]["auto_approve_writes"] is False


def test_web_sheet_is_workspace_first_with_one_title_or_key_field():
    html = (ROOT / "mu/gui/templates/work.html").read_text()
    work = (ROOT / "mu/gui/static/js/work.js").read_text()
    sheet = html[html.index('id="work-create-form"'):html.index('</form>', html.index('id="work-create-form"'))]
    assert sheet.index('id="job-workspace"') < sheet.index('id="job-title"')
    assert "Title or ticket key" in sheet and 'id="job-session"' not in sheet
    assert "Run unattended in sandbox" in sheet and 'id="job-auto-approve"' in sheet
    # Models, retries and budgets sit under one Advanced section with one picker. The build
    # environment is the visible "Run in" picker (job_templates.md §3.2: it replaces the old
    # Build environments summary row; the multi-environment editor is its Advanced… action).
    advanced = sheet[sheet.index('id="work-advanced-details"'):]
    for field in ('id="job-agents"', 'id="job-retries"', 'id="job-cost"'):
        assert field in advanced, field
    assert sheet.index('id="job-workspace"') < sheet.index('id="job-environments-field"') < sheet.index('id="work-advanced-details"')
    assert 'id="job-run-in"' in sheet and ">Run in<" in sheet and ">Advanced…<" in sheet
    for gone in ('id="job-provider"', 'id="job-model"', 'id="work-stage-details"', 'id="work-execution-details"'):
        assert gone not in html, gone
    assert "/^[A-Z][A-Z0-9_]+-\\d+$/" in work and "'/api/jobs/jira/import'" in work
    assert "'defaults.run_unattended'" in work
    assert "verification-required human gate" not in html
