"""Tests for the pinned session_goal mechanism.

The mechanism has four layers:
  1. `session_goal` variable in VARIABLE_SCHEMA (string, default empty).
  2. L3 rendering via `_build_active_goal_context` — renders in EVERY
     mode, not just loop.
  3. Durable persistence via `_ensure_session_goal_persistence` —
     mirrors into task_memory with `goal:locked` tag so compaction
     can't erase it.
  4. `/goal` slash command + `set_session_goal` agent tool.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


@pytest.fixture
def session(tmp_path, monkeypatch):
    """Build a minimal Session for the variable + rendering tests."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("utils.config.HISTORY_DIR", str(tmp_path / "_mucli_home"))
    from mu.session.session import Session, SessionManager
    from providers.base import LLMProvider, ProviderResponse

    class _StubProvider(LLMProvider):
        name = "stub"
        model_name = "stub"
        provider_name = "stub"

        def get_available_models(self):
            return ["stub"]

        def generate(self, messages, system_prompt=None, thinking=False, tools=None):
            return ProviderResponse(
                text="", parts=[], input_tokens=0, output_tokens=0, total_tokens=0
            )

        def upload_file(self, file_path, mime_type):
            return None

    sm = SessionManager()
    sess = Session(
        provider=_StubProvider(),
        thinking=False,
        system_instruction="",
        session_manager=sm,
        ui=None,
    )
    return sess


# ------------------------------------------------------ variable schema


def test_variable_schema_includes_session_goal():
    from utils.config import VARIABLE_SCHEMA, DEFAULT_VARIABLES

    assert "session_goal" in VARIABLE_SCHEMA
    assert VARIABLE_SCHEMA["session_goal"]["type"] is str
    assert DEFAULT_VARIABLES["session_goal"] == ""


# ----------------------------------------------------- L3 rendering


def test_l3_renders_pinned_goal_in_default_mode(session):
    session.variables["agent_mode"] = "default"
    session.variables["session_goal"] = "Refactor the auth layer"
    body = session._build_active_goal_context()
    assert "session_goal" in body
    assert "Refactor the auth layer" in body


def test_l3_renders_pinned_goal_in_teacher_mode(session):
    session.variables["agent_mode"] = "teacher"
    session.variables["session_goal"] = "Teach me C"
    body = session._build_active_goal_context()
    assert "Teach me C" in body
    # The policy hint should accompany the goal so the model knows what
    # to do with the pin.
    assert "session_goal_policy" in body


def test_l3_renders_pinned_goal_alongside_loop_goal(session):
    """When both session_goal and loop_goal are set in loop mode, both
    render in L3 — the pinned goal is the overarching ask; the loop
    goal is the current segment objective."""
    session.variables["agent_mode"] = "loop"
    session.variables["session_goal"] = "Ship the v2 migration"
    session.variables["loop_goal"] = "Migrate the users table"
    body = session._build_active_goal_context()
    assert "Ship the v2 migration" in body
    assert "Migrate the users table" in body


def test_l3_omits_pinned_goal_when_unset(session):
    session.variables["session_goal"] = ""
    body = session._build_active_goal_context()
    assert "session_goal" not in body


# ----------------------------------------------- durable persistence


def test_ensure_session_goal_persistence_writes_to_task_memory(session):
    session.variables["session_goal"] = "Audit the auth layer"
    assert len(session.task_memory.entries) == 0
    session._ensure_session_goal_persistence()
    entries = session.task_memory.entries
    assert len(entries) == 1
    assert "Audit the auth layer" in str(entries[0].content)
    # Tags must include goal+locked so /memory search and the future
    # recovery hatch can find the audit row.
    assert "goal" in entries[0].tags
    assert "locked" in entries[0].tags


def test_ensure_session_goal_persistence_is_idempotent(session):
    """Calling the persistence hook every turn must not duplicate the
    memory entry — same goal text only writes once."""
    session.variables["session_goal"] = "Refactor the API"
    session._ensure_session_goal_persistence()
    session._ensure_session_goal_persistence()
    session._ensure_session_goal_persistence()
    assert len(session.task_memory.entries) == 1


def test_ensure_session_goal_persistence_noop_when_empty(session):
    session.variables["session_goal"] = ""
    session._ensure_session_goal_persistence()
    assert len(session.task_memory.entries) == 0


def test_ensure_session_goal_persistence_records_new_goal_after_change(session):
    """When the user replaces the goal, the new text gets its own
    durable audit entry — the old one stays as history."""
    session.variables["session_goal"] = "First goal"
    session._ensure_session_goal_persistence()
    session.variables["session_goal"] = "Second goal"
    session._ensure_session_goal_persistence()
    contents = [str(e.content) for e in session.task_memory.entries]
    assert any("First goal" in c for c in contents)
    assert any("Second goal" in c for c in contents)


# --------------------------------------------------- /goal command


def _goal_cmd(session, args):
    from mu.commands.goal import goal_cmd

    return goal_cmd(session, args, allow_prompt=False)


