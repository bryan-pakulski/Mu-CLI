"""Web GUI: jobs Setup checklist and Doctor pages (planos-style), and the setup/doctor REST API."""
import os
import stat
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SETUP_JS = (ROOT / "mu/gui/static/js/work/setup.js").read_text()
WORK = (ROOT / "mu/gui/static/js/work.js").read_text()
HTML = (ROOT / "mu/gui/templates/work.html").read_text()
CSS = (ROOT / "mu/gui/static/css/work.css").read_text()


def test_jobs_menu_has_one_setup_entry_with_a_doctor_tab():
    # One "Setup" nav entry; Doctor is a tab of the Setup page and #/doctor still routes there.
    assert 'href="#/setup"' in HTML and 'data-nav="setup"' in HTML
    assert 'data-nav="doctor"' not in HTML and 'data-route="doctor"' not in HTML
    assert 'data-route="setup"' in HTML and 'href="#/doctor"' in HTML
    assert 'data-setup-tab="checklist"' in HTML and 'data-setup-tab="doctor"' in HTML
    assert 'data-setup-panel="doctor"' in HTML and 'role="tablist"' in HTML
    assert "setup: 'Setup' }" in WORK and "doctor: 'Doctor'" not in WORK
    assert "if (path === '/doctor') { params.set('tab', 'doctor'); return { name: 'setup', params }; }" in WORK
    assert "params?.get?.('tab') === 'doctor'" in SETUP_JS
    assert "key: 'g u'" in WORK and "key: 'g d'" in WORK
    assert HTML.index("/static/js/work.js") < HTML.index("/static/js/work/setup.js")


def test_setup_page_renders_every_action_kind():
    for endpoint in ("/api/jobs/setup", "/api/jobs/setup/action", "/api/jobs/setup/recheck", "/api/jobs/setup/finish"):
        assert endpoint in SETUP_JS
    assert "a.kind === 'terminal'" in SETUP_JS and "data-copy" in SETUP_JS and "I ran it — check again" in SETUP_JS
    assert "a.kind === 'prompts'" in SETUP_JS and 'type="password" autocomplete="new-password"' in SETUP_JS
    assert "a.kind === 'nav'" in SETUP_JS and "window.confirm(a.confirm)" in SETUP_JS
    # Live log while a setup job runs (polled), and the first step needing attention is selected.
    assert "startPolling" in SETUP_JS and "work-setup-log" in SETUP_JS
    assert "s.state === 'todo' || s.state === 'warn'" in SETUP_JS
    for icon in ("'✓'", "'○'", "'!'", "'–'", "'·'", "'…'"):
        assert icon in SETUP_JS
    assert 'id="work-setup-finish"' in HTML and 'id="work-setup-recheck"' in HTML and 'id="work-setup-bar"' in HTML
    assert ".work-setup-log" in CSS


def test_secret_prompts_are_never_prefilled():
    # A secret prompt renders without a value attribute; only non-secret prompts use `initial`.
    assert "p.secret ? 'type=\"password\" autocomplete=\"new-password\"' : `type=\"text\" value=" in SETUP_JS


def test_doctor_page_and_dashboard_reminder():
    assert "/api/jobs/doctor${deep ? '?deep=1' : ''}" in SETUP_JS
    assert 'id="work-doctor-deep"' in HTML and 'id="work-doctor-run"' in HTML and 'id="work-doctor-badge"' in HTML
    assert 'href="#/setup"' in SETUP_JS  # failing checks link to Setup
    assert 'id="work-setup-cta"' in HTML and "data-cta-dismiss" in HTML and "mucli-jobs-setup-cta" in SETUP_JS
    assert 'id="work-setup-badge"' in HTML


def test_dashboard_shows_one_setup_call_to_action():
    # Missing stage models fold into the single setup banner instead of a second "Set job models" banner.
    stage = (ROOT / "mu/gui/static/js/work_stage_models.js").read_text()
    assert "Set job models" not in stage and "work-stage-models-banner" not in stage
    assert "mu:stage-models" in stage and "mu:stage-models" in SETUP_JS
    assert HTML.count('class="work-setup-cta"') == 1


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    from mu.jobs import setup
    # No docker/network: every step reports a fixed state.
    states = {"docker": ("ok", "Docker 29"), "runtime": ("todo", "not built"), "claude": ("todo", "no credentials")}
    monkeypatch.setattr(setup, "check", lambda step: setup.StepStatus(*states.get(step, ("ok", f"{step} fine"))))
    monkeypatch.setattr(setup, "_setup", None)
    from mu.gui.routers.jobs import router
    app = FastAPI()
    app.include_router(router, prefix="/api/jobs")
    return TestClient(app)


def test_setup_api_shapes_and_secret_handling(client, tmp_path):
    vm = client.get("/api/jobs/setup").json()
    assert {"steps", "done", "total", "blocking", "can_finish", "job", "completed_at"} <= set(vm)
    runtime = next(s for s in vm["steps"] if s["id"] == "runtime")
    assert runtime["state"] == "todo" and runtime["actions"][0]["kind"] == "job"
    assert "Job runtime image" in vm["blocking"] and not vm["can_finish"]
    claude = next(s for s in vm["steps"] if s["id"] == "claude")
    paste = next(a for a in claude["actions"] if a["id"] == "paste-token")
    assert paste["kind"] == "prompts" and paste["prompts"][0]["secret"]
    assert next(a for a in claude["actions"] if a["id"] == "setup-token")["command"] == "claude setup-token"

    saved = client.post("/api/jobs/setup/action", json={"step": "claude", "action": "paste-token",
                                                        "inputs": ["sk-ant-oat-very-secret-token"]})
    assert saved.status_code == 200 and "very-secret" not in saved.text and saved.json()["setup"]["steps"]
    secrets = tmp_path / "home" / "secrets.env"
    assert stat.S_IMODE(os.stat(secrets).st_mode) == 0o600

    assert client.post("/api/jobs/setup/action", json={"step": "claude", "action": "nope"}).status_code == 400
    assert client.post("/api/jobs/setup/action", json={"step": "claude", "action": "paste-token"}).status_code == 400
    assert client.post("/api/jobs/setup/finish").status_code == 400  # runtime still blocking
    rechecked = client.post("/api/jobs/setup/recheck", json={"steps": ["docker"]}).json()
    assert next(s for s in rechecked["steps"] if s["id"] == "docker")["state"] == "ok"


def test_doctor_api_shape(client, monkeypatch):
    from mu.jobs import setup
    monkeypatch.setattr(setup, "doctor", lambda deep=False, log=None: [
        {"name": "docker", "status": "ok", "detail": "Docker 29"},
        {"name": "runtime_image", "status": "fail", "detail": "not built" + (" (deep)" if deep else "")}])
    data = client.get("/api/jobs/doctor").json()
    assert data["failed"] == 1 and data["warnings"] == 0 and data["needs_setup"] is True
    assert [c["name"] for c in data["checks"]] == ["docker", "runtime_image"]
    assert client.get("/api/jobs/doctor?deep=1").json()["checks"][1]["detail"].endswith("(deep)")
