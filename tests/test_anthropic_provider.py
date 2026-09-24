"""Anthropic (Claude) provider: request shaping, tool-id pairing, thinking
replay, streaming event translation, and harness wiring."""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import httpx2
import pytest

import anthropic

from providers.anthropic import (
    AnthropicProvider,
    _decode_thinking,
    _default_max_tokens,
    _encode_thinking,
    _media_block,
    _model_version,
    _normalise_effort,
    _thinking_mode,
)
from providers.base import (
    CacheHint,
    LLMProvider,
    MediaData,
    Message,
    MessagePart,
    ToolDefinition,
)


PNG = b"\x89PNG\r\n\x1a\npixels"


# ------------------------------------------------------------------ fixtures


class _FakeStream:
    def __init__(self, events, final=None):
        self._events = list(events)
        self._final = final
        self.closed = False

    def __iter__(self):
        return iter(self._events)

    def get_final_message(self):
        if self._final is None:
            raise RuntimeError("no final message")
        return self._final

    def close(self):
        self.closed = True


class _FakeManager:
    def __init__(self, stream):
        self._stream = stream

    def __enter__(self):
        return self._stream

    def __exit__(self, *exc):
        self._stream.close()
        return False


class _FakeMessages:
    def __init__(self, responses):
        # Each entry is either a _FakeStream or an exception to raise.
        self._responses = list(responses)
        self.requests = []

    def stream(self, **kwargs):
        self.requests.append(kwargs)
        item = self._responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return _FakeManager(item)


class _FakeClient:
    def __init__(self, responses):
        self.messages = _FakeMessages(responses)
        self.models = SimpleNamespace(
            list=lambda: [SimpleNamespace(id="claude-opus-5"), SimpleNamespace(id="other")],
            retrieve=lambda name: SimpleNamespace(max_input_tokens=1_000_000, max_tokens=128_000),
        )


def _provider(model: str = "claude-opus-5", responses=()) -> AnthropicProvider:
    provider = AnthropicProvider.__new__(AnthropicProvider)
    LLMProvider.__init__(provider, model)
    provider.name = "anthropic"
    provider.timeout_seconds = 600.0
    provider._client = _FakeClient(responses)
    provider._model_info_cache = {}
    return provider


def _bad_request(message: str) -> anthropic.BadRequestError:
    response = httpx2.Response(
        400, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    )
    return anthropic.BadRequestError(message, response=response, body=None)


def _delta(index, **fields):
    return SimpleNamespace(type="content_block_delta", index=index, delta=SimpleNamespace(**fields))


def _tool_turn_events(*, thinking_signature="sig-1"):
    """A Claude turn: thinking -> text -> one tool_use, with usage."""
    return [
        SimpleNamespace(
            type="message_start",
            message=SimpleNamespace(
                usage=SimpleNamespace(
                    input_tokens=100, cache_read_input_tokens=400, cache_creation_input_tokens=50
                )
            ),
        ),
        SimpleNamespace(
            type="content_block_start", index=0,
            content_block=SimpleNamespace(type="thinking", thinking="", signature=""),
        ),
        _delta(0, type="thinking_delta", thinking="Let me look."),
        _delta(0, type="signature_delta", signature=thinking_signature),
        SimpleNamespace(type="content_block_stop", index=0, content_block=None),
        SimpleNamespace(
            type="content_block_start", index=1,
            content_block=SimpleNamespace(type="text", text=""),
        ),
        _delta(1, type="text_delta", text="Reading the file."),
        SimpleNamespace(type="content_block_stop", index=1, content_block=None),
        SimpleNamespace(
            type="content_block_start", index=2,
            content_block=SimpleNamespace(type="tool_use", id="toolu_01", name="read_file", input={}),
        ),
        _delta(2, type="input_json_delta", partial_json='{"path": '),
        _delta(2, type="input_json_delta", partial_json='"a.py"}'),
        SimpleNamespace(
            type="content_block_stop", index=2,
            content_block=SimpleNamespace(type="tool_use", id="toolu_01", name="read_file", input={"path": "a.py"}),
        ),
        SimpleNamespace(
            type="message_delta",
            delta=SimpleNamespace(stop_reason="tool_use"),
            usage=SimpleNamespace(
                output_tokens=42, input_tokens=None, cache_read_input_tokens=None,
                cache_creation_input_tokens=None,
                output_tokens_details=SimpleNamespace(thinking_tokens=7),
            ),
        ),
        SimpleNamespace(type="message_stop"),
    ]


