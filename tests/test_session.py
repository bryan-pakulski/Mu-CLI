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
