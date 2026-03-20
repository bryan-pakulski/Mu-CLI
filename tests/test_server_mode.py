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
from core.workspace import FolderContext
from core.feature_mode import create_feature_plan, update_feature_plan_metadata
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
    assert completed["result"]["result"]["telemetry"]["execution_source"] == "server"
    assert completed["result"]["result"]["modified_files"] == [str(target_file)]
    assert target_file.read_text(encoding="utf-8") == "updated\n"


def test_headless_tool_task_rejection_keeps_file_unchanged(tmp_path):
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
    approval_manager.resolve(task["approval_id"], "n")
    completed = task_manager.wait_for_task_state(
        task["task_id"], {"completed"}, timeout=5.0
    )

    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["result"]["result"]["raw"] == "User denied direct tool call: write_file"
    assert target_file.read_text(encoding="utf-8") == "before\n"


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


@dataclass
class DummyFeatureLoopProvider:
    directory: str
    phase_path: str
    name: str = "dummy"
    model_name: str = "dummy-feature-model"
    call_count: int = 0

    def get_available_models(self):
        return [self.model_name]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        self.call_count += 1
        if self.call_count == 1:
            phase_content = """# Phase 1: Build it\n\n## Objectives\n- [x] Understand scope\n\n## Action Points\n- [x] Implement the feature\n\n## Exit Criteria\n- [x] Confirm phase completion\n"""
            return ProviderResponse(
                text="",
                parts=[
                    MessagePart(
                        type="tool_call",
                        tool_name="write_file",
                        tool_args={"filename": self.phase_path, "content": phase_content},
                    )
                ],
                input_tokens=5,
                output_tokens=3,
                total_tokens=8,
            )
        if self.call_count == 2:
            return ProviderResponse(
                text="phase complete",
                parts=[MessagePart(type="text", text="phase complete")],
                input_tokens=3,
                output_tokens=2,
                total_tokens=5,
            )
        if self.call_count == 3:
            return ProviderResponse(
                text="",
                parts=[
                    MessagePart(
                        type="tool_call",
                        tool_name="update_feature_plan",
                        tool_args={
                            "directory": self.directory,
                            "review_status": "completed",
                            "review_notes": "All criteria satisfied.",
                        },
                    )
                ],
                input_tokens=5,
                output_tokens=3,
                total_tokens=8,
            )
        return ProviderResponse(
            text="review complete",
            parts=[MessagePart(type="text", text="review complete")],
            input_tokens=3,
            output_tokens=2,
            total_tokens=5,
        )

    def upload_file(self, file_path, mime_type):
        return None


