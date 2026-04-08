import os
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
    build_memory_buffers_payload,
    build_state_payload,
    build_workspace_payload,
    execute_server_tool,
)
from core.session import Session, SessionManager
from core.workspace import FolderContext
from core.feature_mode import create_feature_plan, update_feature_plan_metadata
from mucli import (
    build_feature_markdown,
    handle_command,
    refresh_feature_record,
    _feature_confirm_deny_edit_loop,
    get_feature_prompt_context,
    run_auto_update,
)
from providers.base import MessagePart, ProviderResponse
from providers.ollama import OllamaProvider
from utils.config import AGENT_MODE_METADATA


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


def test_mode_command_without_args_lists_available_modes():
    session = build_test_session()

    result = handle_command(session, "/mode", allow_prompt=False)

    assert result["ok"] is True
    assert result["data"]["current_mode"] == session.variables["agent_mode"]
    assert result["data"]["available_modes"] == AGENT_MODE_METADATA
    assert "feature" in result["data"]["available_modes"]
    assert (
        result["data"]["available_modes"]["feature"]["documentation"]
        == "documentation/feature_plan_engine.md"
    )


def test_stats_command_returns_session_snapshot():
    session = build_test_session()

    result = handle_command(session, "/stats", allow_prompt=False)

    assert result["ok"] is True
    assert result["data"]["history_turns"] == len(session.session_manager.history)
    assert "token_counts" in result["data"]
    assert "feature_state" in result["data"]


def test_memory_status_includes_hit_statistics():
    session = build_test_session()
    session.task_memory.save("remember auth flow", tags=["auth"])
    session.task_memory.search("auth", limit=1)
    session.turn_scratchpad.save("temporary todo", tags=["todo"])

    result = handle_command(session, "/memory status", allow_prompt=False)

    assert result["ok"] is True
    assert result["data"]["task_memory_stats"]["entries"] == 1
    assert result["data"]["task_memory_stats"]["total_hits"] >= 1
    assert result["data"]["task_memory_stats"]["top_entries"][0]["content"] == "remember auth flow"
    assert result["data"]["scratchpad_stats"]["entries"] == 1


def test_clear_command_only_resets_conversation_history(tmp_path, monkeypatch):
    monkeypatch.setattr("core.session.HISTORY_DIR", str(tmp_path / "history"))
    session = build_test_session()
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    session.session_manager.history = [
        {"role": "user", "parts": [{"type": "text", "text": "hello"}]}
    ]
    session.task_memory.save("remember this", tags=["fact"])
    session.turn_scratchpad.save("temporary note", tags=["temp"])
    session.collation_buffer.add("read_file", {"filename": "demo.txt"}, "payload")
    handle_command(session, f"/folder {workspace}", allow_prompt=False)
    handle_command(session, "/feature new Demo Feature", allow_prompt=False)
    session.staged_files = [{"file_ref": {"display_name": "demo.txt"}}]

    result = handle_command(session, "/clear", allow_prompt=False)

    assert result["ok"] is True
    assert session.session_manager.history == []
    assert session.task_memory.entries != []
    assert session.turn_scratchpad.entries != []
    assert session.collation_buffer.entries != []
    assert session.folder_context.folders != []
    assert session.session_manager.get_feature_state() is not None
    assert session.session_manager.list_features() != []
    assert session.staged_files != []


def test_memory_clear_accepts_scratch_alias():
    session = build_test_session()
    session.turn_scratchpad.save("temporary note", tags=["temp"])

    result = handle_command(session, "/memory clear scratch", allow_prompt=False)

    assert result["ok"] is True
    assert session.turn_scratchpad.entries == []


def test_workspace_clear_command_clears_folders(tmp_path):
    session = build_test_session()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    handle_command(session, f"/folder {workspace}", allow_prompt=False)

    result = handle_command(session, "/workspace clear", allow_prompt=False)

    assert result["ok"] is True
    assert session.folder_context.folders == []


def test_tokens_command_is_removed():
    session = build_test_session()

    result = handle_command(session, "/tokens", allow_prompt=False)

    assert result["ok"] is False
    assert result["message"] == "Unknown command: /tokens"


