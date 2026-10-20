"""Prompt caching requests: the session's cache hint (Anthropic TTL) and OpenAI's GPT-5.6+
breakpoints (explicit markers on the stable prefix, never the volatile tail; no deprecated
retention; a rejected marker falls back to the plain shape)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from providers.base import CacheHint, LLMProvider, Message, MessagePart

RUNTIME = "RUNTIME STATE (request-only, rebuilt every iteration; not part of the conversation):\n\nclock"


class _Accepts:
    def stream(self, messages, cache_hint=None):  # signature only: retry checks for cache_hint
        return iter(())


def _session(**variables):
    return SimpleNamespace(variables=variables, session_manager=SimpleNamespace(current_session_name="s1"))


def _runtime_tail():
    return [Message(role="user", parts=[MessagePart(type="text", text="go"), MessagePart(type="text", text=RUNTIME)])]


def test_sessions_cache_for_an_hour_by_default():
    from mu.agent.retry import _stream_cache_kwargs

    hint = _stream_cache_kwargs(_Accepts(), _runtime_tail(), _session())["cache_hint"]
    assert hint.ttl == "1h" and hint.volatile_tail_parts == 1 and hint.cache_key
    assert _stream_cache_kwargs(_Accepts(), _runtime_tail(), _session(anthropic_cache_ttl="5m"))["cache_hint"].ttl == "5m"
    assert _stream_cache_kwargs(_Accepts(), _runtime_tail(), _session(anthropic_cache_ttl="2h"))["cache_hint"].ttl == ""


# ------------------------------------------------------------------ OpenAI GPT-5.6+


class _Responses:
    def __init__(self, fail_first=None):
        self.calls = []
        self.fail_first = fail_first

    def create(self, **payload):
        self.calls.append(payload)
        if self.fail_first is not None:
            exc, self.fail_first = self.fail_first, None
            raise exc
        return iter(())


def _openai(model, responses):
    from providers.openai import OpenAIProvider

    provider = OpenAIProvider.__new__(OpenAIProvider)
    LLMProvider.__init__(provider, model)
    provider.name = "openai"
    provider._client = SimpleNamespace(responses=responses)
    return provider


def _run(provider, model, messages, hint, variables=None):
    return list(provider._stream_responses(model, messages, "SYSTEM", False, None, hint, None, variables or {}))


def _tool_loop():
    return [
        Message(role="user", parts=[MessagePart(type="text", text="go"), MessagePart(type="text", text="TURN CONTEXT ...")]),
        Message(role="assistant", parts=[MessagePart(type="tool_call", tool_name="read", tool_args={}, tool_call_id="c1")]),
        Message(role="tool", parts=[MessagePart(type="tool_result", tool_name="read", tool_result="x", tool_call_id="c1")]),
        Message(role="user", parts=[MessagePart(type="text", text=RUNTIME)]),
    ]


def _marked(payload):
    out = []
    for item in payload["input"]:
        for block in item.get("content") or [] if isinstance(item.get("content"), list) else []:
            if "prompt_cache_breakpoint" in block:
                out.append((item["role"], block["text"]))
    return out


def test_gpt56_marks_the_stable_prefix_not_the_volatile_tail():
    responses = _Responses()
    provider = _openai("gpt-5.6-luna", responses)
    _run(provider, "gpt-5.6-luna", _tool_loop(), CacheHint(volatile_tail_parts=1, cache_key="k"),
         {"openai_prompt_cache_retention": "24h"})
    payload = responses.calls[0]
    assert "instructions" not in payload
    assert payload["input"][0]["role"] == "developer"
    # The system prompt and the turn's last stable user block; never the runtime state.
    assert _marked(payload) == [("developer", "SYSTEM"), ("user", "TURN CONTEXT ...")]
    assert "prompt_cache_retention" not in payload  # deprecated for GPT-5.6+
    assert payload["prompt_cache_key"] == "k"


def test_older_models_keep_instructions_and_retention():
    responses = _Responses()
    provider = _openai("gpt-5.5", responses)
    _run(provider, "gpt-5.5", _tool_loop(), CacheHint(volatile_tail_parts=1), {"openai_prompt_cache_retention": "24h"})
    payload = responses.calls[0]
    assert payload["instructions"] == "SYSTEM" and not _marked(payload)
    assert payload["prompt_cache_retention"] == "24h"


def test_rejected_breakpoints_fall_back_to_the_plain_request():
    rejected = Exception("Unknown parameter: prompt_cache_breakpoint")
    rejected.status_code = 400
    responses = _Responses(fail_first=rejected)
    provider = _openai("gpt-6.1-sol", responses)
    _run(provider, "gpt-6.1-sol", _tool_loop(), CacheHint(volatile_tail_parts=1))
    assert len(responses.calls) == 2
    retry = responses.calls[1]
    assert retry["instructions"] == "SYSTEM" and not _marked(retry)
    # Remembered: the next request goes straight to the plain shape.
    _run(provider, "gpt-6.1-sol", _tool_loop(), CacheHint(volatile_tail_parts=1))
    assert len(responses.calls) == 3 and not _marked(responses.calls[2])


def test_other_errors_are_not_swallowed():
    boom = Exception("rate limited")
    boom.status_code = 429
    provider = _openai("gpt-5.6-sol", _Responses(fail_first=boom))
    with pytest.raises(Exception, match="rate limited"):
        _run(provider, "gpt-5.6-sol", _tool_loop(), CacheHint())


def test_gpt6_is_a_reasoning_family_with_none_effort():
    from providers.openai import _is_reasoning_model, openai_reasoning_effort, uses_cache_breakpoints

    assert _is_reasoning_model("gpt-6-sol") and uses_cache_breakpoints("gpt-6-sol")
    assert not uses_cache_breakpoints("gpt-5.5-pro") and uses_cache_breakpoints("gpt-5.6-terra")
    assert openai_reasoning_effort("gpt-6.1-sol", "none", False) == "none"
    assert openai_reasoning_effort("gpt-5", "none", False) == "minimal"
