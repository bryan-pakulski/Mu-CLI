import os
import json

import pytest
from core.approval import ApprovalPlan
from core.feature_mode import (
    create_feature_plan,
    summarize_feature_plan,
    update_feature_plan_metadata,
)
from core.session import Session, SessionManager
from providers.base import LLMProvider, MessagePart, ProviderResponse


class DummyProvider(LLMProvider):
    def get_available_models(self):
        return ["dummy"]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        return ProviderResponse(
            text="dummy", parts=[], input_tokens=10, output_tokens=10, total_tokens=20
        )

    def upload_file(self, file_path, mime_type):
        return None


def test_build_messages_from_history():
    sm = SessionManager()
    session = Session(DummyProvider("dummy"), False, "system instruction", sm)

    history_dicts = [
        {
            "role": "assistant",
            "parts": [
                {
                    "type": "tool_call",
                    "tool_name": "read_file",
                    "tool_args": {"filename": "test.txt"},
                }
            ],
        },
        {
            "role": "tool",
            "parts": [
                {
                    "type": "tool_result",
                    "tool_name": "read_file",
                    "tool_result": "file contents",
                }
            ],
        },
    ]

    new_user_msg = {"role": "user", "parts": [{"type": "text", "text": "hello"}]}

    messages = session._build_messages_from_history(history_dicts, new_user_msg)

    assert len(messages) == 3

    # Check serialization accuracy
    assert messages[0].role == "assistant"
    assert messages[0].parts[0].type == "tool_call"
    assert messages[0].parts[0].tool_name == "read_file"

    assert messages[1].role == "tool"
    assert messages[1].parts[0].type == "tool_result"
    assert messages[1].parts[0].tool_result == "file contents"

    assert messages[2].role == "user"
    assert messages[2].parts[0].type == "text"
    assert messages[2].parts[0].text == "hello"


def test_prepare_runtime_history_compresses_old_tool_messages():
    sm = SessionManager()
    session = Session(DummyProvider("dummy"), False, "system instruction", sm)
    session.variables["tool_context_window"] = 2

    sm.history = [
        {"role": "user", "parts": [{"type": "text", "text": "Implement feature"}]},
        {
            "role": "assistant",
            "parts": [
                {
                    "type": "tool_call",
                    "tool_name": "read_file",
                    "tool_args": {"filename": "a.py"},
                }
            ],
        },
        {
            "role": "tool",
            "parts": [
                {
                    "type": "tool_result",
                    "tool_name": "read_file",
                    "tool_result": "alpha",
                }
            ],
        },
        {
            "role": "assistant",
            "parts": [
                {
                    "type": "tool_call",
                    "tool_name": "search_for_string",
                    "tool_args": {"string": "beta"},
                }
            ],
        },
        {
            "role": "tool",
            "parts": [
                {
                    "type": "tool_result",
                    "tool_name": "search_for_string",
                    "tool_result": "beta result",
                }
            ],
        },
    ]

    prepared = session._prepare_runtime_history(turn_start_index=0)

    assert prepared[0]["role"] == "user"
    assert prepared[1]["role"] == "system"
    assert "LAYER 4 — Recent tool activity (compressed for budget)." in prepared[1]["parts"][0]["text"]
    assert len(prepared) == 4


def test_roll_history_summary_keeps_recent_turns_and_persists_summary():
    sm = SessionManager()
    session = Session(DummyProvider("dummy"), False, "system instruction", sm)

    sm.history = [
        {"role": "user", "parts": [{"type": "text", "text": "turn 1"}]},
        {"role": "assistant", "parts": [{"type": "text", "text": "answer 1"}]},
        {"role": "user", "parts": [{"type": "text", "text": "turn 2"}]},
        {"role": "assistant", "parts": [{"type": "text", "text": "answer 2"}]},
        {"role": "user", "parts": [{"type": "text", "text": "turn 3"}]},
        {"role": "assistant", "parts": [{"type": "text", "text": "answer 3"}]},
    ]

    changed = sm.roll_history_summary(4)

    assert changed is True
    assert sm.summary_anchor == 2
    assert "turn 1" in sm.conversation_summary
    assert "answer 1" in sm.conversation_summary
    assert [msg["parts"][0]["text"] for msg in session._prepare_runtime_history()] == [
        "turn 2",
        "answer 2",
        "turn 3",
        "answer 3",
    ]


