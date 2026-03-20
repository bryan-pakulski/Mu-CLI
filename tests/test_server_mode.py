from dataclasses import dataclass
from threading import Lock
from uuid import uuid4

import pytest

from core.server import (
    ApprovalManager,
    EventHub,
    HeadlessUI,
    TaskManager,
    build_runtime_payload,
    build_sessions_payload,
    build_state_payload,
    build_workspace_payload,
    execute_server_tool,
)
from core.session import Session, SessionManager
from mucli import handle_command
from providers.base import MessagePart, ProviderResponse
from providers.ollama import OllamaProvider


@dataclass
class DummyProvider:
    name: str = "dummy"
    model_name: str = "dummy-model"

    def get_available_models(self):
        return [self.model_name]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        last_text = ""
        for message in reversed(messages):
            if message.role == "user":
                for part in message.parts:
                    if part.type == "text":
                        last_text = part.text
                        break
                break
        return ProviderResponse(
            text=f"echo: {last_text}",
            parts=[MessagePart(type="text", text=f"echo: {last_text}")],
            input_tokens=12,
            output_tokens=5,
            total_tokens=17,
        )

    def upload_file(self, file_path, mime_type):
        return None


@dataclass
class DummyToolProvider:
    target_file: str
    name: str = "dummy"
    model_name: str = "dummy-tool-model"
    call_count: int = 0

    def get_available_models(self):
        return [self.model_name]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        self.call_count += 1
        if self.call_count == 1:
            return ProviderResponse(
                text="",
                parts=[
                    MessagePart(
                        type="tool_call",
                        tool_name="write_file",
                        tool_args={
                            "filename": self.target_file,
                            "content": "updated\n",
                        },
                    )
                ],
                input_tokens=8,
                output_tokens=4,
                total_tokens=12,
            )
        return ProviderResponse(
            text="done after approval",
            parts=[MessagePart(type="text", text="done after approval")],
            input_tokens=6,
            output_tokens=3,
            total_tokens=9,
        )

    def upload_file(self, file_path, mime_type):
        return None


def build_test_session(provider=None, ui=None):
    ui = ui or HeadlessUI()
    session_manager = SessionManager(ui=ui, session_name=f"test_{uuid4().hex}")
    session_manager.provider_config = {"provider": "dummy", "model": "dummy-model"}
    return Session(
        provider=provider or DummyProvider(),
        thinking=False,
        system_instruction="test system prompt",
        session_manager=session_manager,
        ui=ui,
        debug=False,
    )


def test_send_message_returns_structured_turn_data():
    session = build_test_session()

    result = session.send_message("hello server")

    assert result["ok"] is True
    assert result["status"] == "completed"
    assert result["assistant_text"] == "echo: hello server"
    assert result["tokens"]["total"] == 17
    assert result["history_delta"][0]["role"] == "user"


def test_handle_command_updates_variables_non_interactively():
    session = build_test_session()

    result = handle_command(session, "/set yolo true", allow_prompt=False)

    assert result["ok"] is True
    assert session.variables["yolo"] is True
    assert result["data"]["key"] == "yolo"


def test_build_state_payload_includes_workspace_and_tools(tmp_path):
    session = build_test_session()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text("print('hi')\n", encoding="utf-8")

    result = handle_command(session, f"/folder {workspace}", allow_prompt=False)
    state = build_state_payload(session)

    assert result["ok"] is True
    assert str(workspace) in state["folders"]
    read_file_tool = next(
        tool for tool in state["available_tools"] if tool["name"] == "read_file"
    )
    assert read_file_tool["execution_kind"] == "read"
    assert read_file_tool["result_mode"] == "structured+collated"
    assert read_file_tool["server_policy"] == "allowed"


def test_runtime_and_sessions_payloads_reflect_state_changes(tmp_path):
    session = build_test_session()
    session.thinking = True
    session.agentic = False
    session.system_instruction = "updated system prompt"
    session.variables["yolo"] = True
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    handle_command(session, f"/folder {workspace}", allow_prompt=False)

    runtime = build_runtime_payload(session)
    sessions = build_sessions_payload(session)
    workspace_payload = build_workspace_payload(session)

    assert runtime["thinking"] is True
    assert runtime["agentic"] is False
    assert runtime["system_instruction"] == "updated system prompt"
    assert runtime["variables"]["yolo"] is True
    assert sessions["current_session_name"].startswith("test_")
    assert str(workspace) in workspace_payload["folders"]


