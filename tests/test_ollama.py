import pytest
from providers.ollama import OllamaProvider
from providers.base import Message, MessagePart


def test_convert_messages_tool_calls():
    provider = OllamaProvider("dummy")

    # Test formatting an assistant message making a tool call
    msgs = [
        Message(
            role="assistant",
            parts=[
                MessagePart(
                    type="tool_call",
                    tool_name="read_file",
                    tool_args={"filename": "test.txt"},
                )
            ],
        )
    ]

    converted = provider._convert_messages(msgs)

    assert len(converted) == 1
    assert converted[0]["role"] == "assistant"
    assert "tool_calls" in converted[0]

    tool_call = converted[0]["tool_calls"][0]
    assert tool_call["function"]["name"] == "read_file"
    assert tool_call["function"]["arguments"] == {"filename": "test.txt"}


def test_convert_messages_tool_results():
    provider = OllamaProvider("dummy")

    # Test formatting a tool result going back to the model
    msgs = [
        Message(
            role="tool",
            parts=[
                MessagePart(
                    type="tool_result",
                    tool_name="read_file",
                    tool_result="File contents here",
                )
            ],
        )
    ]

    converted = provider._convert_messages(msgs)

    assert len(converted) == 1
    assert converted[0]["role"] == "tool"
    assert converted[0]["content"] == "File contents here"
