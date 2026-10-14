"""OpenAI: effort mapping, cache routing, no SDK retries, cached model list and the Responses API
path that replays encrypted reasoning (provider_latency_ollama_openai.md §5, §9)."""
from types import SimpleNamespace

import pytest

from providers.base import CacheHint, Message, MessagePart, ToolDefinition
from providers.openai import OpenAIProvider, _is_reasoning_model, openai_reasoning_effort
from providers.openai_responses import decode_reasoning

TOOLS = [ToolDefinition(name="read_file", description="Read", parameters={"type": "object", "properties": {"filename": {"type": "string"}}})]


def ev(kind, **fields):
    return SimpleNamespace(type=kind, **fields)


class FakeResponses:
    def __init__(self, script):
        self.script, self.calls = list(script), []

    def create(self, **payload):
        self.calls.append(payload)
        return iter(self.script.pop(0))


def provider(model="gpt-5.1", variables=None, script=()):
    p = OpenAIProvider(model, api_key="sk-test")
    p._session_variables = dict(variables or {})
    p._client = SimpleNamespace(responses=FakeResponses(script), models=SimpleNamespace(list=lambda: None))
    return p


def turn_with_tool_call(call_id="call_1"):
    reasoning = SimpleNamespace(model_dump=lambda exclude_none=True: {"type": "reasoning", "id": "rs_1",
                                                                       "encrypted_content": "ENC", "summary": []})
    call = {"type": "function_call", "id": "fc_1", "call_id": call_id, "name": "read_file", "arguments": ""}
    done_call = dict(call, arguments='{"filename": "a.py"}')
    usage = SimpleNamespace(input_tokens=1000, output_tokens=50, total_tokens=1050,
                            input_tokens_details=SimpleNamespace(cached_tokens=900),
                            output_tokens_details=SimpleNamespace(reasoning_tokens=30))
    return [ev("response.output_item.done", item=reasoning),
            ev("response.output_item.added", item=call),
            ev("response.function_call_arguments.delta", item_id="fc_1", delta='{"filename": '),
            ev("response.function_call_arguments.delta", item_id="fc_1", delta='"a.py"}'),
            ev("response.output_item.done", item=done_call),
            ev("response.completed", response=SimpleNamespace(usage=usage))]


def final_text():
    usage = SimpleNamespace(input_tokens=1100, output_tokens=5, total_tokens=1105,
                            input_tokens_details=SimpleNamespace(cached_tokens=1000), output_tokens_details=None)
    return [ev("response.output_text.delta", delta="done"), ev("response.completed", response=SimpleNamespace(usage=usage))]


def test_effort_values_are_valid_for_openai():
    assert openai_reasoning_effort("gpt-5.1", "max", False) == "high"
    assert openai_reasoning_effort("gpt-5.1", "max", False, ["gpt-5.1"]) == "xhigh"
    assert openai_reasoning_effort("gpt-5.1", "none", False) == "none"
    assert openai_reasoning_effort("gpt-5", "none", False) == "minimal"
    assert openai_reasoning_effort("o3", "minimal", False) == "low"
    assert openai_reasoning_effort("gpt-5.1", None, False) is None  # API default, not forced medium
    assert openai_reasoning_effort("gpt-5.1", None, True) == "high"
    assert not _is_reasoning_model("gpt-5-chat-latest") and openai_reasoning_effort("gpt-5-chat-latest", "high", True) is None
    assert openai_reasoning_effort("gpt-4o", "high", True) is None


