"""Tests for the hint + retryable envelope extensions and the session-level
retryable-failure announcer.
"""

import json

import pytest

from mu.session.session import Session, SessionManager
from mu.tools._envelope import _build_tool_envelope, _envelope_from_handler_result
from mu.workspace.folder_context import FolderContext
from mu.tools._hints import hint_for, retryable_for_code
from providers.base import LLMProvider, MessagePart, ProviderResponse


# ============================================================ hint registry


def test_hint_for_known_tool_code_pair():
    h = hint_for("read_file", "not_found")
    assert h is not None
    assert "list_dir" in h or "search_for_string" in h


def test_hint_for_falls_back_to_generic_when_no_tool_override():
    h = hint_for("some_obscure_tool", "not_found")
    assert h is not None
    # The generic "not_found" hint mentions discovery tools.
    assert "discover" in h.lower() or "list_dir" in h


def test_hint_for_returns_none_when_no_code():
    assert hint_for("read_file", None) is None
    assert hint_for("read_file", "") is None


def test_hint_for_unknown_code_returns_none():
    assert hint_for("read_file", "no_such_code_in_universe") is None


def test_retryable_for_code_true_for_recoverable_codes():
    for code in ("not_found", "invalid_args", "preview_failed", "execution_failed"):
        assert retryable_for_code(code) is True, f"{code} should be retryable"


def test_retryable_for_code_false_for_terminal_codes():
    for code in ("access_denied", "unsupported", "plan_mode_blocked", "hook_denied"):
        assert retryable_for_code(code) is False, f"{code} should NOT be retryable"


def test_retryable_for_code_false_for_no_code():
    assert retryable_for_code(None) is False
    assert retryable_for_code("") is False


# ============================================================ envelope shape


def test_build_tool_envelope_includes_hint_and_retryable_keys():
    env = _build_tool_envelope(tool_name="read_file", ok=True, message="ok")
    assert "hint" in env
    assert "retryable" in env
    assert env["hint"] is None
    assert env["retryable"] is False


def test_build_tool_envelope_failure_auto_populates_hint_from_registry():
    env = _build_tool_envelope(
        tool_name="read_file",
        ok=False,
        message="File not found: /x",
        error_code="not_found",
    )
    assert env["ok"] is False
    assert env["retryable"] is True
    assert env["hint"] is not None
    assert "list_dir" in env["hint"] or "search_for_string" in env["hint"]


def test_build_tool_envelope_explicit_hint_overrides_registry():
    env = _build_tool_envelope(
        tool_name="read_file",
        ok=False,
        message="x",
        error_code="not_found",
        hint="custom override",
    )
    assert env["hint"] == "custom override"


def test_build_tool_envelope_non_retryable_code_keeps_retryable_false():
    env = _build_tool_envelope(
        tool_name="apply_diff",
        ok=False,
        message="Plan mode blocked.",
        error_code="plan_mode_blocked",
    )
    assert env["retryable"] is False
    assert env["hint"] is not None  # generic hint still attached


def test_envelope_from_handler_result_string_inference_includes_hint():
    """Plain-string handler result with an 'Error: File not found' pattern."""
    env = _envelope_from_handler_result(
        "read_file", "Error: File '/missing.py' not found. Try search_for_string."
    )
    assert env["ok"] is False
    # The string-based inference produces 'execution_failed' for generic
    # Error-prefixed strings, which IS retryable.
    assert env["error_code"] is not None
    assert env["retryable"] in (True, False)  # well-formed boolean
    # Failure envelopes carry a hint or None — never a raw string error.
    assert "hint" in env


def test_envelope_passthrough_backfills_hint_on_legacy_six_key_envelope():
    """If a handler returns a fully-formed 6-key envelope, hint/retryable
    must still be added so downstream consumers see the new fields."""
    legacy = {
        "ok": False,
        "error_code": "not_found",
        "message": "missing",
        "data": {},
        "artifacts": [],
        "telemetry": {"tool_name": "read_file"},
    }
    env = _envelope_from_handler_result("read_file", legacy)
    assert env["retryable"] is True
    assert env["hint"] is not None


# ============================================================ session announcer


class _RecordingUI:
    def __init__(self):
        self.info_calls = []
        self.error_calls = []
    def show_info(self, m):
        self.info_calls.append(str(m))
    def show_error(self, m):
        self.error_calls.append(str(m))


