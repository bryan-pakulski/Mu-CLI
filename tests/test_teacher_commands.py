"""Tests for /teach slash commands.

Happy paths for new, list, load, exit, status, next, delete. The
underlying engine logic is exhaustively tested in test_teacher_engine.py;
these tests just ensure the slash-command surface dispatches correctly
and produces sensible CommandResult shapes.
"""

from __future__ import annotations

import os
import importlib
import pytest

from types import SimpleNamespace


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Make tmp_path the active workspace for course storage, AND
    isolate HISTORY_DIR so SessionManager doesn't leak teacher_state
    between tests via the shared session.json on disk."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("utils.config.HISTORY_DIR", str(tmp_path / "_mucli_home"))
    yield tmp_path


@pytest.fixture
def session(isolated, tmp_path):
    from mu.session.manager import SessionManager
    from mu.workspace.folder_context import FolderContext

    sm = SessionManager()
    folder_context = FolderContext()
    folder_context.folders = [str(tmp_path)]
    sess = SimpleNamespace(
        session_manager=sm,
        folder_context=folder_context,
        ui=None,
        variables={},
    )
    return sess


def _teach(session, args: str):
    from mu.commands.teach import teach_cmd

    return teach_cmd(session, args)


def test_teach_help(session):
    res = _teach(session, "help")
    assert res.ok
    assert "/teach new" in res.message


def test_teach_new_creates_course_record(session):
    res = _teach(session, "new Perl")
    assert res.ok
    assert "Created course" in res.message
    courses = session.session_manager.list_courses()
    assert len(courses) == 1
    assert courses[0]["subject"] == "Perl"


def test_teach_list_then_load(session):
    _teach(session, "new Perl")
    listed = _teach(session, "list")
    assert listed.ok
    assert "perl" in listed.message.lower()

    # Force-clear active state to simulate a fresh boot
    session.session_manager.clear_teacher_state()
    assert session.session_manager.active_course_id is None

    loaded = _teach(session, "load perl")
    assert loaded.ok
    assert session.session_manager.active_course_id == "perl"


def test_teach_load_unknown_id_fails(session):
    res = _teach(session, "load nonexistent")
    assert not res.ok


def test_teach_status_with_no_course_is_friendly(session):
    res = _teach(session, "status")
    assert res.ok
    assert "No active course" in res.message


def test_teach_status_after_new(session):
    _teach(session, "new Kubernetes")
    res = _teach(session, "status")
    assert res.ok
    assert "kubernetes" in res.message.lower() or "Kubernetes" in res.message


def test_teach_exit_clears_active(session):
    _teach(session, "new Perl")
    assert session.session_manager.active_course_id == "perl"
    _teach(session, "exit")
    assert session.session_manager.active_course_id is None


def test_teach_delete_removes_directory_and_registry_entry(session):
    res = _teach(session, "new Perl")
    course_directory = res.data["course"]["directory"]
    assert os.path.isdir(course_directory)
    delete_res = _teach(session, "delete perl")
    assert delete_res.ok
    assert not os.path.isdir(course_directory)
    assert session.session_manager.get_course("perl") is None


def test_unknown_subcommand_returns_not_ok(session):
    res = _teach(session, "wat")
    assert not res.ok


def test_teach_status_prints_to_terminal(session):
    """The REPL doesn't auto-echo CommandResult.message — each subcommand
    must surface its text via the UI. Regression guard so /teach status,
    /teach list, etc. never go silent again."""
    printed: list[str] = []

    class FakeConsole:
        def print(self, *args, **kwargs):
            printed.append(" ".join(str(a) for a in args))

    class FakeUI:
        console = FakeConsole()

        def show_info(self, msg):
            printed.append(str(msg))

    _teach(session, "new Perl")
    printed.clear()
    res = _teach(session, "status")
    assert res.ok
    # No UI attached → nothing printed (fine, smoke check below covers UI path).
    assert printed == []

    session.ui = FakeUI()
    res = _teach(session, "status")
    assert res.ok
    assert printed, "/teach status should print to the terminal"
    assert "perl" in printed[0].lower()