def test_roll_history_summary_to_token_budget_summarizes_when_over_budget():
    sm = SessionManager()
    session = Session(DummyProvider("dummy"), False, "system instruction", sm)
    sm.history = [
        {"role": "user", "parts": [{"type": "text", "text": "x" * 1200}]},
        {"role": "assistant", "parts": [{"type": "text", "text": "y" * 1200}]},
        {"role": "user", "parts": [{"type": "text", "text": "z" * 1200}]},
        {"role": "assistant", "parts": [{"type": "text", "text": "w" * 1200}]},
    ]

    changed = sm.roll_history_summary_to_token_budget(200, keep_recent=2)

    assert changed is True
    assert sm.summary_anchor > 0
    assert "Summarized conversation" in sm.conversation_summary


def test_roll_history_summary_to_token_budget_clips_large_payload_when_stuck():
    sm = SessionManager()
    Session(DummyProvider("dummy"), False, "system instruction", sm)
    sm.summary_anchor = 0
    sm.history = [
        {"role": "assistant", "parts": [{"type": "tool_result", "tool_name": "tool", "tool_result": "A" * 20000}]}
    ]

    changed = sm.roll_history_summary_to_token_budget(200, keep_recent=1, max_passes=4)

    assert changed is True
    payload = sm.history[0]["parts"][0]["tool_result"]
    assert "truncated_to_4000_chars_for_context_budget" in payload


def test_clip_conversation_summary_marks_truncation_boundary():
    sm = SessionManager()
    sm.conversation_summary = "header\n" + ("x" * 5000)

    sm._clip_conversation_summary(limit=200)

    assert sm.conversation_summary.startswith(
        "[conversation_summary_truncated_to_last_200_chars]"
    )


def test_send_message_injects_hierarchical_context_layers():
    class CaptureProvider(LLMProvider):
        def __init__(self):
            super().__init__("dummy")
            self.last_system_prompt = ""

        def get_available_models(self):
            return ["dummy"]

        def generate(self, messages, system_prompt=None, thinking=False, tools=None):
            self.last_system_prompt = system_prompt or ""
            return ProviderResponse(
                text="done",
                parts=[MessagePart(type="text", text="done")],
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
            )

        def upload_file(self, file_path, mime_type):
            return None

    provider = CaptureProvider()
    sm = SessionManager(session_name="rolling-summary")
    session = Session(provider, False, "system instruction", sm)
    session.variables["context_token_limit"] = 32
    session.variables["context_trim_threshold"] = 0.5
    sm.history = [
        {"role": "user", "parts": [{"type": "text", "text": "turn 1 " + ("a" * 800)}]},
        {
            "role": "assistant",
            "parts": [{"type": "text", "text": "answer 1 " + ("b" * 800)}],
        },
        {"role": "user", "parts": [{"type": "text", "text": "turn 2 " + ("c" * 800)}]},
        {
            "role": "assistant",
            "parts": [{"type": "text", "text": "answer 2 " + ("d" * 800)}],
        },
        {"role": "user", "parts": [{"type": "text", "text": "turn 3 " + ("e" * 800)}]},
        {
            "role": "assistant",
            "parts": [{"type": "text", "text": "answer 3 " + ("f" * 800)}],
        },
    ]

    session.send_message("turn 4")

    assert "Hierarchical runtime context" in provider.last_system_prompt
    assert "LAYER 2 — Conversation summary:" in provider.last_system_prompt
    assert "LAYER 5 — Current turn:" in provider.last_system_prompt
    assert "turn 1" in provider.last_system_prompt
    assert sm.summary_anchor == 2
    assert "turn 1" in sm.conversation_summary


def test_layered_context_prefers_retrieved_snippets(tmp_path):
    class CaptureProvider(LLMProvider):
        def __init__(self):
            super().__init__("dummy")
            self.last_system_prompt = ""

        def get_available_models(self):
            return ["dummy"]

        def generate(self, messages, system_prompt=None, thinking=False, tools=None):
            self.last_system_prompt = system_prompt or ""
            return ProviderResponse(
                text="done",
                parts=[MessagePart(type="text", text="done")],
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
            )

        def upload_file(self, file_path, mime_type):
            return None

    (tmp_path / "auth.py").write_text(
        "def authenticate_user(token):\n    return token == 'ok'\n"
    )
    (tmp_path / "billing.py").write_text(
        "def charge_card(amount):\n    return amount\n"
    )

    sm = SessionManager(session_name="retrieval-layered")
    sm.folder_context.add_folder(str(tmp_path))
    provider = CaptureProvider()
    session = Session(provider, False, "system instruction", sm)
    session.variables["retrieval_top_k"] = 2
    session.send_message("where is authentication token validated?")

    assert "LAYER 4B — Retrieved workspace snippets:" in provider.last_system_prompt
    assert "auth.py" in provider.last_system_prompt


