"""Tests for the real `spawn_agent` implementation.

Strategy: build a fake provider that scripts a child's per-iteration
behaviour, point the parent's session at it, and call `spawn_agent`.
The fake provider is shared between parent and child so we can verify
the child sees the right system prompt and tool inventory.
"""

import pytest

from mu.session.session import Session, SessionManager
from mu.workspace.folder_context import FolderContext
from mu.tools import build_tool_context, execute
from mu.tools.agent.spawn import MAX_SUBAGENT_DEPTH
from providers.base import LLMProvider, MessagePart, ProviderResponse


class _ScriptedProvider(LLMProvider):
    """Provider that returns a queued sequence of ProviderResponses.

    Each call to `generate()` pops the next response. Captures the
    system_prompt and tools for assertions.
    """

    def __init__(self, responses):
        super().__init__("scripted-model")
        self.name = "scripted"
        self.queue = list(responses)
        self.last_system_prompt = ""
        self.last_tool_names = None

    def get_available_models(self):
        return ["scripted-model"]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        self.last_system_prompt = system_prompt or ""
        self.last_tool_names = (
            [t.name for t in tools] if tools is not None else None
        )
        if not self.queue:
            return ProviderResponse(text="(empty queue)", parts=[])
        return self.queue.pop(0)

    def upload_file(self, *a, **kw):
        return None


def _build_parent(tmp_path, provider, monkeypatch):
    monkeypatch.setattr("utils.config.HISTORY_DIR", str(tmp_path / "history"))
    sm = SessionManager()
    parent = Session(provider, False, "system", sm)
    fc = FolderContext()
    fc.add_folder(str(tmp_path))
    parent.folder_context = fc
    return parent


def _ctx_for(parent):
    return build_tool_context(
        folder_context=parent.folder_context,
        ui=None,
        variables=parent.variables,
        session=parent,
    )


# ---------------------------------------------------------------- happy path


def test_spawn_agent_runs_child_and_returns_assistant_text(tmp_path, monkeypatch):
    # The child will respond once with assistant text and no tool calls.
    provider = _ScriptedProvider(
        responses=[
            ProviderResponse(
                text="subagent says: done",
                parts=[MessagePart(type="text", text="subagent says: done")],
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
            )
        ]
    )
    parent = _build_parent(tmp_path, provider, monkeypatch)

    result = execute(
        "spawn_agent",
        {"task": "do a thing"},
        _ctx_for(parent),
    )

    assert result["ok"] is True
    assert result["message"] == "subagent says: done"
    assert result["data"]["depth"] == 1
    # The child's task tokens are surfaced.
    assert result["data"]["tokens"]["total"] >= 15


def test_spawn_agent_passes_task_into_child_system_prompt(tmp_path, monkeypatch):
    provider = _ScriptedProvider(
        responses=[
            ProviderResponse(
                text="ok", parts=[MessagePart(type="text", text="ok")]
            )
        ]
    )
    parent = _build_parent(tmp_path, provider, monkeypatch)

    execute("spawn_agent", {"task": "refactor module X"}, _ctx_for(parent))

    assert "refactor module X" in provider.last_system_prompt
    # The subagent system prompt should NOT contain the parent's system text.
    assert "system" not in provider.last_system_prompt or "Sub-agent task" in provider.last_system_prompt


def test_spawn_agent_does_not_pollute_parent_history(tmp_path, monkeypatch):
    provider = _ScriptedProvider(
        responses=[
            ProviderResponse(
                text="subagent finished",
                parts=[MessagePart(type="text", text="subagent finished")],
            )
        ]
    )
    parent = _build_parent(tmp_path, provider, monkeypatch)
    parent_len_before = len(parent.session_manager.history)

    execute("spawn_agent", {"task": "go"}, _ctx_for(parent))

    assert len(parent.session_manager.history) == parent_len_before


# ---------------------------------------------------------- validation

def test_spawn_agent_requires_task(tmp_path, monkeypatch):
    provider = _ScriptedProvider(responses=[])
    parent = _build_parent(tmp_path, provider, monkeypatch)
    res = execute("spawn_agent", {"task": "   "}, _ctx_for(parent))
    assert res["ok"] is False
    assert res["error_code"] == "invalid_args"


def test_spawn_agent_requires_session(tmp_path):
    ctx = build_tool_context(
        folder_context=None, ui=None, variables={}, session=None
    )
    res = execute("spawn_agent", {"task": "x"}, ctx)
    assert res["ok"] is False
    assert res["error_code"] == "no_session"


