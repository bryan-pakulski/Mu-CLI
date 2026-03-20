import pytest
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
            "parts": [{"type": "tool_call", "tool_name": "read_file", "tool_args": {"filename": "a.py"}}],
        },
        {
            "role": "tool",
            "parts": [{"type": "tool_result", "tool_name": "read_file", "tool_result": "alpha"}],
        },
        {
            "role": "assistant",
            "parts": [{"type": "tool_call", "tool_name": "search_for_string", "tool_args": {"string": "beta"}}],
        },
        {
            "role": "tool",
            "parts": [{"type": "tool_result", "tool_name": "search_for_string", "tool_result": "beta result"}],
        },
    ]

    prepared = session._prepare_runtime_history(turn_start_index=0)

    assert prepared[0]["role"] == "user"
    assert prepared[1]["role"] == "system"
    assert "Compressed prior tool activity" in prepared[1]["parts"][0]["text"]
    assert len(prepared) == 4




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
            "parts": [{"type": "tool_call", "tool_name": "search_for_string", "tool_args": {"string": "beta"}}],
        },
        {
            "role": "tool",
            "parts": [{"type": "tool_result", "tool_name": "search_for_string", "tool_result": "beta result"}],
        },
        {
            "role": "assistant",
            "parts": [{"type": "tool_call", "tool_name": "list_dir", "tool_args": {"path": "."}}],
        },
        {
            "role": "tool",
            "parts": [{"type": "tool_result", "tool_name": "list_dir", "tool_result": "file.py"}],
        },
    ]

    prepared = session._prepare_runtime_history(turn_start_index=0)

    signed_messages = [
        msg for msg in prepared if any(part.get("thought_signature") for part in msg.get("parts", []))
    ]
    assert len(signed_messages) == 2
    assert signed_messages[0]["parts"][0]["tool_name"] == "read_file"
    assert not any(
        msg.get("role") == "system"
        and "read_file" in msg["parts"][0]["text"]
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
    assert tool_result["raw"].startswith("Stored 'read_file' result in collation buffer")
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


def test_build_structured_tool_result_and_auto_promote():
    sm = SessionManager()
    session = Session(DummyProvider("dummy"), False, "system instruction", sm)
    session.variables["auto_promote_memory"] = True
    session.variables["auto_promote_max_per_turn"] = 4

    structured = session._build_structured_tool_result(
        "search_for_string",
        {"string": "needle"},
        "/tmp/a.py:10 -> needle here\n/tmp/b.py:22 -> another needle",
    )
    promoted = session._maybe_auto_promote_memory(structured)

    assert structured["data"]["match_count"] == 2
    assert structured["data"]["file_count"] == 2
    assert structured["telemetry"]["execution_source"] == "session"
    assert structured["artifacts"] == []
    assert promoted
    memory_results = session.task_memory.search("needle", limit=5)
    assert memory_results


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

    assert "create_feature_plan" in provider.last_user_text
    assert "phase_N.md" in provider.last_user_text
    assert "Do not create alternate planning documents" in provider.last_user_text
    assert "do not begin code implementation until the user has reviewed and approved the plan" in provider.last_user_text
    assert "call raise_blocker" in provider.last_user_text
    assert "FEATURE MODE SYSTEM PROMPT" in provider.last_system_prompt
    assert "You are in Feature Plan Engine mode" in provider.last_system_prompt
    assert provider.last_user_text.endswith("Implement an approvals dashboard")
