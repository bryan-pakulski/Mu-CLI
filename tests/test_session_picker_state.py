"""Pin the SessionPickerState pure state machine.

The prompt-toolkit Application shell can't be exercised in a headless
test runner — so the picker is split into a pure state machine
(`SessionPickerState`, no I/O) and a thin UI shell. Tests target the
state machine; the shell is verified manually + indirectly via the
`mucli.choose_session` fallback path test."""

import pytest

from ui.session_picker import NEW_SESSION, SessionPickerState


def _state(*sessions: str) -> SessionPickerState:
    return SessionPickerState(sessions=list(sessions))


# ----------------------------------------------- items / cursor


def test_items_always_includes_new_session_sentinel():
    s = _state("alpha", "beta")
    items = s.items
    assert items[:-1] == ["alpha", "beta"]
    assert items[-1] is NEW_SESSION


def test_empty_session_list_still_has_new_sentinel():
    s = _state()
    assert s.items == [NEW_SESSION]
    assert s.current() is NEW_SESSION


def test_cursor_starts_on_first_session():
    s = _state("alpha", "beta", "gamma")
    assert s.current() == "alpha"


def test_move_advances_cursor():
    s = _state("alpha", "beta", "gamma")
    s.move(1)
    assert s.current() == "beta"
    s.move(1)
    assert s.current() == "gamma"
    s.move(1)
    assert s.current() is NEW_SESSION  # the sentinel


def test_move_wraps_around():
    s = _state("alpha", "beta")
    s.move(-1)  # from index 0 → wraps to last (NEW_SESSION)
    assert s.current() is NEW_SESSION
    s.move(1)  # from NEW_SESSION → wraps to alpha
    assert s.current() == "alpha"


def test_move_on_empty_list_is_safe():
    s = _state()
    s.move(1)  # only NEW_SESSION exists
    assert s.current() is NEW_SESSION


# ----------------------------------------------- delete request / confirm


def test_request_delete_stages_pending():
    s = _state("alpha", "beta")
    assert s.request_delete() is True
    assert s.pending_delete == "alpha"


def test_request_delete_on_new_sentinel_refuses():
    """[+ New Session] can't be deleted."""
    s = _state("alpha")
    s.move(1)
    assert s.current() is NEW_SESSION
    assert s.request_delete() is False
    assert s.pending_delete is None


def test_confirm_delete_removes_from_list():
    s = _state("alpha", "beta", "gamma")
    s.move(1)  # → beta
    s.request_delete()
    assert s.confirm_delete() == "beta"
    assert s.sessions == ["alpha", "gamma"]
    assert s.pending_delete is None


def test_confirm_delete_with_no_pending_is_noop():
    s = _state("alpha")
    assert s.confirm_delete() is None
    assert s.sessions == ["alpha"]


def test_cancel_delete_clears_pending():
    s = _state("alpha", "beta")
    s.request_delete()
    s.cancel_delete()
    assert s.pending_delete is None
    assert s.sessions == ["alpha", "beta"]  # nothing removed


def test_delete_keeps_cursor_in_bounds_when_last_item_removed():
    """Deleting the highlighted item: cursor must clamp into the new
    (smaller) range so subsequent renders don't crash."""
    s = _state("alpha", "beta")
    # Move to the last *real* session (beta).
    s.move(1)
    assert s.current() == "beta"
    s.request_delete()
    s.confirm_delete()
    # Now items = ["alpha", NEW_SESSION]. Cursor was at index 1 (beta);
    # after delete it should still be in-bounds.
    assert 0 <= s.cursor < len(s.items)
    # And current() returns something valid.
    cur = s.current()
    assert cur == "alpha" or cur is NEW_SESSION


def test_delete_all_sessions_leaves_only_new_sentinel():
    s = _state("alpha")
    s.request_delete()
    s.confirm_delete()
    assert s.sessions == []
    assert s.items == [NEW_SESSION]
    assert s.current() is NEW_SESSION


# ----------------------------------------------- integration with choose_session