def test_layer_budgets_eviction_policies():
    sm = SessionManager()
    session = Session(DummyProvider("dummy"), False, "system instruction", sm)
    session.variables["conversation_summary_char_limit"] = 20
    session.variables["recent_tool_context_char_limit"] = 40
    session.variables["retrieval_context_char_limit"] = 30
    sm.conversation_summary = "0123456789abcdefghijklmnopqrstuvwxyz"
    session._pending_retrieved_context = "x" * 200
    layered = session._inject_hierarchical_context("system instruction")

    assert "[budget: 20 chars | eviction: keep newest]" in layered
    assert "[budget: 40 chars | eviction: drop oldest tool records]" not in layered
    assert "LAYER 4B — Retrieved workspace snippets:" in layered
    assert "[budget: 30 chars | eviction: drop lowest-ranked snippets]" in layered


def test_prepare_runtime_history_keeps_signed_tool_messages():
    sm = SessionManager()
    session = Session(DummyProvider("dummy"), False, "system instruction", sm)
    session.variables["tool_context_window"] = 2

    sm.history = [
        {"role": "user", "parts": [{"type": "text", "text": "Investigate bug"}]},
        {
            "role": "assistant",
            "parts": [
                {
                    "type": "tool_call",
                    "tool_name": "read_file",
                    "tool_args": {"filename": "a.py"},
                    "thought_signature": "abc123",
                }
            ],
        },
        {
            "role": "tool",
            "parts": [
                {
                    "type": "tool_result",
                    "tool_name": "read_file",
                    "tool_result": "alpha",
                    "thought_signature": "abc123",
                }
            ],
        },
        {
            "role": "assistant",
            "parts": [
                {
                    "type": "tool_call",
                    "tool_name": "search_for_string",
                    "tool_args": {"string": "beta"},
                }
            ],
        },
        {
            "role": "tool",
            "parts": [
                {
                    "type": "tool_result",
                    "tool_name": "search_for_string",
                    "tool_result": "beta result",
                }
            ],
        },
        {
            "role": "assistant",
            "parts": [
                {
                    "type": "tool_call",
                    "tool_name": "list_dir",
                    "tool_args": {"path": "."},
                }
            ],
        },
        {
            "role": "tool",
            "parts": [
                {
                    "type": "tool_result",
                    "tool_name": "list_dir",
                    "tool_result": "file.py",
                }
            ],
        },
    ]

    prepared = session._prepare_runtime_history(turn_start_index=0)

    signed_messages = [
        msg
        for msg in prepared
        if any(part.get("thought_signature") for part in msg.get("parts", []))
    ]
    assert len(signed_messages) == 2
    assert signed_messages[0]["parts"][0]["tool_name"] == "read_file"
    assert not any(
        msg.get("role") == "system" and "read_file" in msg["parts"][0]["text"]
        for msg in prepared
    )


def test_session_sync_runtime_state_rebinds_memory_stores():
    sm = SessionManager()
    session = Session(DummyProvider("dummy"), False, "system instruction", sm)

    original_memory = session.task_memory
    original_scratchpad = session.turn_scratchpad

    sm.new_session(name="fresh-session", provider_name="dummy", model_name="dummy")
    session.sync_runtime_state()

    assert session.task_memory is sm.task_memory
    assert session.turn_scratchpad is sm.turn_scratchpad
    assert session.task_memory is not original_memory
    assert session.turn_scratchpad is not original_scratchpad