def _final(stop_reason="tool_use", stop_details=None):
    return SimpleNamespace(
        stop_reason=stop_reason,
        stop_details=stop_details,
        usage=SimpleNamespace(
            input_tokens=100, cache_read_input_tokens=400, cache_creation_input_tokens=50,
            output_tokens=42,
        ),
    )


# ------------------------------------------------------------ model metadata


@pytest.mark.parametrize(
    "model, version, mode",
    [
        ("claude-opus-5", (5, 0), "adaptive"),
        ("claude-fable-5-1", (5, 1), "adaptive"),
        ("claude-opus-4-6", (4, 6), "adaptive"),
        ("claude-sonnet-4-6", (4, 6), "adaptive"),
        ("claude-haiku-4-5", (4, 5), "budget"),
        ("claude-opus-4-1-20250805", (4, 1), "budget"),
        ("claude-sonnet-4-20250514", (4, 0), "budget"),
        ("claude-3-7-sonnet-20250219", (3, 7), "budget"),
        ("claude-3-5-haiku-20241022", (3, 5), "none"),
        ("anthropic.claude-opus-4-8", (4, 8), "adaptive"),
        ("not-a-claude-model", (0, 0), "none"),
    ],
)
def test_model_version_and_thinking_mode(model, version, mode):
    assert _model_version(model) == version
    assert _thinking_mode(model) == mode


def test_default_max_tokens_by_generation():
    assert _default_max_tokens("claude-opus-5") == 64000
    assert _default_max_tokens("claude-haiku-4-5") == 32000
    assert _default_max_tokens("claude-3-5-haiku-20241022") == 8192


def test_effort_normalisation():
    assert _normalise_effort("xhigh", "claude-opus-4-6") == "high"
    assert _normalise_effort("xhigh", "claude-opus-4-7") == "xhigh"
    assert _normalise_effort("MAX", "claude-opus-5") == "max"
    assert _normalise_effort("turbo", "claude-opus-5") is None
    assert _normalise_effort(None, "claude-opus-5") is None


def test_env_max_tokens_and_models_api_cap(monkeypatch):
    provider = _provider("claude-opus-5")
    provider._client.models = SimpleNamespace(
        retrieve=lambda name: SimpleNamespace(max_input_tokens=200_000, max_tokens=32_000),
        list=lambda: [],
    )
    assert provider._resolve_max_tokens("claude-opus-5") == 32_000
    assert provider.effective_context_window() == 200_000
    assert provider.effective_response_reserve() == 32_000
    monkeypatch.setenv("ANTHROPIC_MAX_TOKENS", "9000")
    assert provider._resolve_max_tokens("claude-opus-5") == 9000


def test_context_window_falls_back_to_pricing_registry():
    provider = _provider("claude-haiku-4-5")
    provider._client.models = SimpleNamespace(
        retrieve=lambda name: (_ for _ in ()).throw(RuntimeError("offline")),
        list=lambda: [],
    )
    assert provider.effective_context_window() == 200_000


def test_available_models_filters_to_claude_and_falls_back():
    provider = _provider()
    assert provider.get_available_models() == ["claude-opus-5"]
    provider._client.models = SimpleNamespace(
        list=lambda: (_ for _ in ()).throw(RuntimeError("offline")),
        retrieve=lambda name: None,
    )
    assert "claude-opus-5" in provider.get_available_models()