def test_feature_loop_runs_until_review_completed(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    ctx = FolderContext()
    ctx.add_folder(str(workspace))
    create_feature_plan(
        feature_name="Feature Loop",
        feature_request="Implement looped feature delivery",
        phases=[
            {
                "title": "Build it",
                "objectives": ["Understand scope"],
                "action_points": ["Implement the feature"],
                "exit_criteria": ["Confirm phase completion"],
            }
        ],
        folder_context=ctx,
        feature_id="loop_test",
    )
    workspace_doc_dir = workspace / "documentation" / "feature_req_loop_test"
    update_feature_plan_metadata(str(workspace_doc_dir), approved=True)

    ui = HeadlessUI(auto_approve=True)
    provider = DummyFeatureLoopProvider(
        directory=str(workspace_doc_dir),
        phase_path=str(workspace_doc_dir / "phase_1.md"),
    )
    session = build_test_session(provider=provider, ui=ui)
    handle_command(session, f"/folder {workspace}", allow_prompt=False)

    event_hub = EventHub()
    task_manager = TaskManager(session, Lock(), event_hub=event_hub)
    approval_manager = ApprovalManager(task_manager, event_hub=event_hub)
    ui.bind_runtime(task_manager, approval_manager)

    task = task_manager.start_feature_task(str(workspace_doc_dir), approval_manager, max_cycles=4)
    completed = task_manager.wait_for_task_state(task["task_id"], {"completed", "error"}, timeout=10.0)

    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["result"]["mode"] == "feature"
    assert completed["result"]["feature_plan"]["review_status"] == "completed"
    assert completed["result"]["feature_plan"]["phases"][0]["status"] == "completed"


@dataclass
class DummyBlockingFeatureProvider:
    directory: str
    phase_path: str
    call_count: int = 0
    name: str = "dummy"
    model_name: str = "dummy-blocking-feature-model"

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
                        tool_name="raise_blocker",
                        tool_args={
                            "summary": "Need a product choice",
                            "details": "Implementation cannot continue until the user picks the target provider.",
                            "requested_input": "Choose whether the feature should use OpenAI or Gemini.",
                            "questions": ["Which provider should the new feature target?"],
                        },
                    )
                ],
                input_tokens=5,
                output_tokens=3,
                total_tokens=8,
            )
        if self.call_count == 2:
            return ProviderResponse(
                text="blocked pending user input",
                parts=[MessagePart(type="text", text="blocked pending user input")],
                input_tokens=3,
                output_tokens=2,
                total_tokens=5,
            )
        if self.call_count == 3:
            phase_content = """# Phase 1: Build it\n\n## Objectives\n- [x] Understand scope\n\n## Action Points\n- [x] Implement the feature\n\n## Exit Criteria\n- [x] Confirm phase completion\n\n## Notes\nUser selected OpenAI during blocker resolution.\n"""
            return ProviderResponse(
                text="",
                parts=[
                    MessagePart(
                        type="tool_call",
                        tool_name="write_file",
                        tool_args={"filename": self.phase_path, "content": phase_content},
                    )
                ],
                input_tokens=5,
                output_tokens=3,
                total_tokens=8,
            )
        if self.call_count == 4:
            return ProviderResponse(
                text="phase complete after unblock",
                parts=[MessagePart(type="text", text="phase complete after unblock")],
                input_tokens=3,
                output_tokens=2,
                total_tokens=5,
            )
        if self.call_count == 5:
            return ProviderResponse(
                text="",
                parts=[
                    MessagePart(
                        type="tool_call",
                        tool_name="update_feature_plan",
                        tool_args={
                            "directory": self.directory,
                            "review_status": "completed",
                            "review_notes": "Completed after blocker resolution.",
                        },
                    )
                ],
                input_tokens=5,
                output_tokens=3,
                total_tokens=8,
            )
        return ProviderResponse(
            text="review complete",
            parts=[MessagePart(type="text", text="review complete")],
            input_tokens=3,
            output_tokens=2,
            total_tokens=5,
        )

    def upload_file(self, file_path, mime_type):
        return None


def test_feature_loop_can_pause_on_blocker_and_resume(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    ctx = FolderContext()
    ctx.add_folder(str(workspace))
    create_feature_plan(
        feature_name="Blocking Feature Loop",
        feature_request="Implement looped feature delivery with blocker handling",
        phases=[
            {
                "title": "Build it",
                "objectives": ["Understand scope"],
                "action_points": ["Implement the feature"],
                "exit_criteria": ["Confirm phase completion"],
            }
        ],
        folder_context=ctx,
        feature_id="blocking_loop_test",
    )
    workspace_doc_dir = workspace / "documentation" / "feature_req_blocking_loop_test"
    update_feature_plan_metadata(str(workspace_doc_dir), approved=True)

    ui = HeadlessUI(auto_approve=True)
    provider = DummyBlockingFeatureProvider(
        directory=str(workspace_doc_dir),
        phase_path=str(workspace_doc_dir / "phase_1.md"),
    )
    session = build_test_session(provider=provider, ui=ui)
    handle_command(session, f"/folder {workspace}", allow_prompt=False)

    event_hub = EventHub()
    task_manager = TaskManager(session, Lock(), event_hub=event_hub)
    approval_manager = ApprovalManager(task_manager, event_hub=event_hub)
    ui.bind_runtime(task_manager, approval_manager)

    task = task_manager.start_feature_task(str(workspace_doc_dir), approval_manager, max_cycles=5)
    blocked = task_manager.wait_for_task_state(
        task["task_id"], {"awaiting_input", "error"}, timeout=10.0
    )

    assert blocked is not None
    assert blocked["status"] == "awaiting_input"
    assert blocked["blocker"]["summary"] == "Need a product choice"
    assert blocked["result"]["status"] == "awaiting_input"
    assert blocked["result"]["cycles"]

    resumed = task_manager.resume_feature_task(
        task["task_id"],
        "Use OpenAI for the new feature.",
        approval_manager,
    )
    assert resumed["status"] == "running"

    completed = task_manager.wait_for_task_state(
        task["task_id"], {"completed", "error", "awaiting_input"}, timeout=10.0
    )

    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["result"]["feature_plan"]["review_status"] == "completed"
    assert completed["result"]["feature_plan"]["phases"][0]["status"] == "completed"
    assert len(completed["result"]["cycles"]) >= 3