def test_collated_structured_result_omits_source_blob(tmp_path, monkeypatch):
    sample = tmp_path / "sample.txt"
    sample.write_text("important line\n" * 50)
    monkeypatch.setattr("core.session.HISTORY_DIR", str(tmp_path / "history"))

    class SequencedProvider(LLMProvider):
        def __init__(self):
            super().__init__("dummy")
            self.responses = [
                ProviderResponse(
                    text="",
                    parts=[
                        MessagePart(
                            type="tool_call",
                            tool_name="read_file",
                            tool_args={"filename": str(sample)},
                        )
                    ],
                    input_tokens=1,
                    output_tokens=1,
                    total_tokens=2,
                ),
                ProviderResponse(
                    text="done",
                    parts=[MessagePart(type="text", text="done")],
                    input_tokens=1,
                    output_tokens=1,
                    total_tokens=2,
                ),
            ]

        def get_available_models(self):
            return ["dummy"]

        def generate(self, messages, system_prompt=None, thinking=False, tools=None):
            self.last_system_prompt = system_prompt or ""
            return self.responses.pop(0)

        def upload_file(self, file_path, mime_type):
            return None

    sm = SessionManager(session_name="collation-test")
    session = Session(SequencedProvider(), False, "system instruction", sm)
    session.folder_context.add_folder(str(tmp_path))
    session.sync_runtime_state()

    session.send_message("inspect the file")

    tool_message = next(msg for msg in reversed(sm.history) if msg["role"] == "tool")
    tool_result = tool_message["parts"][0]["tool_result"]

    assert tool_result["data"]["collated"] is True
    assert tool_result["raw"].startswith(
        "Stored 'read_file' result in collation buffer"
    )
    assert "important line" not in tool_result["raw"]
    assert tool_result["data"]["source_line_count"] == 50


def test_memory_round_trip_via_session_manager(tmp_path, monkeypatch):
    monkeypatch.setattr("core.session.HISTORY_DIR", str(tmp_path))

    sm = SessionManager(session_name="memory_test")
    entry = sm.task_memory.save("Important fact", tags=["fact"], source="unit-test")
    sm.save_history()

    restored = SessionManager(session_name="memory_test")
    matches = restored.task_memory.search("important", limit=1)

    assert matches
    assert matches[0].id == entry.id
    assert matches[0].source == "unit-test"


def test_execute_tool_with_memory_handles_scratchpad():
    sm = SessionManager()
    session = Session(DummyProvider("dummy"), False, "system instruction", sm)

    save_result = session._execute_tool_with_memory(
        "save_scratchpad",
        {"content": "temporary plan", "tags": ["plan"], "source": "test"},
    )
    search_result = session._execute_tool_with_memory(
        "search_scratchpad",
        {"query": "temporary", "limit": 5},
    )

    assert "Saved scratchpad note" in save_result
    assert "temporary plan" in search_result


def test_build_structured_tool_result_includes_normalized_error_code():
    sm = SessionManager()
    session = Session(DummyProvider("dummy"), False, "system instruction", sm)

    structured = session._build_structured_tool_result(
        "read_file",
        {"filename": "missing.txt"},
        "Error: Access denied or file ignored. 'missing.txt' is outside boundaries or in ignore list.",
    )

    assert structured["ok"] is False
    assert structured["error_code"] == "access_denied"
    assert structured["error"]["code"] == "access_denied"


def test_build_structured_tool_result_tracks_modified_files():
    sm = SessionManager()
    session = Session(DummyProvider("dummy"), False, "system instruction", sm)

    structured = session._build_structured_tool_result(
        "write_file",
        {"filename": "note.txt"},
        "Successfully wrote to note.txt",
    )

    assert structured["modified_files"] == ["note.txt"]
    assert structured["artifacts"] == []
    assert structured["telemetry"]["delivery_mode"] == "structured"


def test_send_message_resets_scratchpad_each_turn():
    sm = SessionManager()
    session = Session(DummyProvider("dummy"), False, "system instruction", sm)
    session.turn_scratchpad.save("old note", tags=["temp"])

    session.send_message("hello")

    assert session.turn_scratchpad.list_entries() == []