def test_update_command_runs_auto_update(monkeypatch):
    session = build_test_session()

    monkeypatch.setattr(
        "mucli.run_auto_update",
        lambda: {
            "ok": True,
            "message": "μCLI update completed successfully.",
            "steps": [{"name": "git pull --ff-only", "returncode": 0, "stderr": ""}],
        },
    )

    result = handle_command(session, "/update", allow_prompt=False)

    assert result["ok"] is True
    assert "update completed" in result["message"].lower()
    assert result["data"]["steps"][0]["name"] == "git pull --ff-only"


def test_run_auto_update_performs_git_pull(monkeypatch, tmp_path):
    calls = []
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("pytest\n", encoding="utf-8")

    class Result:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(command, cwd=None):
        calls.append((tuple(command), cwd))
        if command[:3] == ["git", "rev-parse", "--show-toplevel"]:
            return Result(stdout=str(tmp_path))
        if command[:3] == ["git", "pull", "--ff-only"]:
            return Result(stdout="Already up to date.")
        if command[0:3] == [os.sys.executable, "-m", "pip"]:
            return Result(stdout="Requirement already satisfied")
        return Result(returncode=1, stderr="unexpected command")

    monkeypatch.setattr("mucli._run_command", fake_run)

    result = run_auto_update()

    assert result["ok"] is True
    assert any(cmd[:3] == ("git", "pull", "--ff-only") for cmd, _ in calls)


def test_feature_commands_manage_session_scoped_features(tmp_path, monkeypatch):
    monkeypatch.setattr("core.session.HISTORY_DIR", str(tmp_path / "history"))
    session = build_test_session()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    handle_command(session, f"/folder {workspace}", allow_prompt=False)

    created = handle_command(
        session, "/feature new Stats Dashboard", allow_prompt=False
    )
    feature = created["data"]["feature"]

    assert created["ok"] is True
    assert feature["metadata_path"].startswith(
        str(tmp_path / "history" / "sessions")
    )
    assert feature["directory"].startswith(str(workspace / "documentation"))

    status = handle_command(session, "/feature status", allow_prompt=False)
    phases = handle_command(session, "/feature phases", allow_prompt=False)
    listed = handle_command(session, "/feature list", allow_prompt=False)
    loaded = handle_command(
        session,
        f"/feature load {feature['feature_id']}",
        allow_prompt=False,
    )
    deleted = handle_command(
        session,
        f"/feature delete {feature['feature_id']}",
        allow_prompt=False,
    )

    assert "# Feature: Stats Dashboard" in status["data"]["markdown"]
    assert "### Task Checklist" in phases["data"]["markdown"]
    assert listed["data"]["features"][0]["feature_id"] == feature["feature_id"]
    assert loaded["data"]["feature"]["feature_id"] == feature["feature_id"]
    assert deleted["ok"] is True
    assert session.session_manager.list_features() == []


def test_features_alias_routes_to_feature_command(tmp_path, monkeypatch):
    monkeypatch.setattr("core.session.HISTORY_DIR", str(tmp_path / "history"))
    session = build_test_session()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    handle_command(session, f"/folder {workspace}", allow_prompt=False)
    created = handle_command(
        session, "/feature new Alias Demo", allow_prompt=False
    )
    feature_id = created["data"]["feature"]["feature_id"]

    result = handle_command(session, f"/features load {feature_id}", allow_prompt=False)

    assert result["ok"] is True
    assert result["data"]["feature"]["feature_id"] == feature_id