def test_constructor_requires_credentials(monkeypatch):
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE",
                "ANTHROPIC_FEDERATION_RULE_ID"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", "/nonexistent-config-root-for-tests")
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        AnthropicProvider(model_name="claude-opus-5")
    provider = AnthropicProvider(model_name="claude-opus-5", api_key="sk-ant-test")
    assert provider.name == "anthropic"
    assert provider.model_name == "claude-opus-5"


# ------------------------------------------------------- message conversion


def test_tool_ids_are_synthesized_and_paired_deterministically():
    provider = _provider()
    messages = [
        Message(role="user", parts=[MessagePart(type="text", text="Read a.py")]),
        Message(
            role="assistant",
            parts=[
                MessagePart(type="text", text="Reading."),
                MessagePart(type="tool_call", tool_name="read_file", tool_args={"path": "a.py"}),
                MessagePart(type="tool_call", tool_name="read_file", tool_args={"path": "b.py"}),
            ],
        ),
        Message(
            role="tool",
            parts=[
                MessagePart(type="tool_result", tool_name="read_file", tool_result="A"),
                MessagePart(type="tool_result", tool_name="read_file", tool_result={"k": 1}),
            ],
        ),
        Message(role="user", parts=[MessagePart(type="text", text="Thanks")]),
    ]
    converted = provider._convert_messages(messages)
    assert [m["role"] for m in converted] == ["user", "assistant", "user"]
    tool_uses = [b for b in converted[1]["content"] if b["type"] == "tool_use"]
    assert [b["id"] for b in tool_uses] == ["mucli_toolu_1", "mucli_toolu_2"]
    results = converted[2]["content"]
    assert results[0] == {"type": "tool_result", "tool_use_id": "mucli_toolu_1", "content": "A"}
    assert results[1]["tool_use_id"] == "mucli_toolu_2"
    assert json.loads(results[1]["content"]) == {"k": 1}
    # The trailing user text merges after the tool results.
    assert results[2] == {"type": "text", "text": "Thanks"}
    # Same history -> identical request (prompt-cache stability).
    assert provider._convert_messages(messages) == converted


def test_interrupted_tool_call_gets_error_result_before_next_turn():
    provider = _provider()
    messages = [
        Message(role="user", parts=[MessagePart(type="text", text="go")]),
        Message(role="assistant", parts=[MessagePart(type="tool_call", tool_name="shell", tool_args={})]),
        Message(role="user", parts=[MessagePart(type="text", text="never mind")]),
    ]
    converted = provider._convert_messages(messages)
    assert converted[2]["role"] == "user"
    first, second = converted[2]["content"]
    assert first["type"] == "tool_result" and first["tool_use_id"] == "mucli_toolu_1"
    assert first["is_error"] is True
    assert second == {"type": "text", "text": "never mind"}


def test_orphan_tool_result_becomes_plain_text():
    provider = _provider()
    messages = [
        Message(role="tool", parts=[MessagePart(type="tool_result", tool_name="grep", tool_result="hit")]),
        Message(role="user", parts=[MessagePart(type="text", text="continue")]),
    ]
    converted = provider._convert_messages(messages)
    assert converted[0]["role"] == "user"
    assert converted[0]["content"][0]["type"] == "text"
    assert "grep" in converted[0]["content"][0]["text"]
    assert converted[0]["content"][1] == {"type": "text", "text": "continue"}


def test_leading_assistant_turn_gets_a_user_opener_and_empty_text_is_dropped():
    provider = _provider()
    messages = [
        Message(role="assistant", parts=[MessagePart(type="text", text="hello")]),
        Message(role="user", parts=[MessagePart(type="text", text="   ")]),
        Message(role="system", parts=[MessagePart(type="text", text="note")]),
    ]
    converted = provider._convert_messages(messages)
    assert converted[0]["role"] == "user"
    assert converted[1] == {"role": "assistant", "content": [{"type": "text", "text": "hello"}]}
    assert converted[2] == {"role": "user", "content": [{"type": "text", "text": "note"}]}