def test_send_message_feature_mode_injects_phased_plan_guidance(tmp_path):
    class CaptureProvider(LLMProvider):
        def __init__(self):
            super().__init__("dummy")
            self.last_user_text = ""
            self.last_system_prompt = ""

        def get_available_models(self):
            return ["dummy"]

        def generate(self, messages, system_prompt=None, thinking=False, tools=None):
            self.last_system_prompt = system_prompt or ""
            for message in reversed(messages):
                if message.role == "user":
                    for part in message.parts:
                        if part.type == "text":
                            self.last_user_text = part.text
                            break
                    break
            return ProviderResponse(
                text="planned",
                parts=[MessagePart(type="text", text="planned")],
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
            )

        def upload_file(self, file_path, mime_type):
            return None

    provider = CaptureProvider()
    sm = SessionManager(session_name="feature-mode-prompt")
    session = Session(provider, False, "system instruction", sm)
    session.folder_context.add_folder(str(tmp_path))
    session.sync_runtime_state()
    session.variables["agent_mode"] = "feature"

    session.send_message("Implement an approvals dashboard")

    assert "create_feature_task" in provider.last_user_text
    assert "tool calls only" in provider.last_user_text
    assert (
        "Use update_task_status/approve_feature_task/get_tasks/get_current_task exclusively to read or change task status"
        in provider.last_user_text
    )
    assert "EXIT CRITERIA" in provider.last_user_text
    assert "status='completed'" in provider.last_user_text
    assert "Do not create alternate planning documents" in provider.last_user_text
    assert (
        "do not begin code implementation until the user has reviewed and approved the plan"
        in provider.last_user_text
    )
    assert "use save_scratchpad for temporary phase notes" in provider.last_user_text
    assert (
        "call flush before acting on the collected context" in provider.last_user_text
    )
    assert "call raise_blocker" in provider.last_user_text
    assert "FEATURE MODE SYSTEM PROMPT" in provider.last_system_prompt
    assert "You are in Feature Plan Engine mode" in provider.last_system_prompt
    assert "gather read-only context first" in provider.last_system_prompt
    assert provider.last_user_text.endswith("Implement an approvals dashboard")


def test_feature_mode_blocks_direct_feature_plan_access(tmp_path, monkeypatch):
    monkeypatch.setattr("core.session.HISTORY_DIR", str(tmp_path / "history"))
    sm = SessionManager(session_name="feature-md-block")
    session = Session(DummyProvider("dummy"), False, "system instruction", sm)
    session.folder_context.add_folder(str(tmp_path))
    session.sync_runtime_state()
    session.variables["agent_mode"] = "feature"
    plan_path = tmp_path / "documentation" / "feature_req_demo" / "feature_plan.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text("{}", encoding="utf-8")
    session.session_manager.set_feature_state(
        {
            "type": "feature",
            "status": "running",
            "directory": str(plan_path.parent),
            "feature_plan": {"phases": []},
        },
        session.folder_context,
    )

    result = session._execute_tool_with_memory(
        "read_file",
        {"filename": str(plan_path)},
    )

    assert str(result).startswith("Error: Feature status files are managed")
    assert "update_task_status" in str(result)


def test_sync_feature_state_tracks_feature_plan_tool_results(tmp_path, monkeypatch):
    monkeypatch.setattr("core.session.HISTORY_DIR", str(tmp_path / "history"))
    sm = SessionManager(session_name="feature-state-tool-sync")
    session = Session(DummyProvider("dummy"), False, "system instruction", sm)
    session.folder_context.add_folder(str(tmp_path))
    session.sync_runtime_state()

    plan = create_feature_plan(
        feature_name="Feature state sync",
        feature_request="Track feature plan progress in the session state.",
        phases=[
            {
                "title": "Plan",
                "objectives": ["Create the plan"],
                "action_points": ["Refresh persisted state"],
                "exit_criteria": ["State summary exists"],
            }
        ],
        folder_context=session.folder_context,
    )
    summary = summarize_feature_plan(plan)

    assert sm.get_feature_state() is None

    session._sync_feature_state_for_tool(
        "get_tasks",
        {"directory": plan.directory},
        raw_result=summary,
        structured_result={"ok": True, "data": summary},
    )

    feature_state = sm.get_feature_state()
    assert feature_state is not None
    assert feature_state["type"] == "feature"
    assert feature_state["directory"] == plan.directory
    assert feature_state["feature_plan"]["feature_id"] == summary["feature_id"]
    assert feature_state["status"] == "awaiting_approval"


