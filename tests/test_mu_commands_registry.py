"""Tests for the new slash-command registry in `mu.commands`.

The registry currently coexists with the legacy `mucli.handle_command`
dispatcher; this exercises the new path on a small set of ported
commands (`/quit`, `/clear`, `/view`, `/help`, `/thinking`, `/agentic`,
`/yolo`, `/stats`).
"""

from types import SimpleNamespace

import pytest

import mu.commands as mc
from mu.session.session import Session, SessionManager
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


def test_clear_does_not_touch_history(session):
    """/clear only wipes the terminal screen — history stays put."""
    # Isolate from any prior on-disk session state.
    session.session_manager.history = []
    session.session_manager.history.append(
        {"role": "user", "parts": [{"type": "text", "text": "hi"}]}
    )
    result = mc.dispatch(session, "/clear", allow_prompt=False)
    assert result.ok is True
    # History MUST NOT be touched by /clear in v2.
    assert len(session.session_manager.history) == 1


def test_clear_calls_console_clear_when_allowed(session):
    """With allow_prompt=True and a UI console available, /clear invokes
    console.clear() so the user starts on an empty screen."""
    cleared = {"calls": 0}

    class _FakeConsole:
        def clear(self):
            cleared["calls"] += 1

    session.ui = SimpleNamespace(console=_FakeConsole())
    result = mc.dispatch(session, "/clear", allow_prompt=True)
    assert result.ok is True
    assert cleared["calls"] == 1


def test_clear_skips_terminal_when_allow_prompt_false(session):
    """In non-interactive contexts (allow_prompt=False) the terminal must
    not be cleared even if a UI console is attached."""
    cleared = {"calls": 0}

    class _FakeConsole:
        def clear(self):
            cleared["calls"] += 1

    session.ui = SimpleNamespace(console=_FakeConsole())
    result = mc.dispatch(session, "/clear", allow_prompt=False)
    assert result.ok is True
    assert cleared["calls"] == 0


def test_history_show_returns_history(session):
    # Isolate from any prior on-disk session state.
    session.session_manager.history = []
    session.session_manager.history.append(
        {"role": "user", "parts": [{"type": "text", "text": "hi"}]}
    )
    result = mc.dispatch(session, "/history", allow_prompt=False)
    assert result.ok is True
    assert len(result.data["history"]) == 1


def test_history_clear_wipes_history(session):
    session.session_manager.history.append(
        {"role": "user", "parts": [{"type": "text", "text": "hi"}]}
    )
    result = mc.dispatch(session, "/history clear", allow_prompt=False)
    assert result.ok is True
    assert session.session_manager.history == []


def test_history_rejects_unknown_subcommand(session):
    result = mc.dispatch(session, "/history nope", allow_prompt=False)
    assert result is not None
    assert result.ok is False


def test_view_command_no_longer_registered(session):
    """`/view` was renamed to `/history` — the registry must not catch it."""
    result = mc.dispatch(session, "/view", allow_prompt=False)
    assert result is None


def test_help_lists_registered_commands(session):
    result = mc.dispatch(session, "/help", allow_prompt=False)
    assert result.ok is True
    assert "/quit" in result.message
    assert "/stats" in result.message


def test_help_includes_legacy_and_registry_commands(session):
    """/help must surface BOTH registry-ported commands (like /skills) and
    commands still implemented in the legacy mucli.py dispatch
    (like /model, /continue, /feature). Regression-pin for the gap
    where /help only listed registry-ported commands."""
    result = mc.dispatch(session, "/help", allow_prompt=False)
    assert result.ok is True
    must_appear = {
        # registry-ported
        "/quit", "/clear", "/history", "/session", "/workspace",
        "/skills", "/docs", "/mcp", "/stats", "/plan",
        "/agentic", "/thinking", "/yolo",
        # still in legacy mucli.py dispatcher
        "/continue", "/model", "/provider",
        "/set", "/get", "/unset", "/variables",
        "/mode", "/research", "/memory", "/tool", "/feature",
        "/ollama",
    }
    for cmd in must_appear:
        assert cmd in result.message, f"/help is missing {cmd}"
    # /system and /flush were removed; the flush *tool* lives on but the
    # slash command is gone.
    for cmd in ("/system", "/flush"):
        assert cmd not in result.message, f"/help still mentions removed {cmd}"


def test_system_and_flush_commands_are_gone(session):
    """Sanity-pin: /system and /flush were dropped (the flush *tool*
    survives in mu.tools for the model)."""
    assert mc.dispatch(session, "/system", allow_prompt=False) is None
    assert mc.dispatch(session, "/system override", allow_prompt=False) is None
    assert mc.dispatch(session, "/flush", allow_prompt=False) is None

    # The `flush` tool must still be registered for the model to call.
    from mu.tools._dispatcher import TOOL_HANDLERS
    from mu.tools.descriptors import TOOLS

    assert any(t.name == "flush" for t in TOOLS), "flush tool definition removed"
    assert "flush" in TOOL_HANDLERS, "flush tool handler removed"


def test_help_h_alias(session):
    """/h should resolve to /help."""
    result = mc.dispatch(session, "/h", allow_prompt=False)
    assert result is not None
    assert result.ok is True
    assert "/quit" in result.message


def test_help_returns_structured_data(session):
    """Non-interactive callers (JSON output) need a structured command list."""
    result = mc.dispatch(session, "/help", allow_prompt=False)
    assert "commands" in result.data
    assert "groups" in result.data
    commands = result.data["commands"]
    assert len(commands) > 20  # we have a lot of commands
    # Each entry has the expected shape
    for entry in commands:
        assert "command" in entry
        assert "description" in entry
        assert "group" in entry


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
