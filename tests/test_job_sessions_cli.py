"""Jobs span every session: `/job sessions`, `/job create --session`, origin-session
display in `/job list`/board and the /jobs TUI, plus the TUI session filter (`s`)."""

from __future__ import annotations

import json
import os
import time
from types import SimpleNamespace

import pytest

from mu.commands import job as terminal
from mu.jobs import JobService, JobStore
from mu.ui.jobs import views
from mu.ui.jobs.app import JobsApp
from mu.ui.jobs.primitives import line_width, to_plain


@pytest.fixture
def home(tmp_path, monkeypatch):
    from utils import config

    monkeypatch.setattr(config, "HISTORY_DIR", str(tmp_path / "home"))
    return tmp_path / "home"


@pytest.fixture
def service(tmp_path, monkeypatch):
    svc = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    monkeypatch.setattr(terminal, "get_default_job_service", lambda: svc)
    monkeypatch.setattr(terminal, "ensure_controller_daemon",
                        lambda: SimpleNamespace(running=True, detail="controller up"))
    return svc


def _save_session(home, name, *, workspace="", provider="anthropic", model="claude-x",
                  variables=None, mtime=None):
    folder = home / "sessions" / name
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "session.json"
    path.write_text(json.dumps({
        "provider_config": {"provider": provider, "model": model},
        "folder_context": {"folders": [workspace] if workspace else []},
        "variables": dict(variables or {}),
        "history": [],
    }))
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


class UI:
    def __init__(self):
        self.info, self.errors = [], []

    def show_info(self, text):
        self.info.append(text)

    def show_error(self, text):
        self.errors.append(text)


def _cli_session(current=None, *, workspace="", provider="ollama", model="live-model", variables=None):
    return SimpleNamespace(
        ui=UI(),
        session_manager=SimpleNamespace(current_session_name=current),
        provider=SimpleNamespace(name=provider, model_name=model),
        variables=dict(variables or {}),
        folder_context=SimpleNamespace(folders=[workspace] if workspace else []),
    )


# ------------------------------------------------------------------ /job sessions

def test_job_sessions_lists_saved_user_sessions_current_first(home, tmp_path):
    now = time.time()
    _save_session(home, "older", workspace=str(tmp_path), mtime=now - 100)
    _save_session(home, "newer", workspace="/w/newer", mtime=now - 10)
    _save_session(home, "mine", workspace="/w/mine", mtime=now - 1000, variables={"agent_mode": "plan", "yolo": True})
    _save_session(home, "job-0123456789abcdef", workspace="/w/worker")  # job worker: hidden
    (home / "sessions" / "no-file").mkdir(parents=True)  # no session.json: hidden

    session = _cli_session("mine")
    result = terminal.job_cmd(session, "sessions")

    assert result.ok
    assert result.data["current"] == "mine"
    names = [s["name"] for s in result.data["sessions"]]
    assert names == ["mine", "newer", "older"]
    mine = result.data["sessions"][0]
    assert mine["current"] and mine["workspace"] == "/w/mine"
    assert mine["agent_mode"] == "plan" and mine["yolo"] is True
    assert mine["provider"] == "anthropic" and mine["model"] == "claude-x"
    text = session.ui.info[-1]
    assert "* mine" in text and "job-0123456789abcdef" not in text
    assert all(len(line) <= 80 for line in text.splitlines())


def test_job_sessions_without_saved_sessions(home):
    result = terminal.job_cmd(_cli_session(None), "sessions")
    assert result.ok and result.data == {"current": None, "sessions": []}


# ------------------------------------------------------------------ /job create --session

def test_create_with_session_uses_saved_session_and_records_origin(home, service, tmp_path):
    ws = tmp_path / "ws-other"
    ws.mkdir()
    _save_session(home, "other", workspace=str(ws), provider="anthropic", model="claude-x",
                  variables={"agent_mode": "plan", "yolo": True})
    session = _cli_session("current", workspace=str(tmp_path))

    result = terminal.job_cmd(session, 'create "Fix login" --session other --accept "works"')

    assert result.ok, result.message
    job = service.get(result.data["job"]["id"])
    assert job.title == "Fix login"
    assert job.repository == str(ws)
    assert job.metadata["origin_session"] == "other"
    assert job.execution["agent_mode"] == "plan"
    # Session YOLO is never inherited: unattended sandbox writes are an explicit choice.
    assert job.execution["auto_approve_writes"] is False
    assert job.execution["stages"]["plan"] == {"provider": "anthropic", "model": "claude-x"}


def test_create_defaults_to_current_cli_session_live_state(home, service, tmp_path):
    ws = tmp_path / "live"
    ws.mkdir()
    session = _cli_session("current", workspace=str(ws), provider="ollama", model="live-model")

    result = terminal.job_cmd(session, "create Ship it --session=current")
    assert result.ok, result.message
    job = service.get(result.data["job"]["id"])
    assert job.metadata["origin_session"] == "current"
    assert job.repository == str(ws)

    result = terminal.job_cmd(session, "create Ship it again")
    assert result.ok, result.message
    assert service.get(result.data["job"]["id"]).metadata["origin_session"] == "current"


def test_create_without_current_session_requires_session_flag(home, service):
    session = _cli_session(None)
    result = terminal.job_cmd(session, "create Orphan job")
    assert not result.ok
    assert "--session" in result.message and "/job sessions" in result.message
    assert service.list() == []


@pytest.mark.parametrize("name", ["missing", "job-0123456789abcdef", "../escape"])
def test_create_with_unknown_or_hidden_session_is_rejected(home, service, name):
    _save_session(home, "job-0123456789abcdef", workspace="/w")
    result = terminal.job_cmd(_cli_session("current"), f"create Nope --session {name}")
    assert not result.ok
    assert "does not exist" in result.message
    assert service.list() == []