def test_headless_approval_workflow_for_modifying_tool(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target_file = workspace / "note.txt"
    target_file.write_text("before\n", encoding="utf-8")

    ui = HeadlessUI()
    provider = DummyToolProvider(target_file=str(target_file))
    session = build_test_session(provider=provider, ui=ui)
    handle_command(session, f"/folder {workspace}", allow_prompt=False)

    event_hub = EventHub()
    task_manager = TaskManager(session, Lock(), event_hub=event_hub)
    approval_manager = ApprovalManager(task_manager, event_hub=event_hub)
    ui.bind_runtime(task_manager, approval_manager)
    subscriber_id, event_queue = event_hub.subscribe()

    task = task_manager.start_message_task("please update the file", approval_manager)
    task = task_manager.wait_for_task_state(
        task["task_id"], {"awaiting_approval"}, timeout=5.0
    )

    assert task is not None
    assert task["status"] == "awaiting_approval"
    assert task["approval_id"] is not None

    pending = approval_manager.list_pending()
    assert len(pending) == 1
    assert pending[0]["tool_name"] == "write_file"
    assert pending[0]["modifications"][0]["filename"] == str(target_file)
    assert pending[0]["preview_error"] is None
    assert pending[0]["error_code"] is None

    observed_events = []
    while not event_queue.empty():
        observed_events.append(event_queue.get()["event"])
    assert "task.created" in observed_events
    assert "approval.requested" in observed_events
    assert "task.awaiting_approval" in observed_events
    assert "trace.message" in observed_events
    assert "trace.info" in observed_events

    approval_manager.resolve(task["approval_id"], "y")
    completed = task_manager.wait_for_task_state(
        task["task_id"], {"completed"}, timeout=5.0
    )

    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["result"]["assistant_text"] == "done after approval"
    assert target_file.read_text(encoding="utf-8") == "updated\n"

    observed_events = []
    while not event_queue.empty():
        observed_events.append(event_queue.get()["event"])
    event_hub.unsubscribe(subscriber_id)

    assert "approval.resolved" in observed_events
    assert "task.completed" in observed_events
    assert "trace.tool" in observed_events
    assert "trace.tool_result" in observed_events


def test_headless_tool_task_requires_approval_for_direct_tool_calls(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target_file = workspace / "note.txt"
    target_file.write_text("before\n", encoding="utf-8")

    ui = HeadlessUI()
    session = build_test_session(ui=ui)
    handle_command(session, f"/folder {workspace}", allow_prompt=False)

    event_hub = EventHub()
    task_manager = TaskManager(session, Lock(), event_hub=event_hub)
    approval_manager = ApprovalManager(task_manager, event_hub=event_hub)
    ui.bind_runtime(task_manager, approval_manager)

    task = task_manager.start_tool_task(
        "write_file",
        {"filename": str(target_file), "content": "updated\n"},
        approval_manager,
    )
    task = task_manager.wait_for_task_state(
        task["task_id"], {"awaiting_approval"}, timeout=5.0
    )

    assert task is not None
    assert task["status"] == "awaiting_approval"

    pending = approval_manager.list_pending()
    assert len(pending) == 1
    assert pending[0]["tool_name"] == "write_file"
    assert pending[0]["modifications"][0]["filename"] == str(target_file)
    assert pending[0]["preview_error"] is None
    assert pending[0]["error_code"] is None

    approval_manager.resolve(task["approval_id"], "y")
    completed = task_manager.wait_for_task_state(
        task["task_id"], {"completed"}, timeout=5.0
    )

    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["result"]["ok"] is True
    assert completed["result"]["result"]["raw"] == f"Successfully wrote to {target_file}"
    assert target_file.read_text(encoding="utf-8") == "updated\n"


def test_execute_server_tool_blocks_session_only_tools():
    session = build_test_session()

    with pytest.raises(PermissionError):
        execute_server_tool(session, "flush", {})


def test_build_runtime_payload_syncs_saved_variables_into_ollama_provider():
    ui = HeadlessUI()
    session_manager = SessionManager(ui=ui, session_name=f"test_{uuid4().hex}")
    session_manager.provider_config = {"provider": "ollama", "model": "qwen3"}
    session_manager.variables["ollama_host"] = "http://example.local:11434"
    session = Session(
        provider=OllamaProvider(model_name="qwen3", host="http://localhost:11434"),
        thinking=False,
        system_instruction="test system prompt",
        session_manager=session_manager,
        ui=ui,
        debug=False,
    )

    runtime = build_runtime_payload(session)

    assert runtime["variables"]["ollama_host"] == "http://example.local:11434"
    assert session.provider.host == "http://example.local:11434"