def test_get_current_task_sync_does_not_drop_feature_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr("core.session.HISTORY_DIR", str(tmp_path / "history"))
    sm = SessionManager(session_name="feature-state-current-task")
    session = Session(DummyProvider("dummy"), False, "system instruction", sm)
    session.folder_context.add_folder(str(tmp_path))
    session.sync_runtime_state()

    plan = create_feature_plan(
        feature_name="Feature state sync",
        feature_request="Track feature plan metadata stability.",
        phases=[
            {
                "title": "Phase 1",
                "objectives": ["Gather context"],
                "action_points": ["Update status"],
                "exit_criteria": ["Task done"],
            }
        ],
        folder_context=session.folder_context,
    )
    summary = summarize_feature_plan(plan)
    session._sync_feature_state_for_tool(
        "create_feature_task",
        {},
        raw_result={"plan": summary},
        structured_result={"ok": True, "data": {"plan": summary}},
    )

    session._sync_feature_state_for_tool(
        "get_current_task",
        {},
        raw_result={"feature_id": summary["feature_id"], "task": {"id": 1}},
        structured_result={
            "ok": True,
            "data": {"feature_id": summary["feature_id"], "task": {"id": 1}},
        },
    )

    feature_state = sm.get_feature_state()
    assert feature_state is not None
    assert feature_state["metadata_path"] == summary["metadata_path"]
    assert feature_state["feature_plan"]["feature_id"] == summary["feature_id"]


def test_wrapped_plan_payloads_update_feature_state(tmp_path, monkeypatch):
    monkeypatch.setattr("core.session.HISTORY_DIR", str(tmp_path / "history"))
    sm = SessionManager(session_name="feature-state-wrapped-plan")
    session = Session(DummyProvider("dummy"), False, "system instruction", sm)
    session.folder_context.add_folder(str(tmp_path))
    session.sync_runtime_state()

    plan = create_feature_plan(
        feature_name="Wrapped payload state sync",
        feature_request="Ensure wrapped plan payloads sync state.",
        phases=[
            {
                "title": "Phase 1",
                "objectives": ["Gather context"],
                "action_points": ["Update status"],
                "exit_criteria": ["Task done"],
            }
        ],
        folder_context=session.folder_context,
    )
    summary = summarize_feature_plan(plan)
    session._sync_feature_state_for_tool(
        "create_feature_task",
        {},
        raw_result={"ok": True, "feature_id": summary["feature_id"], "plan": summary},
        structured_result={
            "ok": True,
            "data": {"ok": True, "feature_id": summary["feature_id"], "plan": summary},
        },
    )

    approved_summary = {**summary, "approved": True, "review_status": "pending"}
    session._sync_feature_state_for_tool(
        "approve_feature_task",
        {},
        raw_result={
            "ok": True,
            "approved": True,
            "feature_id": summary["feature_id"],
            "plan": approved_summary,
        },
        structured_result={
            "ok": True,
            "data": {
                "ok": True,
                "approved": True,
                "feature_id": summary["feature_id"],
                "plan": approved_summary,
            },
        },
    )

    feature_state = sm.get_feature_state()
    assert feature_state is not None
    assert feature_state["feature_plan"]["approved"] is True
    assert feature_state["feature_plan"]["feature_id"] == summary["feature_id"]