class _DummyProvider(LLMProvider):
    def get_available_models(self):
        return ["dummy"]
    def generate(self, *a, **kw):
        return ProviderResponse(text="", parts=[])
    def upload_file(self, *a, **kw):
        return None


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.config.HISTORY_DIR", str(tmp_path / "history"))
    sm = SessionManager()
    sess = Session(_DummyProvider("dummy"), False, "system", sm, ui=_RecordingUI())
    return sess


def test_announcer_surfaces_hint_to_ui_on_retryable_failure(session):
    envelope = {
        "ok": False,
        "error_code": "not_found",
        "message": "File missing",
        "data": {},
        "artifacts": [],
        "hint": "Use list_dir to find the correct path.",
        "retryable": True,
        "telemetry": {"tool_name": "read_file"},
    }
    session._announce_retryable_failure("read_file", envelope)
    assert any("Use list_dir" in m for m in session.ui.info_calls)


def test_announcer_accepts_json_string_envelope(session):
    envelope = {
        "ok": False,
        "error_code": "not_found",
        "message": "x",
        "data": {},
        "artifacts": [],
        "hint": "Use list_dir.",
        "retryable": True,
        "telemetry": {"tool_name": "read_file"},
    }
    session._announce_retryable_failure("read_file", json.dumps(envelope))
    assert any("Use list_dir" in m for m in session.ui.info_calls)


def test_announcer_silent_on_success_envelope(session):
    envelope = {
        "ok": True,
        "error_code": None,
        "message": "ok",
        "data": {},
        "artifacts": [],
        "hint": None,
        "retryable": False,
        "telemetry": {"tool_name": "read_file"},
    }
    session._announce_retryable_failure("read_file", envelope)
    assert session.ui.info_calls == []


def test_announcer_silent_on_non_retryable_failure(session):
    envelope = {
        "ok": False,
        "error_code": "access_denied",
        "message": "denied",
        "data": {},
        "artifacts": [],
        "hint": "Path is outside workspace.",
        "retryable": False,
        "telemetry": {"tool_name": "read_file"},
    }
    session._announce_retryable_failure("read_file", envelope)
    assert session.ui.info_calls == []


def test_announcer_escalates_on_repeated_failure(session):
    envelope = {
        "ok": False,
        "error_code": "not_found",
        "message": "x",
        "data": {},
        "artifacts": [],
        "hint": "Use list_dir.",
        "retryable": True,
        "telemetry": {"tool_name": "read_file"},
    }
    # First and second time → info-level
    session._announce_retryable_failure("read_file", envelope)
    session._announce_retryable_failure("read_file", envelope)
    assert session.ui.error_calls == []
    # Third hit → escalated to show_error
    session._announce_retryable_failure("read_file", envelope)
    assert any("3x" in m or "stays the same" in m for m in session.ui.error_calls)


def test_announcer_disabled_by_variable(session):
    session.variables["reflective_retry_enabled"] = False
    envelope = {
        "ok": False,
        "error_code": "not_found",
        "message": "x",
        "data": {},
        "artifacts": [],
        "hint": "Use list_dir.",
        "retryable": True,
        "telemetry": {"tool_name": "read_file"},
    }
    session._announce_retryable_failure("read_file", envelope)
    assert session.ui.info_calls == []


def test_announcer_silent_on_non_envelope_string(session):
    """If the tool returned a plain (non-JSON) string, the announcer is a no-op."""
    session._announce_retryable_failure("read_file", "just some text")
    assert session.ui.info_calls == []
    assert session.ui.error_calls == []


# ============================================================ end-to-end envelope flow


def test_real_tool_call_produces_envelope_with_hint(tmp_path):
    """A read_file call against a missing path should produce a failure
    envelope where the model can see the hint."""
    from mu.tools._dispatcher import execute_tool

    fc = FolderContext()
    fc.add_folder(str(tmp_path))
    raw = execute_tool(
        "read_file",
        {"filename": str(tmp_path / "does_not_exist.txt")},
        fc,
    )
    # `execute_tool` returns a JSON-string envelope; parse it.
    env = json.loads(raw)
    assert env["ok"] is False
    # Either 'not_found' (preferred) or 'execution_failed' depending on the
    # path the bounds check + read_file took. Both should be retryable.
    assert env["retryable"] is True
    assert env["hint"] is not None
    assert env["hint"]  # non-empty