# ---------------------------------------------------------- depth + recursion


def test_spawn_agent_refuses_at_max_depth(tmp_path, monkeypatch):
    provider = _ScriptedProvider(responses=[])
    parent = _build_parent(tmp_path, provider, monkeypatch)
    parent._subagent_depth = MAX_SUBAGENT_DEPTH  # pretend we're already nested

    res = execute("spawn_agent", {"task": "x"}, _ctx_for(parent))
    assert res["ok"] is False
    assert res["error_code"] == "depth_exceeded"


def test_spawn_agent_disables_further_spawn_at_depth_cap(tmp_path, monkeypatch):
    provider = _ScriptedProvider(
        responses=[
            ProviderResponse(
                text="depth check",
                parts=[MessagePart(type="text", text="depth check")],
            )
        ]
    )
    parent = _build_parent(tmp_path, provider, monkeypatch)
    parent._subagent_depth = MAX_SUBAGENT_DEPTH - 1  # one level above cap

    execute("spawn_agent", {"task": "z"}, _ctx_for(parent))

    # The child's tool list (captured by provider) must not include spawn_agent.
    if provider.last_tool_names is not None:
        assert "spawn_agent" not in provider.last_tool_names


# ---------------------------------------------------------- plan-mode block


def test_spawn_agent_refuses_in_plan_mode(tmp_path, monkeypatch):
    provider = _ScriptedProvider(responses=[])
    parent = _build_parent(tmp_path, provider, monkeypatch)
    parent.variables["plan_mode"] = True

    res = execute("spawn_agent", {"task": "x"}, _ctx_for(parent))
    assert res["ok"] is False
    assert res["error_code"] == "plan_mode_blocked"


# ---------------------------------------------------------- tools whitelist


def test_spawn_agent_whitelist_filters_child_tool_surface(tmp_path, monkeypatch):
    provider = _ScriptedProvider(
        responses=[
            ProviderResponse(
                text="ok", parts=[MessagePart(type="text", text="ok")]
            )
        ]
    )
    parent = _build_parent(tmp_path, provider, monkeypatch)

    execute(
        "spawn_agent",
        {"task": "ping", "tools": ["read_file", "list_dir"]},
        _ctx_for(parent),
    )

    # The child saw only read_file, list_dir, and flush (always-on).
    if provider.last_tool_names is not None:
        names = set(provider.last_tool_names)
        # Allowed
        assert "read_file" in names
        assert "list_dir" in names
        # Always kept
        assert "flush" in names
        # NOT in whitelist → should be absent
        assert "bash" not in names
        assert "write_file" not in names


# ---------------------------------------------------------- model override


def test_spawn_agent_model_override_restores_parent_model(tmp_path, monkeypatch):
    provider = _ScriptedProvider(
        responses=[
            ProviderResponse(
                text="ok", parts=[MessagePart(type="text", text="ok")]
            )
        ]
    )
    parent = _build_parent(tmp_path, provider, monkeypatch)
    parent.provider.model_name = "original-model"

    execute(
        "spawn_agent",
        {"task": "do", "model": "different-model"},
        _ctx_for(parent),
    )

    # Parent's provider must be restored to its original model.
    assert parent.provider.model_name == "original-model"


# ---------------------------------------------------------- YOLO inheritance


def test_spawn_agent_runs_yolo_in_child(tmp_path, monkeypatch):
    """The child is auto-approved so it can complete without prompting."""
    # We script the child to call a modifying tool (write_file) without
    # blocking on approval. If yolo were not set, the approval would call
    # ui.request_tool_approval — and the child's ui is None, so the flow
    # would crash. Successful completion is the proof.
    target = tmp_path / "child_wrote.txt"
    provider = _ScriptedProvider(
        responses=[
            ProviderResponse(
                text="",
                parts=[
                    MessagePart(
                        type="tool_call",
                        tool_name="write_file",
                        tool_args={
                            "filename": str(target),
                            "content": "subagent payload",
                        },
                    )
                ],
            ),
            ProviderResponse(
                text="done",
                parts=[MessagePart(type="text", text="done")],
            ),
        ]
    )
    parent = _build_parent(tmp_path, provider, monkeypatch)

    result = execute(
        "spawn_agent",
        {"task": "write a file", "max_iterations": 4},
        _ctx_for(parent),
    )

    assert result["ok"] is True
    assert target.exists()
    assert target.read_text() == "subagent payload"