def test_feature_cli_phase_five_command_surface(tmp_path, monkeypatch):
    monkeypatch.setattr("core.session.HISTORY_DIR", str(tmp_path / "history"))
    session = build_test_session()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    handle_command(session, f"/folder {workspace}", allow_prompt=False)

    help_result = handle_command(session, "/feature help", allow_prompt=False)
    assert help_result["ok"] is True
    assert "/feature create plan <name>" in help_result["data"]["usage"]

    plan = handle_command(session, "/feature create plan CLI Loop", allow_prompt=False)
    assert plan["ok"] is True

    phase = handle_command(
        session,
        "/feature create phase Build CLI Surface | Add command loop",
        allow_prompt=False,
    )
    assert phase["ok"] is True

    task = handle_command(
        session,
        "/feature create task 1 | Add command parser | Build parser | Help shown;Invalid guidance shown",
        allow_prompt=False,
    )
    assert task["ok"] is True

    moved = handle_command(session, "/feature move 1 in_progress", allow_prompt=False)
    assert moved["ok"] is True

    blocked = handle_command(session, "/feature block 1 waiting_for_input", allow_prompt=False)
    assert blocked["ok"] is True

    unblocked = handle_command(session, "/feature move 1 in_progress", allow_prompt=False)
    assert unblocked["ok"] is True

    completed = handle_command(session, "/feature move 1 completed", allow_prompt=False)
    assert completed["ok"] is True

    reviewed_auto = handle_command(session, "/feature review auto", allow_prompt=False)
    assert reviewed_auto["ok"] is True

    reviewed = handle_command(
        session, "/feature review 1 concise_summary", allow_prompt=False
    )
    assert reviewed["ok"] is True

    show_reviews = handle_command(session, "/feature show reviews", allow_prompt=False)
    assert show_reviews["ok"] is True
    assert show_reviews["data"]["review_count"] >= 1

    monitor = handle_command(session, "/feature monitor 0.5", allow_prompt=False)
    assert monitor["ok"] is True
    assert monitor["data"]["refresh_seconds"] == 0.5
    assert monitor["data"]["iterations"] == 1

    archived = handle_command(session, "/feature archive 1", allow_prompt=False)
    assert archived["ok"] is True
    assert archived["data"]["status"] == "archived"

    feature = refresh_feature_record(session, None)
    plan_data = (feature or {}).get("feature_plan", {})
    event_kinds = [
        item.get("kind")
        for item in plan_data.get("event_log", [])
        if isinstance(item, dict)
    ]
    assert "cli_prompt_selected" in event_kinds


def test_feature_unknown_command_returns_corrective_guidance():
    session = build_test_session()

    unknown = handle_command(session, "/feature wut", allow_prompt=False)

    assert unknown["ok"] is False
    assert "/feature help" in unknown["message"]


def test_feature_confirm_deny_edit_loop_supports_edit(monkeypatch):
    session = build_test_session()
    choices = iter(["edit", "confirm"])
    monkeypatch.setattr(
        "mucli._feature_three_option_prompt",
        lambda *args, **kwargs: next(choices),
    )
    monkeypatch.setattr("mucli.Prompt.ask", lambda *args, **kwargs: "Updated Title")

    result = _feature_confirm_deny_edit_loop(
        session,
        label="task title",
        value="Initial Title",
        allow_prompt=True,
    )

    assert result["decision"] == "confirm"
    assert result["value"] == "Updated Title"


def test_feature_exit_clears_only_active_feature_state(tmp_path, monkeypatch):
    monkeypatch.setattr("core.session.HISTORY_DIR", str(tmp_path / "history"))
    session = build_test_session()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    handle_command(session, f"/folder {workspace}", allow_prompt=False)
    created = handle_command(
        session, "/feature new Exit Demo", allow_prompt=False
    )
    feature_id = created["data"]["feature"]["feature_id"]

    result = handle_command(session, "/feature exit", allow_prompt=False)

    assert result["ok"] is True
    assert session.session_manager.get_feature_state() is None
    assert session.session_manager.active_feature_id is None
    assert session.session_manager.get_feature(feature_id) is not None


def test_build_feature_markdown_shows_task_snapshot():
    feature = {
        "feature_id": "demo",
        "feature_name": "Demo Feature",
        "status": "in_progress",
        "directory": "/tmp/demo",
        "metadata_path": "/tmp/demo.json",
        "started_at": 0,
        "start_tokens": 1000,
        "token_total": 30100,
        "feature_plan": {
            "approved": False,
            "review_status": "pending",
            "next_task": {"number": 2, "title": "Implement fixtures/pcap.py"},
            "phases": [
                {
                    "number": 1,
                    "title": "Implement fixtures/sipp.py",
                    "status": "completed",
                    "task_counts": {"completed": 3, "in_progress": 0, "not_started": 0},
                },
                {
                    "number": 2,
                    "title": "Implement fixtures/pcap.py",
                    "status": "in_progress",
                    "task_counts": {"completed": 1, "in_progress": 1, "not_started": 1},
                },
                {
                    "number": 3,
                    "title": "Implement test_basic_calls.py",
                    "status": "not_started",
                    "task_counts": {"completed": 0, "in_progress": 0, "not_started": 2},
                },
            ],
        },
    }

    markdown = build_feature_markdown(feature)

    assert "## Progress Snapshot" in markdown
    assert "Token delta:** ↓ 29.1k tokens" in markdown
    assert "### Active Work" in markdown
    assert "*Implementing Implement fixtures/pcap.py…" in markdown
    assert "- ✔ **Implement fixtures/sipp.py**" in markdown
    assert "- ◼ **Implement fixtures/pcap.py**" in markdown
    assert "- ◻ **Implement test_basic_calls.py**" in markdown