def test_sync_feature_state_refreshes_after_feature_task_status_change(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("core.session.HISTORY_DIR", str(tmp_path / "history"))
    sm = SessionManager(session_name="feature-state-refresh")
    session = Session(DummyProvider("dummy"), False, "system instruction", sm)
    session.folder_context.add_folder(str(tmp_path))
    session.sync_runtime_state()

    plan = create_feature_plan(
        feature_name="Feature refresh",
        feature_request="Refresh plan status after status tool updates.",
        phases=[
            {
                "title": "Phase 1",
                "objectives": ["Ship the implementation"],
                "action_points": ["Update task status"],
                "exit_criteria": ["The phase is complete"],
            }
        ],
        folder_context=session.folder_context,
    )
    plan = update_feature_plan_metadata(plan.directory, approved=True)
    session._set_feature_state(
        feature_plan=summarize_feature_plan(plan), status="running"
    )

    updated_summary = summarize_feature_plan(plan)
    updated_summary["tasks"][0]["status"] = "completed"
    updated_summary["phases"][0]["status"] = "completed"
    updated_summary["next_task"] = None
    updated_summary["next_phase"] = None
    updated_summary["tasks_completed"] = True
    updated_summary["phases_completed"] = True

    session._sync_feature_state_for_tool(
        "update_task_status",
        {"task_id": 1, "status": "completed"},
        raw_result=updated_summary,
        structured_result={
            "ok": True,
            "data": updated_summary,
        },
    )

    feature_state = sm.get_feature_state()
    assert feature_state is not None
    assert feature_state["feature_plan"]["phases"][0]["status"] == "completed"
    assert feature_state["feature_plan"]["next_phase"] is None
    assert feature_state["status"] == "review"


def test_summarize_feature_plan_uses_task_status_for_task_counts(tmp_path):
    plan = create_feature_plan(
        feature_name="Feature summary",
        feature_request="Summarize task status without markdown files.",
        phases=[
            {
                "title": "Phase 1",
                "objectives": ["Ship implementation"],
                "action_points": ["Implement feature"],
                "exit_criteria": ["Confirm implementation"],
                "status": "in_progress",
            }
        ],
        folder_context=None,
    )

    summary = summarize_feature_plan(plan)

    assert summary["tasks"][0]["status"] == "in_progress"
    assert summary["phases"][0]["status"] == "in_progress"
    assert summary["phases"][0]["task_counts"] == {
        "not_started": 0,
        "in_progress": 1,
        "completed": 0,
    }


def test_mid_loop_yolo_toggle_skips_remaining_approvals(tmp_path, monkeypatch):
    monkeypatch.setattr("core.session.HISTORY_DIR", str(tmp_path / "history"))

    class SequencedProvider(LLMProvider):
        def __init__(self):
            super().__init__("dummy")
            self.responses = [
                ProviderResponse(
                    text="",
                    parts=[
                        MessagePart(
                            type="tool_call",
                            tool_name="write_file",
                            tool_args={
                                "filename": str(tmp_path / "one.txt"),
                                "content": "one",
                            },
                        ),
                        MessagePart(
                            type="tool_call",
                            tool_name="write_file",
                            tool_args={
                                "filename": str(tmp_path / "two.txt"),
                                "content": "two",
                            },
                        ),
                    ],
                    input_tokens=1,
                    output_tokens=1,
                    total_tokens=2,
                ),
                ProviderResponse(
                    text="done",
                    parts=[MessagePart(type="text", text="done")],
                    input_tokens=1,
                    output_tokens=1,
                    total_tokens=2,
                ),
            ]

        def get_available_models(self):
            return ["dummy"]

        def generate(self, messages, system_prompt=None, thinking=False, tools=None):
            return self.responses.pop(0)

        def upload_file(self, file_path, mime_type):
            return None

    class ApprovalToggleUI:
        def __init__(self):
            self.prompt_count = 0
            self.variables = None

        def set_variables(self, variables_dict):
            self.variables = variables_dict

        def request_tool_approval(self, **kwargs):
            self.prompt_count += 1
            self.variables["yolo"] = True
            return "y", None

        def show_info(self, message):
            return None

        def show_error(self, message):
            return None

        def show_tool_result(self, result):
            return None

        def render_message(self, role, content, model_name=None):
            return None

        def show_status(self, message):
            class _Status:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, exc_type, exc, tb):
                    return False

            return _Status()

    provider = SequencedProvider()
    ui = ApprovalToggleUI()
    sm = SessionManager(session_name="mid-loop-yolo")
    session = Session(provider, False, "system instruction", sm, ui=ui)
    session.folder_context.add_folder(str(tmp_path))
    session.sync_runtime_state()
    ui.set_variables(session.variables)

    approval_plan = ApprovalPlan(
        tool_name="write_file",
        tool_args={},
        requires_approval=True,
        can_approve=True,
        modifications=[],
    )
    monkeypatch.setattr(
        "core.session.collect_approval_plans",
        lambda tool_calls, folder_context, strict_mode=False, yolo=False: {
            0: approval_plan,
            1: approval_plan,
        },
    )

    executed = []

    def fake_execute(tool_name, tool_args, *, invocation_source="session"):
        executed.append((tool_name, tool_args["filename"]))
        return f"executed {tool_args['filename']}"

    monkeypatch.setattr(session, "_execute_tool_with_memory", fake_execute)

    session.send_message("do both writes")

    assert ui.prompt_count == 1
    assert session.variables["yolo"] is True
    assert executed == [
        ("write_file", str(tmp_path / "one.txt")),
        ("write_file", str(tmp_path / "two.txt")),
    ]