def test_interactive_picker_uses_full_screen_mode():
    """Regression pin for the rendering bugs: the picker MUST use
    `full_screen=True` so frame redraws don't leave ghost rows when
    the list shrinks (after a delete) and don't overprint the footer
    when the hint line replaces the pending-delete prompt.

    Source-level check — we don't want to actually run a TTY Application
    in pytest."""
    import inspect

    from ui import session_picker

    source = inspect.getsource(session_picker.run_interactive_picker)
    assert "full_screen=True" in source, (
        "run_interactive_picker must set full_screen=True or ghost rows will appear"
    )


def test_safe_delete_session_silent_mode_detaches_ui(tmp_path, monkeypatch):
    """When `silent=True`, `_safe_delete_session` must temporarily set
    `session_manager.ui = None` for the delete_session call so the
    "Deleted session: ..." `show_info` doesn't bleed into the picker's
    TUI render. Restore the UI afterwards."""
    import os
    from pathlib import Path

    import mucli
    from core.session import SessionManager

    monkeypatch.setattr("core.session.HISTORY_DIR", str(tmp_path))
    (tmp_path / "sessions" / "alpha").mkdir(parents=True)
    Path(tmp_path, "sessions", "alpha", "session.json").write_text("{}", encoding="utf-8")

    class _SpyUI:
        def __init__(self):
            self.calls = []

        def show_info(self, body):
            self.calls.append(("info", body))

        def show_error(self, body):
            self.calls.append(("error", body))

    sm = SessionManager()
    sm.ui = _SpyUI()
    spy = sm.ui

    # Silent path — show_info must NOT fire.
    mucli._safe_delete_session(sm, "alpha", silent=True)
    assert spy.calls == []
    # And the UI is restored after the call.
    assert sm.ui is spy


def test_safe_delete_session_loud_mode_keeps_ui_attached(tmp_path, monkeypatch):
    """Default (non-silent) path is unchanged — the fallback numbered
    picker still shows the SessionManager's "Deleted session: ..." line."""
    import os
    from pathlib import Path

    import mucli
    from core.session import SessionManager

    monkeypatch.setattr("core.session.HISTORY_DIR", str(tmp_path))
    (tmp_path / "sessions" / "alpha").mkdir(parents=True)
    Path(tmp_path, "sessions", "alpha", "session.json").write_text("{}", encoding="utf-8")

    class _SpyUI:
        def __init__(self):
            self.calls = []

        def show_info(self, body):
            self.calls.append(("info", body))

        def show_error(self, body):
            self.calls.append(("error", body))

    sm = SessionManager()
    sm.ui = _SpyUI()

    mucli._safe_delete_session(sm, "alpha")
    # The SessionManager's "Deleted session: 'alpha'" should have fired.
    assert any("Deleted session" in body for kind, body in sm.ui.calls if kind == "info"), (
        f"expected 'Deleted session' info; got {sm.ui.calls}"
    )


def test_choose_session_falls_back_to_numbered_picker_in_headless(
    tmp_path, monkeypatch
):
    """When prompt-toolkit's `Application.run()` can't drive the
    current terminal (e.g. pytest's captured stdin), `choose_session`
    must fall through to the numbered IntPrompt fallback rather than
    crash. End-to-end check using the same fixture pattern as the
    other picker tests."""
    import os
    from pathlib import Path

    import mucli
    from core.session import SessionManager

    monkeypatch.setattr("core.session.HISTORY_DIR", str(tmp_path))
    (tmp_path / "sessions" / "alpha").mkdir(parents=True)
    Path(tmp_path, "sessions", "alpha", "session.json").write_text("{}", encoding="utf-8")
    sm = SessionManager()

    # Force the interactive picker to raise so we exercise the fallback.
    def _boom(*a, **kw):
        raise RuntimeError("no tty for prompt-toolkit")

    monkeypatch.setattr("ui.session_picker.run_interactive_picker", _boom)
    # Fallback uses IntPrompt — return 1 (load alpha).
    monkeypatch.setattr("mucli.IntPrompt.ask", lambda *a, **kw: 1)

    action, name = mucli.choose_session(sm)
    assert action == "load"
    assert name == "alpha"
