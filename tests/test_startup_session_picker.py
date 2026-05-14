"""Pin the startup session picker — load / new / delete flows.

The picker is interactive (IntPrompt + Confirm). Tests stub those
prompt classes so each branch is exercised deterministically.
"""

import os
from pathlib import Path
from typing import List

import pytest

import mucli
from core.session import SessionManager


@pytest.fixture
def session_manager(tmp_path, monkeypatch):
    """Sandboxed SessionManager rooted at a per-test temp dir so the
    real `~/.mucli/sessions/` stays out of these tests. SessionManager
    reads `core.session.HISTORY_DIR` (imported once at module load) so
    we monkeypatch that single name."""
    (tmp_path / "sessions").mkdir()
    monkeypatch.setattr("core.session.HISTORY_DIR", str(tmp_path))
    return SessionManager()


def _make_session_dir(session_manager, name: str) -> None:
    """Create a minimal on-disk session so `get_session_list()` sees it."""
    sm_dir = session_manager._get_session_dir(name)
    os.makedirs(sm_dir, exist_ok=True)
    Path(sm_dir, "session.json").write_text("{}", encoding="utf-8")


# ----------------------------------------------- happy paths


def test_choose_session_returns_new_when_no_sessions_exist(session_manager):
    """First-ever launch — nothing to list."""
    action, name = mucli.choose_session(session_manager)
    assert action == "new"
    assert name is None


def test_choose_session_load_existing(session_manager, monkeypatch):
    _make_session_dir(session_manager, "alpha")
    _make_session_dir(session_manager, "beta")

    # User picks #1 (alpha).
    monkeypatch.setattr("mucli.IntPrompt.ask", lambda *a, **kw: 1)
    action, name = mucli.choose_session(session_manager)
    assert action == "load"
    assert name in ("alpha", "beta")  # sort order isn't pinned, but it IS one of them


def test_choose_session_new_with_explicit_name(session_manager, monkeypatch):
    _make_session_dir(session_manager, "alpha")
    # Two sessions in the list means [New] is option 2 (no — 1 session + new + delete = pick 2).
    # We craft prompts to land on [New Session] then provide a name.
    new_idx = len(session_manager.get_session_list()) + 1
    monkeypatch.setattr("mucli.IntPrompt.ask", lambda *a, **kw: new_idx)
    monkeypatch.setattr(
        "rich.prompt.Prompt.ask",
        lambda *a, **kw: "fresh-session",
    )
    action, name = mucli.choose_session(session_manager)
    assert action == "new"
    assert name == "fresh-session"


def test_choose_session_new_with_empty_name_returns_none(session_manager, monkeypatch):
    _make_session_dir(session_manager, "alpha")
    new_idx = len(session_manager.get_session_list()) + 1
    monkeypatch.setattr("mucli.IntPrompt.ask", lambda *a, **kw: new_idx)
    monkeypatch.setattr("rich.prompt.Prompt.ask", lambda *a, **kw: "")
    action, name = mucli.choose_session(session_manager)
    assert action == "new"
    assert name is None


# ----------------------------------------------- delete flow


def test_delete_flow_with_confirmation(session_manager, monkeypatch):
    _make_session_dir(session_manager, "alpha")
    _make_session_dir(session_manager, "beta")
    _make_session_dir(session_manager, "gamma")

    sessions_before = session_manager.get_session_list()
    delete_menu_idx = len(sessions_before) + 2  # [Delete a session…]

    # Sequence of IntPrompt calls:
    #   1. Outer menu → user picks "Delete a session…"
    #   2. Inner delete menu → pick #1 (some session, depends on listing order)
    #   3. Outer menu re-renders → user picks #1 (load the remaining session)
    calls = iter([delete_menu_idx, 1, 1])
    monkeypatch.setattr("mucli.IntPrompt.ask", lambda *a, **kw: next(calls))
    # Confirm.ask returns True → deletion proceeds.
    monkeypatch.setattr("rich.prompt.Confirm.ask", lambda *a, **kw: True)

    target = sessions_before[0]
    action, name = mucli.choose_session(session_manager)

    sessions_after = session_manager.get_session_list()
    assert target not in sessions_after
    assert len(sessions_after) == len(sessions_before) - 1
    # The re-rendered menu's #1 returned a load action.
    assert action == "load"
    assert name in sessions_after


