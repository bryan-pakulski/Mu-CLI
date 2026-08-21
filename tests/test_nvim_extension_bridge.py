"""Regression coverage for the session-scoped Neovim extension bridge."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from types import SimpleNamespace

from providers.base import ProviderResponse, ToolDefinition

from mu.gui.routers import extensions as extension_router
from mu.gui.routers import inspector as inspector_router
from mu.gui.routers import providers as provider_router
from mu.gui.web_ui import WebUI
from mu.session import tools_glue


class _Bus:
    def __init__(self):
        self.events: list[dict] = []

    def publish_threadsafe(self, event):
        self.events.append(event)


class _Manager:
    def __init__(self, name="editor-session"):
        self.current_session_name = name
        self.provider_config = {}
        self.saved = 0
        self.token_counts = {
            "input": 0,
            "output": 0,
            "total": 0,
            "cached": 0,
            "reasoning": 0,
            "total_cost": 0.0,
        }

    def save_history(self, *_args):
        self.saved += 1


def _session(*, extensions=None, bus=None):
    return SimpleNamespace(
        extensions=extensions or {},
        session_manager=_Manager(),
        variables={"extension_tool_timeout_seconds": 5},
        ui=SimpleNamespace(_bus=bus) if bus else None,
    )


def test_provider_definitions_include_fresh_registered_tools(monkeypatch):
    from mu.tools import descriptors

    monkeypatch.setattr(
        descriptors,
        "TOOLS",
        [ToolDefinition("native_tool", "native", {"type": "object"})],
    )
    session = _session(
        extensions={
            "neovim": {
                "client_id": "nvim-client",
                "tool_prefix": "nvim_",
                "last_seen": time.time(),
                "system_prompt": "Live editor bridge",
                "tools": [
                    {
                        "name": "nvim_get_buffer",
                        "description": "Read an unsaved buffer",
                        "parameters": {"type": "object", "properties": {}},
                    },
                    {
                        "name": "native_tool",
                        "description": "Must not shadow a built-in",
                        "parameters": {"type": "object"},
                    },
                ],
            }
        }
    )

    definitions = tools_glue.extension_tool_definitions(session)

    assert [tool.name for tool in definitions] == ["nvim_get_buffer"]
    assert definitions[0].requires_approval is False
    assert tools_glue.extension_system_prompts(session) == ["Live editor bridge"]


def test_stale_extension_tools_are_not_exposed():
    session = _session(
        extensions={
            "neovim": {
                "tool_prefix": "nvim_",
                "last_seen": time.time() - 120,
                "tools": [{"name": "nvim_get_buffer", "parameters": {}}],
            }
        }
    )
    assert tools_glue.extension_tool_definitions(session) == []
    assert tools_glue.extension_system_prompts(session) == []


def test_plan_mode_hides_and_blocks_extension_mutations():
    extension = {
        "tool_prefix": "nvim_",
        "last_seen": time.time(),
        "tools": [
            {
                "name": "nvim_get_buffer",
                "parameters": {},
                "execution_kind": "read",
            },
            {
                "name": "nvim_propose_edit",
                "parameters": {},
                "execution_kind": "mutate",
            },
        ],
    }
    session = _session(extensions={"neovim": extension})
    session.variables["plan_mode"] = True

    assert [tool.name for tool in tools_glue.extension_tool_definitions(session)] == [
        "nvim_get_buffer"
    ]
    assert tools_glue._extension_tool_is_mutating(extension, "nvim_propose_edit")


def test_secret_paths_are_blocked_before_editor_dispatch():
    bus = _Bus()
    extension = {
        "client_id": "nvim-client",
        "last_seen": time.time(),
        "tools": [{"name": "nvim_get_buffer"}],
    }
    session = _session(extensions={"neovim": extension}, bus=bus)

    result = tools_glue._dispatch_extension_tool(
        session,
        "neovim",
        extension,
        "nvim_get_buffer",
        {"file_path": ".env"},
    )

    assert "blocked" in result
    assert "denied filename pattern" in result
    assert bus.events == []

    output: list[str] = []
    thread = threading.Thread(
        target=lambda: output.append(
            tools_glue._dispatch_extension_tool(
                session, "neovim", extension, "nvim_get_buffer", {}
            )
        )
    )
    thread.start()
    for _ in range(100):
        if bus.events:
            break
        time.sleep(0.01)
    assert tools_glue.resolve_extension_tool_result(
        bus.events[0]["call_id"],
        result={"ok": True, "data": {"path": ".env", "content": "secret"}},
        extension_id="neovim",
        client_id="nvim-client",
        session_name="editor-session",
    )
    thread.join(timeout=1)
    assert "blocked" in output[0]


def test_exact_tool_dispatch_works_with_or_without_a_prefix():
    unprefixed = _session(
        extensions={
            "editor": {
                "tool_prefix": "",
                "tools": [{"name": "editor_ping"}],
            }
        }
    )
    assert tools_glue._find_extension_for_tool(unprefixed, "editor_ping")[0] == "editor"

    prefixed = _session(
        extensions={
            "editor": {
                "tool_prefix": "editor_",
                "tools": [{"name": "editor_read"}],
            }
        }
    )
    assert tools_glue._find_extension_for_tool(prefixed, "editor_read")[0] == "editor"
    assert tools_glue._find_extension_for_tool(prefixed, "editor_unregistered") is None


def test_extension_call_is_client_and_session_bound():
    bus = _Bus()
    extension = {
        "client_id": "nvim-client",
        "last_seen": time.time(),
        "tool_prefix": "nvim_",
        "tools": [{"name": "nvim_get_buffer"}],
    }
    session = _session(extensions={"neovim": extension}, bus=bus)
    output: list[str] = []
    thread = threading.Thread(
        target=lambda: output.append(
            tools_glue._dispatch_extension_tool(
                session, "neovim", extension, "nvim_get_buffer", {"file_path": "a.py"}
            )
        )
    )
    thread.start()
    for _ in range(100):
        if bus.events:
            break
        time.sleep(0.01)
    assert bus.events
    event = bus.events[0]
    assert event["session_name"] == "editor-session"
    assert event["client_id"] == "nvim-client"
    assert not tools_glue.resolve_extension_tool_result(
        event["call_id"],
        result={"content": "wrong"},
        extension_id="neovim",
        client_id="stale-client",
        session_name="editor-session",
    )
    assert tools_glue.resolve_extension_tool_result(
        event["call_id"],
        result={"content": "unsaved"},
        extension_id="neovim",
        client_id="nvim-client",
        session_name="editor-session",
    )
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert json.loads(output[0]) == {"content": "unsaved"}


def test_extension_dispatch_without_event_bus_fails_immediately():
    session = _session()
    result = tools_glue._dispatch_extension_tool(
        session,
        "neovim",
        {"client_id": "client"},
        "nvim_get_buffer",
        {},
    )
    assert "not connected to an event bus" in result


def test_webui_tool_approval_returns_session_contract_tuple():
    ui = object.__new__(WebUI)
    captured = {}

    def ask(payload):
        captured.update(payload)
        return {"approved": False, "reason": "Please keep the public API"}

    ui._ask_prompt = ask
    choice, reason = ui.request_tool_approval(
        tool_name="write_file",
        display_args={"filename": "a.py"},
        modifications=[
            {"filename": "a.py", "original_content": "a", "modified_content": "b"}
        ],
        can_approve=True,
    )
    assert (choice, reason) == ("e", "Please keep the public API")
    assert captured["shape"] == "tool_approval"
    assert captured["modifications"][0]["filename"] == "a.py"


def test_provider_switch_targets_requested_loaded_session(monkeypatch):
    target = _session()
    target.folder_context = None
    state = SimpleNamespace(
        session_by_name=lambda name=None: target if name == "editor-session" else None
    )
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    provider = SimpleNamespace(name="openai", model_name="gpt-test")
    monkeypatch.setattr(
        provider_router, "_safe_init", lambda *_args, **_kwargs: provider
    )

    result = asyncio.run(
        provider_router.switch_provider(
            provider_router.SwitchRequest(
                provider="openai", model="gpt-test", session_name="editor-session"
            ),
            request,
        )
    )

    assert result == {"ok": True, "provider": "openai", "model": "gpt-test"}
    assert target.provider is provider
    assert target.session_manager.provider_config == {
        "provider": "openai",
        "model": "gpt-test",
    }
    assert target.session_manager.saved == 1


def test_variable_persistence_locks_the_requested_session():
    target = _session()
    target.folder_context = None
    locks = []
    lock = threading.Lock()
    state = SimpleNamespace(
        session_lock_for=lambda name=None: locks.append(name) or lock,
    )
    request = SimpleNamespace(app=SimpleNamespace(state=state))

    result = asyncio.run(
        inspector_router.set_variable(
            "yolo",
            request,
            {"value": True},
            session=target,
        )
    )

    assert result == {"ok": True, "key": "yolo", "value": True}
    assert target.variables["yolo"] is True
    assert locks == ["editor-session"]


def test_ollama_model_discovery_uses_requested_session():
    target = SimpleNamespace(
        variables={
            "ollama_mode": "cloud",
            "ollama_host": "https://ollama.example",
            "ollama_api_key": "session-key",
        }
    )
    requested = []
    state = SimpleNamespace(
        session_by_name=lambda name=None: requested.append(name) or target
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=state),
        query_params={"session_name": "editor-session"},
    )

    overrides = provider_router._ollama_discovery_overrides(request)

    assert requested == ["editor-session"]
    assert overrides == {
        "ollama_mode": "cloud",
        "ollama_host": "https://ollama.example",
        "ollama_api_key": "session-key",
    }


def test_editor_request_is_history_free_and_records_usage():
    captured = {}

    class Provider:
        model_name = "unpriced-test-model"

        def generate(self, **kwargs):
            captured.update(kwargs)
            return ProviderResponse(
                text="<mucli-completion>value</mucli-completion>",
                parts=[],
                input_tokens=11,
                output_tokens=3,
                total_tokens=14,
            )

    target = _session(
        extensions={
            "neovim": {
                "client_id": "nvim-client",
                "system_prompt": "Use live editor state.",
            }
        }
    )
    target.provider = Provider()
    target.folder_context = None
    state = SimpleNamespace(
        session_by_name=lambda name=None: target if name == "editor-session" else None,
        session_busy_for=lambda _name: threading.Event(),
        session_lock_for=lambda _name: threading.Lock(),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=state))

    result = asyncio.run(
        extension_router.extension_request(
            "neovim",
            extension_router.ExtensionRequest(
                client_id="nvim-client",
                session_name="editor-session",
                kind="completion",
                prompt="Complete this buffer",
            ),
            request,
        )
    )

    assert result["text"] == "<mucli-completion>value</mucli-completion>"
    assert captured["tools"] is None
    assert captured["messages"][0].parts[0].text == "Complete this buffer"
    assert target.session_manager.token_counts["total"] == 14
    assert target.session_manager.saved == 1


def test_registration_validation_rejects_prefix_and_builtin_collisions(monkeypatch):
    from fastapi import HTTPException
    from mu.tools import descriptors

    invalid = extension_router.ExtensionRegister(
        extension_id="neovim",
        client_id="client",
        tools=[extension_router.ToolDefinition(name="other_tool")],
        tool_prefix="nvim_",
    )
    try:
        extension_router._validate_registration(invalid)
    except HTTPException as exc:
        assert exc.status_code == 400
    else:
        raise AssertionError("prefix mismatch should be rejected")

    invalid_kind = extension_router.ExtensionRegister(
        extension_id="neovim",
        client_id="client",
        tools=[
            extension_router.ToolDefinition(name="nvim_read", execution_kind="surprise")
        ],
        tool_prefix="nvim_",
    )
    try:
        extension_router._validate_registration(invalid_kind)
    except HTTPException as exc:
        assert exc.status_code == 400
    else:
        raise AssertionError("unknown execution kinds should be rejected")

    monkeypatch.setattr(
        descriptors,
        "TOOLS",
        [ToolDefinition("nvim_collision", "native", {"type": "object"})],
    )
    collision = extension_router.ExtensionRegister(
        extension_id="neovim",
        client_id="client",
        tools=[extension_router.ToolDefinition(name="nvim_collision")],
        tool_prefix="nvim_",
    )
    try:
        extension_router._validate_registration(collision)
    except HTTPException as exc:
        assert exc.status_code == 409
    else:
        raise AssertionError("built-in collision should be rejected")