def test_goal_set_pins_into_variables(session):
    res = _goal_cmd(session, "Refactor the auth layer")
    assert res.ok
    assert session.variables["session_goal"] == "Refactor the auth layer"
    assert "Refactor the auth layer" in res.message


def test_goal_set_explicit_form(session):
    res = _goal_cmd(session, "set Teach me Kubernetes")
    assert res.ok
    assert session.variables["session_goal"] == "Teach me Kubernetes"


def test_goal_clear_removes_pin(session):
    session.variables["session_goal"] = "Some goal"
    res = _goal_cmd(session, "clear")
    assert res.ok
    assert session.variables["session_goal"] == ""
    assert "cleared" in res.message.lower()


def test_goal_clear_when_already_empty_is_friendly(session):
    res = _goal_cmd(session, "clear")
    assert res.ok
    assert "No session goal pinned" in res.message


def test_goal_show_with_no_pin(session):
    res = _goal_cmd(session, "show")
    assert res.ok
    assert "No session goal pinned" in res.message


def test_goal_show_with_pin(session):
    session.variables["session_goal"] = "Ship v2"
    res = _goal_cmd(session, "show")
    assert res.ok
    assert "Ship v2" in res.message


def test_goal_bare_call_shows(session):
    """`/goal` with no args is the show form (not an error)."""
    session.variables["session_goal"] = "Audit"
    res = _goal_cmd(session, "")
    assert res.ok
    assert "Audit" in res.message


def test_goal_set_replaces_and_records_previous(session):
    _goal_cmd(session, "First goal")
    res = _goal_cmd(session, "Second goal")
    assert res.ok
    assert "Previous" in res.message
    assert "First goal" in res.message
    assert "Second goal" in res.message


def test_goal_empty_set_is_rejected(session):
    res = _goal_cmd(session, "set   ")
    assert not res.ok
    assert "/goal clear" in res.message


def test_goal_set_mirrors_into_task_memory(session):
    _goal_cmd(session, "Pin this")
    contents = [str(e.content) for e in session.task_memory.entries]
    assert any("Pin this" in c for c in contents)


def test_goal_help_returns_usage(session):
    res = _goal_cmd(session, "help")
    assert res.ok
    assert "/goal" in res.message
    assert "L3" in res.message


# -------------------------------------------- set_session_goal tool


def _call_set_goal(session, args):
    from mu.tools.prompt.handlers import set_session_goal_tool

    context = SimpleNamespace(
        session=session,
        ui=getattr(session, "ui", None),
        folder_context=session.folder_context,
        variables=session.variables,
    )
    return set_session_goal_tool(args, context)


def test_set_session_goal_tool_pins(session):
    raw = _call_set_goal(session, {"goal": "Build the v2 API"})
    payload = json.loads(raw)
    assert payload["ok"] is True
    assert payload["goal"] == "Build the v2 API"
    assert payload["replaced"] is False
    assert session.variables["session_goal"] == "Build the v2 API"


def test_set_session_goal_tool_replaces(session):
    _call_set_goal(session, {"goal": "First"})
    raw = _call_set_goal(session, {"goal": "Second"})
    payload = json.loads(raw)
    assert payload["replaced"] is True
    assert payload["previous_goal"] == "First"
    assert session.variables["session_goal"] == "Second"


def test_set_session_goal_tool_clears(session):
    _call_set_goal(session, {"goal": "Pin me"})
    raw = _call_set_goal(session, {"clear": True})
    payload = json.loads(raw)
    assert payload["ok"] is True
    assert payload["cleared"] is True
    assert payload["previous_goal"] == "Pin me"
    assert session.variables["session_goal"] == ""


def test_set_session_goal_tool_requires_goal_when_not_clearing(session):
    raw = _call_set_goal(session, {"goal": "   "})
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert "non-empty" in payload["error"]


def test_set_session_goal_tool_persists_to_task_memory(session):
    _call_set_goal(session, {"goal": "Durable goal"})
    contents = [str(e.content) for e in session.task_memory.entries]
    assert any("Durable goal" in c for c in contents)


def test_set_session_goal_tool_handles_missing_session():
    """The tool must fail gracefully when no session is on the context."""
    from mu.tools.prompt.handlers import set_session_goal_tool

    context = SimpleNamespace(session=None, ui=None)
    raw = set_session_goal_tool({"goal": "x"}, context)
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert "session" in payload["error"]


# --------------------------------------- visible in /help + system prompt


def test_set_session_goal_in_tool_descriptors():
    from mu.tools.descriptors import TOOL_DESCRIPTORS

    assert "set_session_goal" in TOOL_DESCRIPTORS


def test_goal_appears_in_help_groups():
    from mucli import _curated_commands

    assert "/goal" in _curated_commands()


def test_session_goal_mentioned_in_system_base():
    from utils.config import AGENTIC_SYSTEM_BASE

    assert "set_session_goal" in AGENTIC_SYSTEM_BASE