def test_thinking_blocks_replay_only_on_last_tool_turn():
    provider = _provider()
    sig_old = _encode_thinking([{"type": "thinking", "thinking": "old", "signature": "s-old"}])
    sig_new = _encode_thinking(
        [
            {"type": "thinking", "thinking": "new", "signature": "s-new"},
            {"type": "redacted_thinking", "data": "blob"},
        ]
    )
    messages = [
        Message(role="user", parts=[MessagePart(type="text", text="go")]),
        Message(role="assistant", parts=[
            MessagePart(type="tool_call", tool_name="a", tool_args={}, thought_signature=sig_old),
        ]),
        Message(role="tool", parts=[MessagePart(type="tool_result", tool_name="a", tool_result="1")]),
        Message(role="assistant", parts=[
            MessagePart(type="text", text="next"),
            MessagePart(type="tool_call", tool_name="b", tool_args={}, thought_signature=sig_new),
        ]),
        Message(role="tool", parts=[MessagePart(type="tool_result", tool_name="b", tool_result="2")]),
    ]
    converted = provider._convert_messages(messages, replay_thinking=True)
    older = converted[1]["content"]
    assert [b["type"] for b in older] == ["tool_use"]
    latest = converted[3]["content"]
    assert [b["type"] for b in latest] == ["thinking", "redacted_thinking", "text", "tool_use"]
    assert latest[0] == {"type": "thinking", "thinking": "new", "signature": "s-new"}

    stripped = provider._convert_messages(messages, replay_thinking=False)
    assert [b["type"] for b in stripped[3]["content"]] == ["text", "tool_use"]


def test_gemini_style_hex_signature_is_ignored_by_decoder():
    assert _decode_thinking("deadbeef") == []
    assert _decode_thinking(None) == []
    assert _decode_thinking('{"other": 1}') == []
    assert _decode_thinking(json.dumps({"anthropic_thinking": [{"type": "thinking", "thinking": "x", "signature": ""}]})) == []


def test_media_blocks_and_tool_result_media():
    image = _media_block(MediaData(PNG, "image/png", display_name="s.png"))
    assert image == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(PNG).decode("ascii")},
    }
    pdf = _media_block(MediaData(b"%PDF", "application/pdf", display_name="r.pdf"))
    assert pdf["type"] == "document" and pdf["source"]["media_type"] == "application/pdf"
    assert pdf["title"] == "r.pdf"
    text = _media_block(MediaData(b"hello", "text/markdown", display_name="n.md"))
    assert text["source"] == {"type": "text", "media_type": "text/plain", "data": "hello"}
    assert _media_block(MediaData(b"wave", "audio/wav", display_name="a.wav")) is None

    provider = _provider()
    messages = [
        Message(role="user", parts=[MessagePart(type="text", text="shot")]),
        Message(role="assistant", parts=[MessagePart(type="tool_call", tool_name="browser_snapshot", tool_args={})]),
        Message(role="tool", parts=[
            MessagePart(
                type="tool_result", tool_name="browser_snapshot", tool_result="saved",
                media_inputs=[MediaData(PNG, "image/png", display_name="shot.png")],
            ),
        ]),
    ]
    converted = provider._convert_messages(messages)
    result = converted[2]["content"][0]
    assert result["tool_use_id"] == "mucli_toolu_1"
    assert result["content"][0] == {"type": "text", "text": "saved"}
    assert result["content"][1]["type"] == "image"


def test_supports_input_mime_uses_registry_then_native_shapes():
    provider = _provider("claude-opus-5")
    assert provider.supports_input_mime("image/png", "a.png")
    assert provider.supports_input_mime("application/pdf", "a.pdf")
    assert not provider.supports_input_mime("audio/wav", "a.wav")
    assert not provider.supports_input_mime("video/mp4", "a.mp4")
    assert provider.native_media_request_limit() == 32 * 1024 * 1024


# ------------------------------------------------------------ request build


def _tools():
    return [ToolDefinition(name="read_file", description="Read", parameters={"type": "object", "properties": {"path": {"type": "string"}}})]


