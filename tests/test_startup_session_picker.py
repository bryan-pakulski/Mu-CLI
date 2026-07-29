"""Startup control-centre flow for terminal users."""

import os
from pathlib import Path

import pytest

import mucli
from mu.session.session import SessionManager


@pytest.fixture
def session_manager(tmp_path, monkeypatch):
    (tmp_path / "sessions").mkdir()
    monkeypatch.setattr("utils.config.HISTORY_DIR", str(tmp_path))
    return SessionManager()


def _make_session_dir(session_manager, name: str) -> None:
    path = Path(session_manager._get_session_dir(name))
    path.mkdir(parents=True, exist_ok=True)
    (path / "session.json").write_text("{}", encoding="utf-8")


def _prompt_sequence(monkeypatch, *answers):
    values = iter(answers)
    monkeypatch.setattr("mucli.Prompt.ask", lambda *args, **kwargs: next(values))


def test_create_flow_records_type_when_no_sessions(session_manager, monkeypatch):
    _prompt_sequence(monkeypatch, "create", "", "workspace")

    action, name = mucli.choose_session(session_manager)

    assert (action, name) == ("new", None)
    assert session_manager._startup_session_type == "workspace"


def test_create_container_flow_with_explicit_name(session_manager, monkeypatch):
    _prompt_sequence(monkeypatch, "create", "container-work", "container")

    action, name = mucli.choose_session(session_manager)

    assert (action, name) == ("new", "container-work")
    assert session_manager._startup_session_type == "container"


def test_sessions_flow_loads_selected_session(session_manager, monkeypatch):
    _make_session_dir(session_manager, "alpha")
    _make_session_dir(session_manager, "beta")
    _prompt_sequence(monkeypatch, "sessions", "load")
    monkeypatch.setattr("mucli.IntPrompt.ask", lambda *args, **kwargs: 1)

    action, name = mucli.choose_session(session_manager)

    assert action == "load"
    assert name in {"alpha", "beta"}


def test_sessions_flow_can_delete_then_create(session_manager, monkeypatch):
    _make_session_dir(session_manager, "alpha")
    _prompt_sequence(
        monkeypatch,
        "sessions", "delete",
        "create", "fresh", "chat",
    )
    monkeypatch.setattr("mucli.IntPrompt.ask", lambda *args, **kwargs: 1)
    monkeypatch.setattr("mucli.Confirm.ask", lambda *args, **kwargs: True)

    action, name = mucli.choose_session(session_manager)

    assert session_manager.get_session_list() == []
    assert (action, name) == ("new", "fresh")
    assert session_manager._startup_session_type == "chat"


def test_container_manager_returns_to_launcher(session_manager, monkeypatch):
    calls = []
    _prompt_sequence(monkeypatch, "containers", "create", "", "workspace")
    monkeypatch.setattr(
        "mu.container.tui.run_container_manager",
        lambda: calls.append("opened"),
    )

    action, name = mucli.choose_session(session_manager)

    assert calls == ["opened"]
    assert (action, name) == ("new", None)


def test_quit_exits_cleanly(session_manager, monkeypatch):
    _prompt_sequence(monkeypatch, "quit")
    with pytest.raises(SystemExit) as exc:
        mucli.choose_session(session_manager)
    assert exc.value.code == 0


def test_safe_delete_session_silent_mode_detaches_ui(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.config.HISTORY_DIR", str(tmp_path))
    session_path = tmp_path / "sessions" / "alpha"
    session_path.mkdir(parents=True)
    (session_path / "session.json").write_text("{}", encoding="utf-8")

    class SpyUI:
        def __init__(self):
            self.calls = []

        def show_info(self, body):
            self.calls.append(("info", body))

        def show_error(self, body):
            self.calls.append(("error", body))

    manager = SessionManager()
    manager.ui = SpyUI()
    original_ui = manager.ui

    mucli._safe_delete_session(manager, "alpha", silent=True)

    assert original_ui.calls == []
    assert manager.ui is original_ui
