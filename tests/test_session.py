import pytest
from core.session import Session, SessionManager
from providers.base import LLMProvider, ProviderResponse


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
    assert promoted
    memory_results = session.task_memory.search("needle", limit=5)
    assert memory_results


def test_send_message_resets_scratchpad_each_turn():
    sm = SessionManager()
    session = Session(DummyProvider("dummy"), False, "system instruction", sm)
    session.turn_scratchpad.save("old note", tags=["temp"])

    session.send_message("hello")

    assert session.turn_scratchpad.list_entries() == []