def test_get_feature_prompt_context_returns_task_and_progress():
    session = build_test_session()
    session.session_manager.set_feature_state(
        {
            "status": "in_progress",
            "feature_plan": {
                "next_task": {"number": 2, "title": "Implement fixtures/pcap.py"},
                "phases": [
                    {
                        "number": 1,
                        "title": "Implement fixtures/sipp.py",
                        "status": "completed",
                        "task_counts": {"completed": 3, "in_progress": 0, "not_started": 0},
                    },
                    {
                        "number": 2,
                        "title": "Implement fixtures/pcap.py",
                        "status": "in_progress",
                        "task_counts": {"completed": 1, "in_progress": 1, "not_started": 2},
                    },
                ],
            },
        }
    )

    context = get_feature_prompt_context(session)

    assert context["status"] == "in_progress"
    assert context["task"] == "Implement fixtures/pcap.py"
    assert context["phase_done"] == 1
    assert context["phase_total"] == 4
    assert context["overall_done"] == 1
    assert context["overall_total"] == 2


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


def test_build_memory_buffers_payload_exposes_saved_entries():
    session = build_test_session()
    session.session_manager.task_memory.save(
        "Remember phase 2 handoff details.",
        tags=["feature", "handoff"],
        source="test",
    )
    session.session_manager.turn_scratchpad.save(
        "Temporary blocker note.",
        tags=["blocker"],
        source="test",
    )

    payload = build_memory_buffers_payload(session)

    assert payload["memory_entries"]
    assert payload["memory_entries"][0]["content"]
    assert payload["scratchpad_entries"]
    assert payload["scratchpad_entries"][0]["content"]


def test_task_manager_cancel_task_marks_cancelled_and_blocks_completion():
    session = build_test_session()
    manager = TaskManager(session, Lock(), event_hub=EventHub())
    task_id = manager.create_task("message", {"text": "hello"})
    manager.set_running(task_id)

    cancelled = manager.cancel_task(task_id)
    manager.complete_task(task_id, {"ok": True})
    latest = manager.get_task(task_id)

    assert cancelled is not None
    assert cancelled["status"] == "cancelled"
    assert latest["status"] == "cancelled"


def test_runtime_and_sessions_payloads_reflect_state_changes(tmp_path):
    session = build_test_session()
    session.thinking = True
    session.agentic = False
    session.system_instruction = "updated system prompt"
    session.variables["yolo"] = True
    session.session_manager.set_feature_state(
        {"status": "awaiting_input", "type": "feature"}
    )
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
    assert runtime["feature_state"]["status"] == "awaiting_input"
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
    assert (
        completed["result"]["result"]["raw"] == f"Successfully wrote to {target_file}"
    )
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
    assert (
        completed["result"]["result"]["raw"]
        == "User denied direct tool call: write_file"
    )
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
    name: str = "dummy"
    model_name: str = "dummy-feature-model"
    call_count: int = 0

    def get_available_models(self):
        return [self.model_name]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        self.call_count += 1
        if self.call_count == 1:
            return ProviderResponse(
                text="implementation in progress",
                parts=[MessagePart(type="text", text="implementation in progress")],
                input_tokens=5,
                output_tokens=3,
                total_tokens=8,
            )
        if self.call_count == 2:
            return ProviderResponse(
                text="",
                parts=[
                    MessagePart(
                        type="tool_call",
                        tool_name="update_task_status",
                        tool_args={
                            "task_id": 1,
                            "status": "completed",
                            "notes": "Checklist validated and complete.",
                            "verified_exit_criteria": ["Confirm phase completion"],
                            "directory": self.directory,
                        },
                    )
                ],
                input_tokens=5,
                output_tokens=3,
                total_tokens=8,
            )
        if self.call_count == 3:
            return ProviderResponse(
                text="",
                parts=[
                    MessagePart(
                        type="tool_call",
                        tool_name="approve_feature_task",
                        tool_args={
                            "directory": self.directory,
                            "approved": True,
                            "review_status": "completed",
                            "review_notes": "All criteria satisfied.",
                        },
                    )
                ],
                input_tokens=5,
                output_tokens=3,
                total_tokens=8,
            )
        if self.call_count == 4:
            return ProviderResponse(
                text="phase complete",
                parts=[MessagePart(type="text", text="phase complete")],
                input_tokens=3,
                output_tokens=2,
                total_tokens=5,
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
    )
    session = build_test_session(provider=provider, ui=ui)
    handle_command(session, f"/folder {workspace}", allow_prompt=False)

    event_hub = EventHub()
    task_manager = TaskManager(session, Lock(), event_hub=event_hub)
    approval_manager = ApprovalManager(task_manager, event_hub=event_hub)
    ui.bind_runtime(task_manager, approval_manager)

    task = task_manager.start_feature_task(
        str(workspace_doc_dir), approval_manager, max_cycles=4
    )
    completed = task_manager.wait_for_task_state(
        task["task_id"], {"completed", "error"}, timeout=10.0
    )

    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["result"]["mode"] == "feature"
    assert completed["result"]["feature_plan"]["review_status"] == "completed"
    assert completed["result"]["feature_plan"]["phases"][0]["status"] == "completed"
    assert "transition_events" in completed["result"]["cycles"][0]