def test_delete_flow_cancelled_at_confirmation(session_manager, monkeypatch):
    _make_session_dir(session_manager, "alpha")
    _make_session_dir(session_manager, "beta")
    sessions_before = session_manager.get_session_list()
    delete_menu_idx = len(sessions_before) + 2

    # Outer → Delete. Inner → pick #1. Confirm → No. Outer redraws → pick #1 to load.
    calls = iter([delete_menu_idx, 1, 1])
    monkeypatch.setattr("mucli.IntPrompt.ask", lambda *a, **kw: next(calls))
    monkeypatch.setattr("rich.prompt.Confirm.ask", lambda *a, **kw: False)

    action, name = mucli.choose_session(session_manager)
    # Nothing was deleted.
    assert session_manager.get_session_list() == sessions_before
    assert action == "load"


def test_delete_flow_cancelled_in_picker(session_manager, monkeypatch):
    """User picks Delete, then picks [Cancel] in the inner menu."""
    _make_session_dir(session_manager, "alpha")
    _make_session_dir(session_manager, "beta")
    sessions_before = session_manager.get_session_list()
    delete_menu_idx = len(sessions_before) + 2
    cancel_idx = len(sessions_before) + 1  # last option in the inner menu

    # Outer → Delete. Inner → Cancel. Outer redraws → pick #1 to load.
    calls = iter([delete_menu_idx, cancel_idx, 1])
    monkeypatch.setattr("mucli.IntPrompt.ask", lambda *a, **kw: next(calls))
    # Confirm.ask should NOT be called when cancelling in the picker —
    # raise if it is, to catch a regression.
    monkeypatch.setattr(
        "rich.prompt.Confirm.ask",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("Confirm called on cancel")),
    )

    action, name = mucli.choose_session(session_manager)
    assert session_manager.get_session_list() == sessions_before
    assert action == "load"


def test_delete_then_no_sessions_left_returns_new(session_manager, monkeypatch):
    """If the user deletes their only session, the picker should fall
    back to `new` (the empty-list branch)."""
    _make_session_dir(session_manager, "lonely")
    delete_menu_idx = 1 + 2  # 1 session + [New]=2 + [Delete]=3

    # Outer → Delete. Inner → pick #1 (lonely). Confirm yes. Then the
    # outer redraws on empty list, falls into the early-return `new`.
    calls = iter([delete_menu_idx, 1])
    monkeypatch.setattr("mucli.IntPrompt.ask", lambda *a, **kw: next(calls))
    monkeypatch.setattr("rich.prompt.Confirm.ask", lambda *a, **kw: True)

    action, name = mucli.choose_session(session_manager)
    assert action == "new"
    assert name is None
    assert session_manager.get_session_list() == []


def test_delete_works_even_for_bootstrap_default_active(session_manager, monkeypatch):
    """`delete_session` normally refuses the active session, but at
    startup `current_session_name` is just the bootstrap placeholder
    'default' — the user hasn't actually loaded anything. The picker
    bypasses the guard so users aren't stuck with an orphan 'default'."""
    _make_session_dir(session_manager, "default")
    assert session_manager.current_session_name == "default"
    delete_menu_idx = 1 + 2

    calls = iter([delete_menu_idx, 1])
    monkeypatch.setattr("mucli.IntPrompt.ask", lambda *a, **kw: next(calls))
    monkeypatch.setattr("rich.prompt.Confirm.ask", lambda *a, **kw: True)

    action, name = mucli.choose_session(session_manager)
    assert action == "new"
    assert "default" not in session_manager.get_session_list()


# ----------------------------------------------- delete helper directly


def test_delete_session_flow_helper_noop_on_empty_list(session_manager, monkeypatch):
    """`_delete_session_flow` with no sessions returns without prompting."""
    monkeypatch.setattr(
        "mucli.IntPrompt.ask",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not prompt")),
    )
    mucli._delete_session_flow(session_manager, [])  # no exception, no prompt