def test_reasoning_models_use_the_responses_api_and_replay_reasoning():
    p = provider(script=[turn_with_tool_call(), final_text()])
    history = [Message("user", [MessagePart("text", text="read a.py")])]
    first = p.drain_stream(p.stream(history, system_prompt="SYS", tools=TOOLS, reasoning_effort="low",
                                    cache_hint=CacheHint(cache_key="mucli-abc")))
    request = p._client.responses.calls[0]
    assert request["store"] is False and request["include"] == ["reasoning.encrypted_content"]
    assert request["instructions"] == "SYS" and request["reasoning"] == {"effort": "low"}
    assert request["prompt_cache_key"] == "mucli-abc" and request["tools"][0]["name"] == "read_file"
    call = next(part for part in first.parts if part.type == "tool_call")
    assert call.tool_args == {"filename": "a.py"} and call.tool_call_id == "call_1"
    assert decode_reasoning(call.thought_signature)[0]["encrypted_content"] == "ENC"
    assert first.cached_tokens == 900 and first.reasoning_tokens == 30

    # Next tool-loop turn: the reasoning item is replayed right before its function call.
    history += [Message("assistant", first.parts),
                Message("tool", [MessagePart(type="tool_result", tool_name="read_file", tool_call_id="call_1",
                                             tool_result="print(1)")])]
    second = p.drain_stream(p.stream(history, system_prompt="SYS", tools=TOOLS))
    items = p._client.responses.calls[1]["input"]
    kinds = [i.get("type") or i.get("role") for i in items]
    assert kinds == ["user", "reasoning", "function_call", "function_call_output"]
    assert items[1]["encrypted_content"] == "ENC" and items[3] == {"type": "function_call_output", "call_id": "call_1",
                                                                     "output": "print(1)"}
    assert second.text == "done"


def test_api_selection():
    history = [Message("user", [MessagePart("text", text="hi")])]
    assert provider("gpt-5.1")._use_responses("gpt-5.1", history, {})
    assert not provider("gpt-4o")._use_responses("gpt-4o", history, {})
    assert not provider("gpt-5.1")._use_responses("gpt-5.1", history, {"openai_api": "chat"})
    assert provider("gpt-4o")._use_responses("gpt-4o", history, {"openai_api": "responses"})


def test_sdk_retries_are_off_and_models_are_cached(monkeypatch):
    p = OpenAIProvider("gpt-4o", api_key="sk-test")
    assert p._client.max_retries == 0
    calls = []
    p._client = SimpleNamespace(models=SimpleNamespace(list=lambda: calls.append(1) or SimpleNamespace(
        data=[SimpleNamespace(id="gpt-5.1")])))
    assert p.get_available_models() == ["gpt-5.1"] and p.get_available_models() == ["gpt-5.1"] and calls == [1]
    from providers.anthropic import AnthropicProvider
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert AnthropicProvider("claude-opus-5-5")._client.max_retries == 0


def test_chat_path_gets_the_cache_key():
    p = OpenAIProvider("gpt-4o", api_key="sk-test")
    p._session_variables = {"openai_prompt_cache_retention": "24h"}
    seen = []

    class Completions:
        def create(self, **payload):
            seen.append(payload)
            return iter([])
    p._client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    list(p.stream([Message("user", [MessagePart("text", text="hi")])], cache_hint=CacheHint(cache_key="mucli-k")))
    assert seen[0]["prompt_cache_key"] == "mucli-k" and seen[0]["prompt_cache_retention"] == "24h"
    assert "reasoning_effort" not in seen[0]


def test_loop_sends_a_stable_per_session_cache_key():
    from mu.agent.retry import _cache_key, _stream_cache_kwargs
    session = SimpleNamespace(session_manager=SimpleNamespace(current_session_name="work"), _trace_run_id="run_1")

    class Capable:
        def stream(self, messages, cache_hint=None):
            pass
    hint = _stream_cache_kwargs(Capable(), [Message("user", [MessagePart("text", text="hi")])], session)["cache_hint"]
    assert hint.cache_key == _cache_key(session) and hint.cache_key.startswith("mucli-") and hint.volatile_tail_parts == 0


def test_quota_exhaustion_is_not_retried():
    from mu.agent.retry import is_transient_provider_error
    quota = RuntimeError("RateLimitError: Error code: 429 - {'error': {'message': 'You have no credits remaining.', "
                         "'type': 'insufficient_quota', 'code': 'credit_balance_exhausted'}}")
    assert not is_transient_provider_error(quota)
    assert is_transient_provider_error(RuntimeError("Error code: 429 - rate limit reached for requests"))