def test_create_session_flag_needs_a_value(home, service):
    result = terminal.job_cmd(_cli_session("current"), "create Nope --session")
    assert not result.ok and "--session" in result.message


# ------------------------------------------------------------------ text surfaces

def _job_with_origin(service, tmp_path, title, origin):
    ws = tmp_path / f"ws-{abs(hash(title))}"
    ws.mkdir(exist_ok=True)
    payload = {"title": title, "workspace_root": str(ws), "execution": {"session_type": "workspace"}}
    if origin:
        payload["metadata"] = {"origin_session": origin}
    return service.create_from_payload(payload)


def test_board_and_list_show_origin_session(home, service, tmp_path):
    _job_with_origin(service, tmp_path, "Alpha", "alpha-session")
    _job_with_origin(service, tmp_path, "Beta", "a-really-long-session-name-for-truncation")
    _job_with_origin(service, tmp_path, "Gamma", "")

    session = _cli_session("x")
    board = terminal.job_cmd(session, "board")
    text = session.ui.info[-1]
    assert "all sessions" in text.splitlines()[0]
    assert "alpha-session" in text
    assert "a-really-long-s…" in text
    assert {row["origin_session"] for row in board.data["jobs"]} == {
        "alpha-session", "a-really-long-session-name-for-truncation", ""}

    terminal.job_cmd(session, "list queued")
    listing = session.ui.info[-1]
    assert "SESSION" in listing
    assert "alpha-session" in listing and "a-really-long…" in listing


# ------------------------------------------------------------------ TUI

def _row(job_id, title, origin, *, state="queued"):
    return {"id": job_id, "key": job_id[:6].upper(), "title": title, "state": state, "steps": [],
            "cost_usd": 0.0, "updated_at": time.time(), "origin_session": origin, "actions": [],
            "last": "working", "attention_detail": "Need an answer" if state == "needs_input" else ""}


class FakeData:
    def __init__(self, home_vm):
        self.vm = home_vm

    def home(self):
        return json.loads(json.dumps(self.vm))

    def status(self, home=None):
        return {"agents": 0, "checks": 0, "needs_you": len(self.vm["needs_you"]), "engine": ""}


def _tui():
    needs = [_row("aaaaaaaaaaaa", "Needs a decision", "alpha", state="needs_input")]
    jobs = [
        *needs,
        _row("bbbbbbbbbbbb", "Beta work", "beta"),
        _row("cccccccccccc", "Alpha work", "alpha"),
        _row("dddddddddddd", "Untracked", ""),
    ]
    app = JobsApp(FakeData({"needs_you": needs, "running": [], "jobs": jobs}))
    app.refresh()
    return app


def _screen(app, width=120, height=24):
    lines = views.render(app, width, height)
    assert all(line_width(l) <= width for l in lines)
    return to_plain(lines)


@pytest.mark.parametrize("width", [80, 120])
def test_tui_dashboard_shows_session_column_and_scope(width):
    app = _tui()
    text = _screen(app, width)
    assert "all sessions" in text.splitlines()[0]
    assert "SESSION" in text
    assert "beta" in text and "alpha" in text
    assert "all sessions" in text.splitlines()[-1]


def test_tui_s_cycles_session_filter_across_needs_and_jobs():
    app = _tui()
    assert app.session_names() == ["alpha", "beta", ""]
    assert ("s", "all sessions") in app.hints()

    app.handle_key("s")
    assert app.session_filter == "alpha"
    assert [r["id"][:1] for r in app.home_rows("jobs")] == ["a", "c"]
    assert len(app.home_rows("needs")) == 1
    assert ("s", "session alpha") in app.hints()
    text = _screen(app)
    assert "session alpha" in text.splitlines()[0]
    assert "Beta work" not in text and "Alpha work" in text

    app.handle_key("s")
    assert app.session_filter == "beta"
    assert [r["title"] for r in app.home_rows("jobs")] == ["Beta work"]
    assert app.home_rows("needs") == [] and app.region == "jobs"

    app.handle_key("s")
    assert app.session_filter == "" and [r["title"] for r in app.home_rows("jobs")] == ["Untracked"]
    assert "no session" in _screen(app)

    app.handle_key("s")
    assert app.session_filter is None and len(app.home_rows("jobs")) == 4


def test_tui_g_s_chord_still_opens_settings_not_filter():
    app = _tui()
    app.handle_key("g")
    app.handle_key("s")
    assert app.route["name"] == "settings"
    assert app.session_filter is None


def test_tui_filter_resets_when_session_disappears():
    app = _tui()
    app.handle_key("s")
    assert app.session_filter == "alpha"
    app.data.vm = {"needs_you": [], "running": [], "jobs": [_row("bbbbbbbbbbbb", "Beta work", "beta")]}
    app.refresh()
    assert app.session_filter is None


def test_tui_palette_offers_session_filters():
    app = _tui()
    labels = [i["label"] for i in app.palette_items() if i["group"] == "session"]
    assert labels == ["Show all sessions", "Show session alpha", "Show session beta", "Show no session"]


def test_job_completer_offers_sessions_subcommand():
    from mu.ui import input as ui_input

    source = open(ui_input.__file__, encoding="utf-8").read()
    start = source.index("_JOB_SUBCOMMAND_COMPLETER = NestedCompleter.from_nested_dict(")
    block = source[start:source.index("teach_completer", start)]
    assert '"sessions": None' in block and '"create": None' in block
