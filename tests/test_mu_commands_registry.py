"""Tests for the new slash-command registry in `mu.commands`.

The registry currently coexists with the legacy `mucli.handle_command`
dispatcher; this exercises the new path on a small set of ported
commands (`/quit`, `/clear`, `/view`, `/help`, `/thinking`, `/agentic`,
`/yolo`, `/stats`).
"""

from types import SimpleNamespace

import pytest

import mu.commands as mc
from core.session import Session, SessionManager
from providers.base import LLMProvider, ProviderResponse


class _DummyProvider(LLMProvider):
    def get_available_models(self):
        return ["dummy"]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        return ProviderResponse(text="", parts=[])

    def upload_file(self, file_path, mime_type):
        return None


@pytest.fixture
def session():
    sm = SessionManager()
    return Session(_DummyProvider("dummy"), False, "system instruction", sm)


def test_quit_returns_exit_flag(session):
    result = mc.dispatch(session, "/quit", allow_prompt=False)
    assert result is not None
    assert result.ok is True
    assert result.exit is True


def test_dispatch_returns_none_for_unknown_command(session):
    assert mc.dispatch(session, "/this-does-not-exist", allow_prompt=False) is None


def test_dispatch_returns_none_for_non_slash_input(session):
    assert mc.dispatch(session, "hello world") is None


def test_thinking_toggle(session):
    session.thinking = False
    result = mc.dispatch(session, "/thinking", allow_prompt=False)
    assert result.ok is True
    assert session.thinking is True
    # Toggle again
    result2 = mc.dispatch(session, "/thinking", allow_prompt=False)
    assert result2.ok is True
    assert session.thinking is False


def test_agentic_toggle(session):
    session.agentic = False
    result = mc.dispatch(session, "/agentic", allow_prompt=False)
    assert result.ok is True
    assert session.agentic is True


def test_yolo_toggle_persists(session):
    session.variables["yolo"] = False
    result = mc.dispatch(session, "/yolo", allow_prompt=False)
    assert result.ok is True
    assert session.variables["yolo"] is True


def test_stats_returns_snapshot(session):
    result = mc.dispatch(session, "/stats", allow_prompt=False)
    assert result.ok is True
    assert "tokens" in result.data
    assert "ctx" in result.data


def test_clear_history(session):
    session.session_manager.history.append(
        {"role": "user", "parts": [{"type": "text", "text": "hi"}]}
    )
    result = mc.dispatch(session, "/clear", allow_prompt=False)
    assert result.ok is True
    assert session.session_manager.history == []


def test_help_lists_registered_commands(session):
    result = mc.dispatch(session, "/help", allow_prompt=False)
    assert result.ok is True
    assert "/quit" in result.message
    assert "/stats" in result.message


def test_command_aliases_share_handler(session):
    """/quit and /q (the one surviving alias) resolve to the same handler.
    /exit was dropped in the alias cleanup."""
    quit_spec = mc.get("/quit")
    q_spec = mc.get("/q")
    assert quit_spec is not None
    assert quit_spec is q_spec
    assert mc.get("/exit") is None  # deliberately dropped


def test_plan_toggle(session):
    session.variables["plan_mode"] = False
    r1 = mc.dispatch(session, "/plan", allow_prompt=False)
    assert r1.ok is True
    assert session.variables["plan_mode"] is True
    r2 = mc.dispatch(session, "/plan", allow_prompt=False)
    assert r2.ok is True
    assert session.variables["plan_mode"] is False


def test_plan_explicit_on_off(session):
    session.variables["plan_mode"] = False
    mc.dispatch(session, "/plan on", allow_prompt=False)
    assert session.variables["plan_mode"] is True
    mc.dispatch(session, "/plan off", allow_prompt=False)
    assert session.variables["plan_mode"] is False


def test_plan_rejects_unknown_arg(session):
    result = mc.dispatch(session, "/plan banana", allow_prompt=False)
    assert result.ok is False
