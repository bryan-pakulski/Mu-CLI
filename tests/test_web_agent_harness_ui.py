"""Web GUI: agent per stage on the New job sheet, the Agents settings card and the job page."""
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
AGENTS = (ROOT / "mu/gui/static/js/work/agents.js").read_text()
WORK = (ROOT / "mu/gui/static/js/work.js").read_text()
RULES = (ROOT / "mu/gui/static/js/work/rules.js").read_text()
VIEW = (ROOT / "mu/gui/static/js/work/view.js").read_text()
HTML = (ROOT / "mu/gui/templates/work.html").read_text()


def test_new_job_sheet_asks_for_an_agent_per_stage():
    assert 'id="job-agents"' in HTML and "Agent per stage" in HTML
    assert HTML.index("/static/js/work/agents.js") < HTML.index("/static/js/work.js")
    assert "['mucli', 'mucli'], ['claude', 'Claude Code'], ['codex', 'Codex']" in AGENTS
    assert "data-agent-stage" in AGENTS and "data-agent-model" in AGENTS
    assert "data?.defaults?.[stage]" in AGENTS and "(not set up)" in AGENTS and "info.problems?.[0]" in AGENTS
    # Jobs run unattended: Codex implement is never gated, so the sheet has no gated-Codex warning
    # and no auto-approve dependency.
    assert "data-codex-warning" not in AGENTS and "autoApprove" not in AGENTS
    assert "execution.stage_agents = stageAgents" in WORK and "agentPicker?.collect()" in WORK
    # Only sent when an external agent is chosen or the user changed the picker.
    assert "return touched || external ? out : null;" in AGENTS


def test_settings_card_shows_status_and_saves_the_token_without_echoing_it():
    assert "section.id === 'harness'" in RULES and "MuJobAgents?.card" in RULES
    assert "'/api/jobs/agents'" in AGENTS and "'/api/jobs/agents/secret'" in AGENTS
    assert 'type="password"' in AGENTS and "key: 'CLAUDE_CODE_OAUTH_TOKEN'" in AGENTS
    assert "input.value = ''" in AGENTS and "data-secret-clear" in AGENTS


def test_job_page_shows_the_agent_per_stage():
    assert "MuJobAgents.stageText(job)" in VIEW
    assert "job.stage_agents" in AGENTS and "data-stage-agent" in AGENTS


def test_agents_api_never_returns_secret_values(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    from fastapi import FastAPI
    from mu.gui.routers.jobs import router
    app = FastAPI()
    app.include_router(router, prefix="/api/jobs")
    client = TestClient(app)
    data = client.put("/api/jobs/agents/secret", json={"key": "CLAUDE_CODE_OAUTH_TOKEN", "value": "sk-ant-oat-secret-value"}).json()
    assert data["secrets"]["CLAUDE_CODE_OAUTH_TOKEN"] == "secrets.env"
    assert "sk-ant-oat-secret-value" not in client.get("/api/jobs/agents").text
    assert (tmp_path / "secrets.env").stat().st_mode & 0o777 == 0o600
    assert client.put("/api/jobs/agents/secret", json={"key": "PATH", "value": "x"}).status_code == 400
    cleared = client.put("/api/jobs/agents/secret", json={"key": "CLAUDE_CODE_OAUTH_TOKEN", "value": ""}).json()
    assert cleared["secrets"]["CLAUDE_CODE_OAUTH_TOKEN"] == ""
