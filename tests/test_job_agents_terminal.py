"""Terminal surfaces for agent harnesses: /job create --agent, /job agents [secret], and the /jobs
TUI new-job flow (agent per stage), job header agents line and agents overlay."""
import os
import stat
from types import SimpleNamespace

import pytest

from mu.commands import job as job_commands
from mu.jobs import harness
from mu.ui.jobs import content, views
from mu.ui.jobs.app import JobsApp
from mu.ui.jobs.data import JobsData
from mu.ui.jobs.primitives import to_plain


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    for key in harness.SECRET_KEYS:
        monkeypatch.delenv(key, raising=False)
    return tmp_path


class UI:
    def __init__(self):
        self.info, self.errors = [], []

    def show_info(self, text):
        self.info.append(text)

    def show_error(self, text):
        self.errors.append(text)


# ---------------------------------------------------------------- /job create --agent


def test_create_parses_agent_specs():
    one = job_commands._parse_create("Fix it --agent claude", {})
    assert one["execution"]["stage_agents"] == {s: {"agent": "claude"} for s in ("plan", "implement", "review")}
    per = job_commands._parse_create("Fix it --agent plan=claude,implement=codex:gpt-5.5 --agent review=mucli", {})
    assert per["title"] == "Fix it"
    assert per["execution"]["stage_agents"] == {"plan": {"agent": "claude"},
                                                "implement": {"agent": "codex", "model": "gpt-5.5"},
                                                "review": {"agent": "mucli"}}
    with pytest.raises(ValueError, match="--agent plan=gpt"):
        job_commands._parse_create("x --agent plan=gpt", {})
    with pytest.raises(ValueError, match="unknown agent stage"):
        job_commands._parse_create("x --agent checks=claude", {})


# ---------------------------------------------------------------- /job agents


def test_job_agents_status_and_secret_is_saved_0600_and_never_echoed(home):
    ui = UI()
    session = SimpleNamespace(ui=ui)
    secret = "sk-ant-oat01-SUPERSECRETVALUE1234"
    result = job_commands.job_cmd(session, f"agents secret CLAUDE_CODE_OAUTH_TOKEN {secret}")
    assert result.ok and result.message == "CLAUDE_CODE_OAUTH_TOKEN saved"
    path = home / "home" / "secrets.env"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert secret in path.read_text()
    status = job_commands.job_cmd(session, "agents")
    assert status.ok and "Agent harnesses" in status.message and "Claude Code" in status.message and "Codex" in status.message
    assert "CLAUDE_CODE_OAUTH_TOKEN secrets.env" in status.message
    everything = "\n".join(ui.info + ui.errors) + status.message + repr(status.data)
    assert secret not in everything and secret[:8] not in everything.replace("sk-ant-oat01", "")
    cleared = job_commands.job_cmd(session, "agents secret CLAUDE_CODE_OAUTH_TOKEN")
    assert cleared.ok and secret not in path.read_text()
    bad = job_commands.job_cmd(session, "agents secret NOT_A_KEY value")
    assert not bad.ok and "unknown secret" in bad.message


# ---------------------------------------------------------------- /jobs TUI


class Data(JobsData):
    """No job store; records dashboard creates."""

    def __init__(self, creator=None, agents=None):
        super().__init__(object(), creator=creator)
        self._agents = agents or {"defaults": {"plan": "mucli", "implement": "claude", "review": "mucli"},
                                  "claude": {"label": "Claude Code", "ready": True},
                                  "codex": {"label": "Codex", "ready": False}}

    def home(self):
        return {"needs_you": [], "running": [], "jobs": []}

    def status(self, home=None):
        return {}

    def agents(self):
        return dict(self._agents)


def _type(app, text):
    for ch in text:
        app.handle_key("space" if ch == " " else ch)


def test_new_job_prompts_for_agents_per_stage_with_settings_defaults():
    created = []
    app = JobsApp(Data(creator=lambda title, spec: created.append((title, spec)) or ""))
    app.refresh()
    assert ("n", "new job") in app.hints()
    app.handle_key("n")
    assert app.mode["kind"] == "prompt" and app.mode["label"] == "New job title"
    _type(app, "Fix login")
    app.handle_key("enter")
    assert app.mode["new_job"] == "agents"
    assert app.mode["text"] == "plan=mucli,implement=claude,review=mucli"
    assert "Claude Code ready" in app.mode["label"] and "Codex not set up" in app.mode["label"]
    text = to_plain(views.render(app, 120, 30))
    assert "Agent per stage" in text
    app.mode["text"] = "plan=claude,implement=codex:gpt-5.5,review=mucli"
    app.handle_key("enter")
    assert created == [("Fix login", "plan=claude,implement=codex:gpt-5.5,review=mucli")]


def test_new_job_rejects_bad_agent_spec_and_needs_a_session():
    created = []
    app = JobsApp(Data(creator=lambda title, spec: created.append(spec) or ""))
    app.refresh()
    app.handle_key("n")
    _type(app, "x")
    app.handle_key("enter")
    app.mode["text"] = "plan=gpt"
    app.handle_key("enter")
    assert not created and "unknown agent" in app.current_flash()[0]
    orphan = JobsApp(Data(creator=None))
    orphan.refresh()
    orphan.handle_key("n")
    assert orphan.mode is None and "from a session" in orphan.current_flash()[0]


def test_agents_overlay_via_g_a_and_palette(monkeypatch):
    app = JobsApp(Data(agents=harness.status()))
    app.refresh()
    app.handle_key("g")
    app.handle_key("a")
    assert app.overlay == "agents"
    text = to_plain(views.render(app, 140, 40))
    assert "Agents" in text and "Agent harnesses" in text and "Claude Code" in text
    app.handle_key("escape")
    assert app.overlay is None
    labels = [i["label"] for i in app.palette_items()]
    assert any(label.startswith("Agents:") for label in labels) and any(label.startswith("New job") for label in labels)


def test_job_header_shows_agent_per_stage():
    vm = {"stage_agents": {"plan": {"agent": "claude", "label": "Claude Code", "model": "claude-opus-5-5"},
                           "implement": {"agent": "codex", "label": "Codex", "model": ""},
                           "review": {"agent": "mucli", "label": "mucli", "model": ""}}}
    assert content.agents_line(vm) == "◆ agents · plan Claude Code claude-opus-5-5 · implement Codex · review mucli"
    assert content.agents_line({"stage_agents": {s: {"agent": "mucli", "label": "mucli"} for s in ("plan", "implement")}}) is None
    assert content.agents_line({}) is None


def test_job_screen_renders_agents_from_a_real_job(home, monkeypatch):
    from test_job_worker import running_job
    service, _, row, _, _ = running_job(home)
    execution = dict(row.execution or {})
    execution["stage_agents"] = {"plan": {"agent": "claude"}, "implement": {"agent": "codex", "model": "gpt-5.5"}}
    service.store.update_runtime_fields(row.id, execution_json=execution)
    app = JobsApp(JobsData(service))
    app.refresh()
    app.open_job(row.id)
    text = to_plain(views.render(app, 160, 34))
    assert "◆ agents · plan Claude Code claude-opus-5-5 · implement Codex gpt-5.5 · review mucli" in text