@dataclass
class DummyBlockingFeatureProvider:
    directory: str
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
                            "questions": [
                                "Which provider should the new feature target?"
                            ],
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
            return ProviderResponse(
                text="",
                parts=[
                    MessagePart(
                        type="tool_call",
                        tool_name="update_task_status",
                        tool_args={
                            "task_id": 1,
                            "status": "completed",
                            "notes": "Completed after unblock.",
                            "verified_exit_criteria": ["Confirm phase completion"],
                            "directory": self.directory,
                        },
                    )
                ],
                input_tokens=5,
                output_tokens=3,
                total_tokens=8,
            )
        if self.call_count == 4:
            return ProviderResponse(
                text="",
                parts=[
                    MessagePart(
                        type="tool_call",
                        tool_name="approve_feature_task",
                        tool_args={
                            "directory": self.directory,
                            "approved": True,
                            "review_status": "completed",
                            "review_notes": "Completed after blocker resolution.",
                        },
                    )
                ],
                input_tokens=5,
                output_tokens=3,
                total_tokens=8,
            )
        if self.call_count == 5:
            return ProviderResponse(
                text="phase complete after unblock",
                parts=[MessagePart(type="text", text="phase complete after unblock")],
                input_tokens=3,
                output_tokens=2,
                total_tokens=5,
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
    )
    session = build_test_session(provider=provider, ui=ui)
    handle_command(session, f"/folder {workspace}", allow_prompt=False)

    event_hub = EventHub()
    task_manager = TaskManager(session, Lock(), event_hub=event_hub)
    approval_manager = ApprovalManager(task_manager, event_hub=event_hub)
    ui.bind_runtime(task_manager, approval_manager)

    task = task_manager.start_feature_task(
        str(workspace_doc_dir), approval_manager, max_cycles=5
    )
    blocked = task_manager.wait_for_task_state(
        task["task_id"], {"awaiting_input", "error"}, timeout=10.0
    )

    assert blocked is not None
    assert blocked["status"] == "awaiting_input"
    assert blocked["blocker"]["summary"] == "Need a product choice"
    assert blocked["result"]["status"] == "awaiting_input"
    assert blocked["result"]["cycles"]
    assert blocked["result"]["feature_plan"]["phases"][0]["status"] == "blocked"

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
    all_events = [
        evt
        for cycle in completed["result"]["cycles"]
        for evt in cycle.get("transition_events", [])
    ]
    assert all_events
    assert len(completed["result"]["cycles"]) >= 2