def test_request_defaults_cache_system_prompt_and_tail(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_MAX_TOKENS", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTO_CACHE", raising=False)
    monkeypatch.delenv("ANTHROPIC_EAGER_TOOL_STREAMING", raising=False)
    provider = _provider("claude-opus-5")
    request = provider._build_request(
        [Message(role="user", parts=[MessagePart(type="text", text="hi")])],
        "SYSTEM", False, _tools(), None, None,
    )
    assert request["model"] == "claude-opus-5"
    assert request["max_tokens"] == 64000
    assert request["system"] == [{"type": "text", "text": "SYSTEM", "cache_control": {"type": "ephemeral"}}]
    assert request["cache_control"] == {"type": "ephemeral"}
    assert request["tool_choice"] == {"type": "auto"}
    assert request["tools"][0] == {
        "name": "read_file",
        "description": "Read",
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
    }
    # Opus 5 thinks by default: no explicit thinking config is sent.
    assert "thinking" not in request and "output_config" not in request
    assert request["messages"] == [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]


def test_request_cache_hint_and_env_switches(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTO_CACHE", "0")
    monkeypatch.setenv("ANTHROPIC_EAGER_TOOL_STREAMING", "1")
    provider = _provider("claude-opus-5")
    hint = CacheHint(cache_system_prompt=False, cache_tools=False)
    request = provider._build_request(
        [Message(role="user", parts=[MessagePart(type="text", text="hi")])],
        "SYSTEM", False, _tools(), hint, None,
    )
    assert request["system"] == [{"type": "text", "text": "SYSTEM"}]
    assert "cache_control" not in request
    assert request["tools"][0]["eager_input_streaming"] is True


def test_adaptive_thinking_and_effort_mapping():
    provider = _provider("claude-opus-4-8")
    msgs = [Message(role="user", parts=[MessagePart(type="text", text="hi")])]
    plain = provider._build_request(msgs, None, False, None, None, None)
    assert "thinking" not in plain
    on = provider._build_request(msgs, None, True, None, None, None)
    assert on["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert "output_config" not in on
    effort = provider._build_request(msgs, None, False, None, None, "low")
    assert effort["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert effort["output_config"] == {"effort": "low"}


def test_budget_thinking_for_pre_adaptive_models():
    provider = _provider("claude-haiku-4-5")
    msgs = [Message(role="user", parts=[MessagePart(type="text", text="hi")])]
    request = provider._build_request(msgs, None, True, None, None, "high")
    assert request["thinking"] == {"type": "enabled", "budget_tokens": 16000}
    assert request["max_tokens"] == 32000
    assert "output_config" not in request
    off = provider._build_request(msgs, None, False, None, None, None)
    assert "thinking" not in off


def test_thinking_replay_follows_thinking_state():
    sig = _encode_thinking([{"type": "thinking", "thinking": "t", "signature": "s"}])
    msgs = [
        Message(role="user", parts=[MessagePart(type="text", text="go")]),
        Message(role="assistant", parts=[MessagePart(type="tool_call", tool_name="a", tool_args={}, thought_signature=sig)]),
        Message(role="tool", parts=[MessagePart(type="tool_result", tool_name="a", tool_result="1")]),
    ]
    # Opus 4.8 with thinking off: thinking blocks are not replayed.
    off = _provider("claude-opus-4-8")._build_request(msgs, None, False, None, None, None)
    assert [b["type"] for b in off["messages"][1]["content"]] == ["tool_use"]
    on = _provider("claude-opus-4-8")._build_request(msgs, None, True, None, None, None)
    assert [b["type"] for b in on["messages"][1]["content"]] == ["thinking", "tool_use"]
    # Opus 5 thinks by default even without the parameter.
    default_on = _provider("claude-opus-5")._build_request(msgs, None, False, None, None, None)
    assert [b["type"] for b in default_on["messages"][1]["content"]] == ["thinking", "tool_use"]


# --------------------------------------------------------------- streaming


def test_stream_translates_events_and_usage():
    provider = _provider("claude-opus-5", responses=[_FakeStream(_tool_turn_events(), _final())])
    events = list(provider.stream(
        [Message(role="user", parts=[MessagePart(type="text", text="read a.py")])],
        system_prompt="S", thinking=True, tools=_tools(),
    ))
    kinds = [e.kind for e in events]
    assert kinds == [
        "thinking_delta", "text_delta", "tool_call_start",
        "tool_call_args_delta", "tool_call_args_delta", "tool_call_complete",
        "usage", "done",
    ]
    start = events[2]
    assert start.tool_name == "read_file" and start.tool_call_id == "toolu_01"
    complete = events[5]
    assert complete.tool_args == {"path": "a.py"}
    assert complete.tool_call_id == "toolu_01"
    assert _decode_thinking(complete.thought_signature) == [
        {"type": "thinking", "thinking": "Let me look.", "signature": "sig-1"}
    ]
    usage = events[6]
    assert usage.input_tokens == 550  # uncached + cache read + cache write
    assert usage.cached_tokens == 400
    assert usage.output_tokens == 42
    assert usage.total_tokens == 592
    assert usage.reasoning_tokens == 7
    assert provider._client.messages.requests[0]["thinking"] == {"type": "adaptive", "display": "summarized"}


def test_generate_drains_stream_into_parts():
    provider = _provider("claude-opus-5", responses=[_FakeStream(_tool_turn_events(), _final())])
    response = provider.generate([Message(role="user", parts=[MessagePart(type="text", text="go")])])
    assert response.text == "Reading the file."
    calls = [p for p in response.parts if p.type == "tool_call"]
    assert len(calls) == 1
    assert calls[0].tool_name == "read_file"
    assert calls[0].tool_args == {"path": "a.py"}
    assert calls[0].thought_signature
    assert response.input_tokens == 550 and response.cached_tokens == 400


def test_stream_retries_once_without_thinking_replay_on_signature_error():
    sig = _encode_thinking([{"type": "thinking", "thinking": "t", "signature": "stale"}])
    msgs = [
        Message(role="user", parts=[MessagePart(type="text", text="go")]),
        Message(role="assistant", parts=[MessagePart(type="tool_call", tool_name="a", tool_args={}, thought_signature=sig)]),
        Message(role="tool", parts=[MessagePart(type="tool_result", tool_name="a", tool_result="1")]),
    ]
    provider = _provider("claude-opus-5", responses=[
        _bad_request("messages.1.content.0: Invalid `signature` in `thinking` block"),
        _FakeStream(_tool_turn_events(), _final()),
    ])
    events = list(provider.stream(msgs, thinking=True))
    assert events[-1].kind == "done"
    first, second = provider._client.messages.requests
    assert [b["type"] for b in first["messages"][1]["content"]] == ["thinking", "tool_use"]
    assert [b["type"] for b in second["messages"][1]["content"]] == ["tool_use"]


def test_stream_surfaces_errors_and_reraises():
    provider = _provider("claude-opus-5", responses=[_bad_request("prompt is too long: 1200000 tokens > 1000000 maximum")])
    gen = provider.stream([Message(role="user", parts=[MessagePart(type="text", text="go")])])
    first = next(gen)
    assert first.kind == "error"
    assert first.text.startswith("BadRequestError:")
    with pytest.raises(anthropic.BadRequestError):
        next(gen)


def test_stream_notes_refusal_and_closes_stream():
    events = [
        SimpleNamespace(type="message_start", message=SimpleNamespace(usage=SimpleNamespace(
            input_tokens=10, cache_read_input_tokens=0, cache_creation_input_tokens=0))),
        SimpleNamespace(type="message_delta", delta=SimpleNamespace(stop_reason="refusal"),
                        usage=SimpleNamespace(output_tokens=0, input_tokens=None,
                                              cache_read_input_tokens=None,
                                              cache_creation_input_tokens=None,
                                              output_tokens_details=None)),
    ]
    fake = _FakeStream(events, _final("refusal", SimpleNamespace(category="cyber", explanation="Declined.")))
    provider = _provider("claude-fable-5-1", responses=[fake])
    out = list(provider.stream([Message(role="user", parts=[MessagePart(type="text", text="x")])]))
    assert out[0].kind == "text_delta" and "cyber" in out[0].text and "Declined." in out[0].text
    assert out[-1].kind == "done"
    assert fake.closed is True


def test_malformed_tool_json_falls_back_to_snapshot_then_raw():
    events = [
        SimpleNamespace(type="content_block_start", index=0,
                        content_block=SimpleNamespace(type="tool_use", id="t1", name="x", input={})),
        _delta(0, type="input_json_delta", partial_json='{"a": '),
        SimpleNamespace(type="content_block_stop", index=0,
                        content_block=SimpleNamespace(type="tool_use", id="t1", name="x", input={"a": 1})),
        SimpleNamespace(type="content_block_start", index=1,
                        content_block=SimpleNamespace(type="tool_use", id="t2", name="y", input={})),
        _delta(1, type="input_json_delta", partial_json='not json'),
        SimpleNamespace(type="content_block_stop", index=1, content_block=None),
    ]
    provider = _provider(responses=[_FakeStream(events, _final())])
    out = [e for e in provider.stream([Message(role="user", parts=[MessagePart(type="text", text="x")])]) if e.kind == "tool_call_complete"]
    assert out[0].tool_args == {"a": 1}
    assert out[1].tool_args == {"_raw": "not json"}


# ------------------------------------------------------------------ wiring


def test_pricing_registry_and_provider_inference():
    from utils.model_pricing import infer_provider, resolve_model_capabilities, resolve_token_pricing

    assert infer_provider("claude-opus-5") == "anthropic"
    assert infer_provider("anthropic.claude-sonnet-5") == "anthropic"
    opus = resolve_token_pricing("anthropic", "claude-opus-5")
    assert opus is not None and opus.key == "claude-opus-5"
    assert opus.input_per_million == pytest.approx(5.0)
    assert opus.cached_input_per_million == pytest.approx(0.5)
    assert resolve_token_pricing("anthropic", "claude-opus-5-5").key == "claude-opus-5-5"
    assert resolve_token_pricing("anthropic", "claude-fable-5-1").key == "claude-fable-5-1"
    assert resolve_token_pricing("anthropic", "claude-fable-5").key == "claude-fable-5"
    caps = resolve_model_capabilities("anthropic", "claude-sonnet-5")
    assert caps["input_modalities"] == ["text", "image", "document"]


def test_cost_estimate_uses_cache_read_rate():
    from utils.model_pricing import estimate_model_cost

    estimate = estimate_model_cost(
        provider="anthropic", model_name="claude-opus-5",
        input_tokens=1_000_000, output_tokens=0, cached_tokens=400_000,
    )
    assert estimate["pricing_key"] == "claude-opus-5"
    # 600k uncached @ $5 + 400k cached @ $0.50
    assert estimate["api_cost_usd"] == pytest.approx(3.0 + 0.2)


def test_gui_router_and_cli_know_anthropic(monkeypatch):
    import asyncio

    from mu.gui.routers import providers as providers_mod

    assert "anthropic" in providers_mod.KNOWN_PROVIDERS
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    listing = asyncio.run(providers_mod.list_providers())
    entry = next(p for p in listing["providers"] if p["name"] == "anthropic")
    assert entry["configured"] is True
    assert entry["requires"] == "ANTHROPIC_API_KEY"
    provider = providers_mod._safe_init("anthropic", "claude-opus-5")
    assert isinstance(provider, AnthropicProvider)

    import mucli

    cli_provider = mucli.init_provider("anthropic", "claude-sonnet-5")
    assert isinstance(cli_provider, AnthropicProvider)
    assert cli_provider.model_name == "claude-sonnet-5"
