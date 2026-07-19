"""Tests for the real `spawn_agent` implementation (async orchestrator).

`spawn_agent` dispatches a child Session to a background daemon thread and
returns immediately with a ``task_id``. The parent retrieves results by
polling the registry. These tests script a child's per-iteration behaviour
via a fake provider, dispatch it, then poll until the child finishes.

The fake provider boxes its capture state in a shared dict so the shallow
copy made by ``clone_for_child()`` shares it — the background child thread's
``generate()`` writes are visible to the test thread.
"""

import time

import pytest

from mu.session.session import Session, SessionManager
from mu.workspace.folder_context import FolderContext
from mu.tools import build_tool_context, execute
from mu.tools.agent.spawn import MAX_SUBAGENT_DEPTH
from providers.base import LLMProvider, MessagePart, ProviderResponse


class _ScriptedProvider(LLMProvider):
    """Provider that returns a queued sequence of ProviderResponses.

    Each call to `generate()` pops the next response. Captures the
    system_prompt and tools in a SHARED dict so a shallow copy (made by
    `clone_for_child`) sees the child's writes — the child runs on a
    background thread with its own provider copy.
    """

    def __init__(self, responses, available=None):
        super().__init__("scripted-model")
        self.name = "scripted"
        self.queue = list(responses)
        # Models this provider advertises as installed. Defaults to just the
        # parent's own model; tests that exercise a model override pass a
        # wider list so the override is considered "installed".
        self._available = list(available) if available is not None else ["scripted-model"]
        # Shared by reference across copy.copy() — the child's copy writes
        # here and the test thread reads here.
        self._captures = {"system_prompt": None, "tool_names": None}

    def get_available_models(self):
        return list(self._available)

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        self._captures["system_prompt"] = system_prompt or ""
        self._captures["tool_names"] = (
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


def _poll_until(parent, task_id, target=("done", "killed", "error"), timeout=10.0):
    """Poll the registry until the child reaches a terminal status."""
    registry = parent._subagent_registry
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = registry.snapshot(task_id)
        if snap.get("status") in target:
            return snap
        time.sleep(0.02)
    return registry.snapshot(task_id)


# ---------------------------------------------------------------- happy path


def test_subagent_model_is_in_variable_schema():
    """subagent_model is a real session variable so /set and the GUI can drive
    it; default "" means children inherit the parent model."""
    from utils.config import VARIABLE_SCHEMA, DEFAULT_VARIABLES

    entry = VARIABLE_SCHEMA.get("subagent_model")
    assert entry is not None, "subagent_model missing from VARIABLE_SCHEMA"
    assert entry["type"] is str
    assert entry["default"] == ""
    assert DEFAULT_VARIABLES["subagent_model"] == ""


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

    # Async contract: dispatch returns immediately with task_id + running.
    assert result["ok"] is True
    assert result["data"]["status"] == "running"
    task_id = result["data"]["task_id"]
    assert result["data"]["depth"] == 1

    # Poll for completion and assert the final summary + tokens.
    snap = _poll_until(parent, task_id)
    assert snap["status"] == "done"
    assert "subagent says: done" in snap["summary"]
    assert snap["tokens"].get("total", 0) >= 15


def test_spawn_agent_passes_task_into_child_system_prompt(tmp_path, monkeypatch):
    provider = _ScriptedProvider(
        responses=[
            ProviderResponse(
                text="ok", parts=[MessagePart(type="text", text="ok")]
            )
        ]
    )
    parent = _build_parent(tmp_path, provider, monkeypatch)

    res = execute("spawn_agent", {"task": "refactor module X"}, _ctx_for(parent))
    task_id = res["data"]["task_id"]
    # Wait for the child to have made at least one generate() call.
    _poll_until(parent, task_id)

    child_prompt = provider._captures["system_prompt"]
    assert child_prompt is not None, "child never called generate()"
    assert "refactor module X" in child_prompt
    # The subagent system prompt should NOT contain the parent's system text.
    assert "system" not in child_prompt or "Sub-agent task" in child_prompt


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

    res = execute("spawn_agent", {"task": "go"}, _ctx_for(parent))
    _poll_until(parent, res["data"]["task_id"])

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

    res = execute("spawn_agent", {"task": "z"}, _ctx_for(parent))
    _poll_until(parent, res["data"]["task_id"])

    # The child's tool list (captured by the shared provider dict) must not
    # include spawn_agent.
    tool_names = provider._captures["tool_names"]
    if tool_names is not None:
        assert "spawn_agent" not in tool_names


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

    res = execute(
        "spawn_agent",
        {"task": "ping", "tools": ["read_file", "list_dir"]},
        _ctx_for(parent),
    )
    _poll_until(parent, res["data"]["task_id"])

    # The child saw only read_file, list_dir, and flush (always-on).
    tool_names = provider._captures["tool_names"]
    if tool_names is not None:
        names = set(tool_names)
        # Allowed
        assert "read_file" in names
        assert "list_dir" in names
        # Always kept
        assert "flush" in names
        # NOT in whitelist → should be absent
        assert "bash" not in names
        assert "write_file" not in names


# ---------------------------------------------------------- model override


def test_spawn_agent_model_override_does_not_mutate_parent(tmp_path, monkeypatch):
    """The child gets a cloned provider; the parent's model_name is never
    touched (the old race where concurrent children clobbered a single
    shared model_name is gone)."""
    provider = _ScriptedProvider(
        responses=[
            ProviderResponse(
                text="ok", parts=[MessagePart(type="text", text="ok")]
            )
        ],
        # Advertise the override as installed so the new validation honors it.
        available=["scripted-model", "different-model"],
    )
    parent = _build_parent(tmp_path, provider, monkeypatch)
    parent.provider.model_name = "original-model"

    res = execute(
        "spawn_agent",
        {"task": "do", "model": "different-model"},
        _ctx_for(parent),
    )
    task_id = res["data"]["task_id"]
    record = parent._subagent_registry.get(task_id)
    _poll_until(parent, task_id)

    # Parent's provider is untouched.
    assert parent.provider.model_name == "original-model"
    # The child's cloned provider carries the override.
    assert record.child.provider.model_name == "different-model"


def test_spawn_agent_defaults_to_parent_model(tmp_path, monkeypatch):
    """With no subagent_model config and no model arg, the child inherits
    the parent's model — the documented default."""
    provider = _ScriptedProvider(
        responses=[
            ProviderResponse(text="ok", parts=[MessagePart(type="text", text="ok")])
        ]
    )
    parent = _build_parent(tmp_path, provider, monkeypatch)
    parent.provider.model_name = "parent-model"

    res = execute("spawn_agent", {"task": "do"}, _ctx_for(parent))
    task_id = res["data"]["task_id"]
    record = parent._subagent_registry.get(task_id)
    _poll_until(parent, task_id)

    assert record.child.provider.model_name == "parent-model"
    assert parent.provider.model_name == "parent-model"


def test_spawn_agent_subagent_model_config_overrides_arg(tmp_path, monkeypatch):
    """The subagent_model session variable takes priority over the agent's
    per-call `model` arg, and is applied when installed."""
    provider = _ScriptedProvider(
        responses=[
            ProviderResponse(text="ok", parts=[MessagePart(type="text", text="ok")])
        ],
        available=["scripted-model", "config-model", "arg-model"],
    )
    parent = _build_parent(tmp_path, provider, monkeypatch)
    parent.provider.model_name = "parent-model"
    parent.variables["subagent_model"] = "config-model"

    res = execute(
        "spawn_agent",
        {"task": "do", "model": "arg-model"},
        _ctx_for(parent),
    )
    record = parent._subagent_registry.get(res["data"]["task_id"])
    _poll_until(parent, res["data"]["task_id"])

    # Config wins over the arg; the child runs on config-model.
    assert record.child.provider.model_name == "config-model"
    assert parent.provider.model_name == "parent-model"


def test_spawn_agent_uninstalled_arg_falls_back_to_parent(tmp_path, monkeypatch, caplog):
    """A hallucinated/uninstalled `model` arg no longer crashes the child —
    it falls back to the parent model with a warning. This is the fix for
    "Ollama model 'sonnet-3.5' is not installed"."""
    provider = _ScriptedProvider(
        responses=[
            ProviderResponse(text="ok", parts=[MessagePart(type="text", text="ok")])
        ],
        available=["scripted-model"],  # "sonnet-3.5" is NOT installed
    )
    parent = _build_parent(tmp_path, provider, monkeypatch)
    parent.provider.model_name = "parent-model"

    import logging

    with caplog.at_level(logging.WARNING, logger="mucli"):
        res = execute(
            "spawn_agent",
            {"task": "do", "model": "sonnet-3.5"},
            _ctx_for(parent),
        )
    record = parent._subagent_registry.get(res["data"]["task_id"])
    _poll_until(parent, res["data"]["task_id"])

    # The uninstalled arg was ignored; child runs on the parent model.
    assert record.child.provider.model_name == "parent-model"
    assert parent.provider.model_name == "parent-model"
    assert any("sonnet-3.5" in r.getMessage() for r in caplog.records)


def test_spawn_agent_uninstalled_config_falls_back_to_parent(tmp_path, monkeypatch):
    """An uninstalled subagent_model config also falls back to parent."""
    provider = _ScriptedProvider(
        responses=[
            ProviderResponse(text="ok", parts=[MessagePart(type="text", text="ok")])
        ],
        available=["scripted-model"],
    )
    parent = _build_parent(tmp_path, provider, monkeypatch)
    parent.provider.model_name = "parent-model"
    parent.variables["subagent_model"] = "ghost-model"

    res = execute("spawn_agent", {"task": "do"}, _ctx_for(parent))
    record = parent._subagent_registry.get(res["data"]["task_id"])
    _poll_until(parent, res["data"]["task_id"])

    assert record.child.provider.model_name == "parent-model"


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
    snap = _poll_until(parent, result["data"]["task_id"])
    assert snap["status"] == "done"
    assert target.exists()
    assert target.read_text() == "subagent payload"


# ---------------------------------------------------------- async contract


def test_spawn_agent_returns_running_within_one_second(tmp_path, monkeypatch):
    """Success criterion 1 & 2: dispatch is non-blocking. A child scripted
    to make several tool calls must not delay the dispatch envelope."""
    target = tmp_path / "slow.txt"
    target.write_text("payload")
    provider = _ScriptedProvider(
        responses=[
            ProviderResponse(
                text="",
                parts=[
                    MessagePart(
                        type="tool_call",
                        tool_name="read_file",
                        tool_args={"filename": str(target)},
                    )
                ],
            ),
            ProviderResponse(
                text="",
                parts=[
                    MessagePart(
                        type="tool_call",
                        tool_name="list_dir",
                        tool_args={"path": str(tmp_path)},
                    )
                ],
            ),
            ProviderResponse(
                text="finished after two tools",
                parts=[MessagePart(type="text", text="finished after two tools")],
            ),
        ]
    )
    parent = _build_parent(tmp_path, provider, monkeypatch)

    t0 = time.monotonic()
    result = execute("spawn_agent", {"task": "multi-step"}, _ctx_for(parent))
    elapsed = time.monotonic() - t0

    assert result["ok"] is True
    assert result["data"]["status"] == "running"
    assert result["data"]["task_id"]
    # Dispatch must return well inside the 1s budget (the child is still
    # running its tool loop in the background).
    assert elapsed < 1.0, f"dispatch took {elapsed:.2f}s — not async"

    # While the child is still running, a poll should report running OR a
    # terminal state if it already finished (both acceptable). Confirm the
    # child eventually completes with the multi-tool summary.
    snap = _poll_until(parent, result["data"]["task_id"])
    assert snap["status"] == "done"
    assert "finished after two tools" in snap["summary"]
    assert snap["tool_calls"] >= 2