def test_feature_loop_pauses_cleanly_when_interrupted(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    ctx = FolderContext()
    ctx.add_folder(str(workspace))
    create_feature_plan(
        feature_name="Interrupted Feature Loop",
        feature_request="Pause on interrupt and resume later",
        phases=[
            {
                "title": "Build it",
                "objectives": ["Understand scope"],
                "action_points": ["Implement the feature"],
                "exit_criteria": ["Confirm phase completion"],
            }
        ],
        folder_context=ctx,
        feature_id="interrupted_loop_test",
    )
    workspace_doc_dir = workspace / "documentation" / "feature_req_interrupted_loop_test"
    update_feature_plan_metadata(str(workspace_doc_dir), approved=True)

    ui = HeadlessUI(auto_approve=True)
    session = build_test_session(ui=ui)
    handle_command(session, f"/folder {workspace}", allow_prompt=False)

    session.send_message = lambda _prompt: {
        "ok": False,
        "status": "interrupted",
        "assistant_text": "",
    }

    event_hub = EventHub()
    task_manager = TaskManager(session, Lock(), event_hub=event_hub)
    approval_manager = ApprovalManager(task_manager, event_hub=event_hub)
    ui.bind_runtime(task_manager, approval_manager)

    task = task_manager.start_feature_task(
        str(workspace_doc_dir), approval_manager, max_cycles=3
    )
    interrupted = task_manager.wait_for_task_state(
        task["task_id"], {"awaiting_input", "error"}, timeout=10.0
    )

    assert interrupted is not None
    assert interrupted["status"] == "awaiting_input"
    assert interrupted["blocker"]["summary"] == "Feature loop interrupted during execution."
    assert interrupted["result"]["status"] == "interrupted"


def test_feature_loop_state_persists_across_session_reload(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    ctx = FolderContext()
    ctx.add_folder(str(workspace))
    create_feature_plan(
        feature_name="Persistent Feature Loop",
        feature_request="Resume after restarting the app",
        phases=[
            {
                "title": "Build it",
                "objectives": ["Understand scope"],
                "action_points": ["Implement the feature"],
                "exit_criteria": ["Confirm phase completion"],
            }
        ],
        folder_context=ctx,
        feature_id="persistent_loop_test",
    )
    workspace_doc_dir = workspace / "documentation" / "feature_req_persistent_loop_test"
    update_feature_plan_metadata(str(workspace_doc_dir), approved=True)

    initial_ui = HeadlessUI(auto_approve=True)
    provider = DummyBlockingFeatureProvider(
        directory=str(workspace_doc_dir),
    )
    session = build_test_session(provider=provider, ui=initial_ui)
    handle_command(session, f"/folder {workspace}", allow_prompt=False)

    initial_event_hub = EventHub()
    initial_task_manager = TaskManager(session, Lock(), event_hub=initial_event_hub)
    initial_approval_manager = ApprovalManager(
        initial_task_manager, event_hub=initial_event_hub
    )
    initial_ui.bind_runtime(initial_task_manager, initial_approval_manager)

    task = initial_task_manager.start_feature_task(
        str(workspace_doc_dir), initial_approval_manager, max_cycles=5
    )
    blocked = initial_task_manager.wait_for_task_state(
        task["task_id"], {"awaiting_input", "error"}, timeout=10.0
    )

    assert blocked is not None
    assert blocked["status"] == "awaiting_input"
    persisted_state = session.session_manager.get_feature_state()
    assert persisted_state is not None
    assert persisted_state["status"] == "awaiting_input"
    assert persisted_state["directory"] == str(workspace_doc_dir)

    reloaded_ui = HeadlessUI(auto_approve=True)
    reloaded_manager = SessionManager(
        ui=reloaded_ui, session_name=session.session_manager.current_session_name
    )
    reloaded_manager.provider_config = {
        "provider": "dummy",
        "model": provider.model_name,
    }
    reloaded_session = Session(
        provider=provider,
        thinking=False,
        system_instruction="test system prompt",
        session_manager=reloaded_manager,
        ui=reloaded_ui,
        debug=False,
    )

    reloaded_event_hub = EventHub()
    reloaded_task_manager = TaskManager(
        reloaded_session, Lock(), event_hub=reloaded_event_hub
    )
    reloaded_approval_manager = ApprovalManager(
        reloaded_task_manager, event_hub=reloaded_event_hub
    )
    reloaded_ui.bind_runtime(reloaded_task_manager, reloaded_approval_manager)

    restored_task = reloaded_task_manager.get_task(task["task_id"])
    assert restored_task is not None
    assert restored_task["status"] == "awaiting_input"

    resumed = reloaded_task_manager.resume_feature_task(
        task["task_id"],
        "Use OpenAI for the resumed feature implementation.",
        reloaded_approval_manager,
    )
    assert resumed["status"] == "running"

    completed = reloaded_task_manager.wait_for_task_state(
        task["task_id"], {"completed", "error", "awaiting_input"}, timeout=10.0
    )

    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["result"]["feature_plan"]["review_status"] == "completed"
    assert reloaded_session.session_manager.get_feature_state()["status"] == "completed"