def test_send_message_persists_feature_state_to_session_json(tmp_path, monkeypatch):
    monkeypatch.setattr("core.session.HISTORY_DIR", str(tmp_path / "history"))

    class SequencedProvider(LLMProvider):
        def __init__(self):
            super().__init__("dummy")
            self.responses = [
                ProviderResponse(
                    text="",
                    parts=[
                        MessagePart(
                            type="tool_call",
                            tool_name="create_feature_task",
                            tool_args={
                                "feature_name": "Persistent feature state",
                                "feature_request": "Persist feature state to the session JSON.",
                                "tasks": [
                                    {
                                        "title": "Phase 1",
                                        "objectives": ["Plan the work"],
                                        "action_points": ["Write the feature plan"],
                                        "exit_criteria": ["A plan exists on disk"],
                                    }
                                ],
                            },
                        )
                    ],
                    input_tokens=1,
                    output_tokens=1,
                    total_tokens=2,
                ),
                ProviderResponse(
                    text="planned",
                    parts=[MessagePart(type="text", text="planned")],
                    input_tokens=1,
                    output_tokens=1,
                    total_tokens=2,
                ),
            ]

        def get_available_models(self):
            return ["dummy"]

        def generate(self, messages, system_prompt=None, thinking=False, tools=None):
            return self.responses.pop(0)

        def upload_file(self, file_path, mime_type):
            return None

    sm = SessionManager(session_name="feature-state-persisted")
    session = Session(SequencedProvider(), False, "system instruction", sm)
    session.folder_context.add_folder(str(tmp_path))
    session.sync_runtime_state()
    session.variables["agent_mode"] = "feature"

    session.send_message("Implement the feature workflow")

    session_json = (
        tmp_path / "history" / "sessions" / "feature-state-persisted" / "session.json"
    )
    assert session_json.exists()

    saved = json.loads(session_json.read_text())
    feature_state = saved.get("feature_state")
    assert feature_state is not None
    assert feature_state["type"] == "feature"
    assert feature_state["status"] == "awaiting_approval"
    assert feature_state["feature_plan"]["feature_name"] == "Persistent feature state"


def test_create_feature_plan_tool_stores_metadata_outside_repo(tmp_path, monkeypatch):
    monkeypatch.setattr("core.session.HISTORY_DIR", str(tmp_path / "history"))

    class SequencedProvider(LLMProvider):
        def __init__(self):
            super().__init__("dummy")
            self.responses = [
                ProviderResponse(
                    text="",
                    parts=[
                        MessagePart(
                            type="tool_call",
                            tool_name="create_feature_task",
                            tool_args={
                                "feature_name": "Externalized feature metadata",
                                "feature_request": "Keep feature JSON under session metadata.",
                                "tasks": [
                                    {
                                        "title": "Plan",
                                        "objectives": ["Capture requirements"],
                                        "action_points": ["Write the plan"],
                                        "exit_criteria": ["Plan exists"],
                                    }
                                ],
                            },
                        )
                    ],
                    input_tokens=1,
                    output_tokens=1,
                    total_tokens=2,
                ),
                ProviderResponse(
                    text="planned",
                    parts=[MessagePart(type="text", text="planned")],
                    input_tokens=1,
                    output_tokens=1,
                    total_tokens=2,
                ),
            ]

        def get_available_models(self):
            return ["dummy"]

        def generate(self, messages, system_prompt=None, thinking=False, tools=None):
            return self.responses.pop(0)

        def upload_file(self, file_path, mime_type):
            return None

    sm = SessionManager(session_name="feature-metadata-location")
    session = Session(SequencedProvider(), False, "system instruction", sm)
    session.folder_context.add_folder(str(tmp_path))
    session.sync_runtime_state()
    session.variables["agent_mode"] = "feature"

    session.send_message("Create the feature plan")

    feature_state = sm.get_feature_state()

    assert feature_state is not None
    assert feature_state["metadata_path"].startswith(
        str(tmp_path / "history" / "sessions")
    )
    assert os.path.exists(feature_state["metadata_path"])
    assert not os.path.exists(
        os.path.join(feature_state["directory"], "feature_plan.json")
    )
    assert not os.path.exists(os.path.join(feature_state["directory"], "phase_1.md"))


def test_session_manager_can_rename_session(tmp_path, monkeypatch):
    monkeypatch.setattr("core.session.HISTORY_DIR", str(tmp_path / "history"))
    sm = SessionManager(session_name="rename-src")
    sm.new_session(name="rename-src", provider_name="dummy", model_name="dummy")
    sm.new_session(name="rename-dst", provider_name="dummy", model_name="dummy")
    sm.switch_session("rename-src")

    sm.rename_session("rename-src", "renamed-session")

    sessions = sm.get_session_list()
    assert "rename-src" not in sessions
    assert "renamed-session" in sessions
