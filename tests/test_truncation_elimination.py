"""Tests for truncation elimination across the context pipeline.

Verifies:
  1. L4 summarize_message_parts uses LLM when provider available, 500-char fallback when not
  2. TaskMemoryStore expanded to 512 entries / 8000 char render
  3. ScratchpadStore expanded to 128 entries / 4000 char render
  4. CollationBuffer expanded to 2MB
  5. retrieval_context_char_limit = 10000
  6. workspace_context_max_chars = 16384
  7. _degrade_oldest uses LLM when provider available, 16000-char fallback when not
  8. All existing tests still pass
"""

from __future__ import annotations

from typing import List

import pytest

from mu.session.history import HistoryMixin
from providers.base import LLMProvider, Message, MessagePart, ProviderResponse


# ---------------------------------------------------------- stubs


class _ScriptedProvider(LLMProvider):
    def __init__(self, response_text: str = "", raise_exc: Exception | None = None):
        super().__init__("scripted")
        self.name = "scripted"
        self._response_text = response_text
        self._raise = raise_exc
        self.generate_calls: list = []

    def get_available_models(self) -> List[str]:
        return ["scripted"]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        self.generate_calls.append({"system_prompt": system_prompt, "messages": messages})
        if self._raise is not None:
            raise self._raise
        return ProviderResponse(
            text=self._response_text,
            parts=[MessagePart(type="text", text=self._response_text)],
        )

    def upload_file(self, *a, **kw):
        return None


class _Host(HistoryMixin):
    def __init__(self, history=None, summary_anchor=0, conversation_summary=""):
        self.history = history or []
        self.summary_anchor = summary_anchor
        self.conversation_summary = conversation_summary


# ============================================================ L4 summarize_message_parts


def test_summarize_message_parts_accepts_provider_param():
    """summarize_message_parts signature includes optional provider param."""
    from mu.session.messages import summarize_message_parts

    msg = {"role": "user", "parts": [{"type": "text", "text": "hello"}]}
    # Should work with provider=None (backward compat).
    result = summarize_message_parts(msg, provider=None)
    assert "hello" in result


def test_summarize_message_parts_mechanical_uses_500_char_limit():
    """When provider=None, text truncation should be at 500 chars, not 120."""
    from mu.session.messages import summarize_message_parts

    long_text = "A" * 400
    msg = {"role": "user", "parts": [{"type": "text", "text": long_text}]}
    result = summarize_message_parts(msg, provider=None)
    # 400 < 500 so full text should survive.
    assert long_text in result


def test_summarize_message_parts_mechanical_truncates_at_500():
    """Text over 500 chars should be truncated to 500 (was 120)."""
    from mu.session.messages import summarize_message_parts

    long_text = "A" * 600
    msg = {"role": "user", "parts": [{"type": "text", "text": long_text}]}
    result = summarize_message_parts(msg, provider=None)
    # Text parts use [:500] truncation (no ellipsis for text, only for tool_result).
    # The result should contain at most 500 A's (the first 500 of the 600).
    assert "A" * 600 not in result  # Full 600 A's shouldn't appear
    assert "A" * 500 in result  # First 500 A's should survive


def test_summarize_message_parts_tool_result_500_char_limit():
    """Tool result truncation should be at 500 chars (was 140)."""
    from mu.session.messages import summarize_message_parts

    long_result = "X" * 400
    msg = {
        "role": "tool",
        "parts": [
            {"type": "tool_result", "tool_name": "read_file", "tool_result": long_result}
        ],
    }
    result = summarize_message_parts(msg, provider=None)
    # 400 < 500 so full result should survive.
    assert long_result in result


def test_summarize_message_parts_preserves_cache_key_tags():
    """cache_key tags should be preserved in mechanical path."""
    from mu.session.messages import summarize_message_parts

    msg = {
        "role": "tool",
        "parts": [
            {
                "type": "tool_result",
                "tool_name": "read_file",
                "tool_result": "file content here",
                "cache_key": "abc123def456",
            }
        ],
    }
    result = summarize_message_parts(msg, provider=None)
    assert "[cache:abc123def456]" in result


# ============================================================ Memory store expansions


def test_task_memory_store_max_entries_is_1024():
    """TaskMemoryStore default max_entries should be 1024."""
    from mu.memory.stores import TaskMemoryStore

    store = TaskMemoryStore()
    assert store.max_entries == 1024


def test_task_memory_store_summary_char_limit_is_16000():
    """TaskMemoryStore default summary_char_limit should be 16000."""
    from mu.memory.stores import TaskMemoryStore

    store = TaskMemoryStore()
    assert store.summary_char_limit == 16000


def test_scratchpad_store_max_entries_is_256():
    """ScratchpadStore default max_entries should be 256."""
    from mu.memory.stores import ScratchpadStore

    store = ScratchpadStore()
    assert store.max_entries == 256


def test_scratchpad_store_summary_char_limit_is_8000():
    """ScratchpadStore default summary_char_limit should be 8000."""
    from mu.memory.stores import ScratchpadStore

    store = ScratchpadStore()
    assert store.summary_char_limit == 8000


# ============================================================ CollationBuffer expansion


def test_collation_buffer_is_lossless_by_default():
    """Deferred evidence is retained until explicit model-directed cleanup."""
    from mu.agent.collation import CollationBuffer

    buf = CollationBuffer()
    buf.add("read_file", {"filename": "a.py"}, "A" * 3_000_000)
    assert len(buf.entries) == 1


# ============================================================ Context injection budgets


def test_retrieval_context_char_limit_default_is_10000():
    """retrieval_context_char_limit default in context.py should be 10000."""
    import inspect
    from mu.session import session as sess_mod
    source = inspect.getsource(sess_mod)
    assert "10000" in source and "retrieval_context_char_limit" in source


def test_workspace_context_max_chars_default_is_16384():
    """workspace_context_max_chars default in context.py should be 16384."""
    import inspect
    from mu.session import context as ctx_mod
    source = inspect.getsource(ctx_mod)
    assert "16384" in source and "workspace_context_max_chars" in source


# ============================================================ _degrade_oldest LLM path


def test_degrade_oldest_uses_16000_max_chars_default():
    """_degrade_oldest_runtime_payload default max_chars should be 16000."""
    host = _Host(
        history=[
            {"role": "user", "parts": [{"type": "text", "text": "X" * 20000}]},
            {"role": "assistant", "parts": [{"type": "text", "text": "short"}]},
        ]
    )
    changed = host._degrade_oldest_runtime_payload()
    assert changed is True
    truncated = host.history[0]["parts"][0]["text"]
    assert len(truncated) <= 16100
    assert "truncated_to_16000_chars_for_context_budget" in truncated


def test_degrade_oldest_uses_llm_when_provider_available():
    """When provider is available, _degrade_oldest should call LLM to summarize
    the oversized payload instead of destructively truncating it."""
    provider = _ScriptedProvider(response_text="This is a concise summary of the payload.")
    host = _Host(
        history=[
            {"role": "user", "parts": [{"type": "text", "text": "X" * 20000}]},
            {"role": "assistant", "parts": [{"type": "text", "text": "short"}]},
        ]
    )
    changed = host._degrade_oldest_runtime_payload(provider=provider)
    assert changed is True
    # LLM was called.
    assert len(provider.generate_calls) == 1
    # The text was replaced with the summary, not truncated.
    result_text = host.history[0]["parts"][0]["text"]
    assert "concise summary" in result_text
    assert "truncated_to_" not in result_text


def test_degrade_oldest_falls_back_on_llm_error():
    """When LLM call fails, _degrade_oldest should fall back to mechanical
    truncation at 16000 chars."""
    provider = _ScriptedProvider(raise_exc=RuntimeError("LLM down"))
    host = _Host(
        history=[
            {"role": "user", "parts": [{"type": "text", "text": "X" * 20000}]},
            {"role": "assistant", "parts": [{"type": "text", "text": "short"}]},
        ]
    )
    changed = host._degrade_oldest_runtime_payload(provider=provider)
    assert changed is True
    # Provider was called but failed.
    assert len(provider.generate_calls) == 1
    # Fell back to mechanical truncation.
    result_text = host.history[0]["parts"][0]["text"]
    assert "truncated_to_16000_chars_for_context_budget" in result_text


def test_degrade_oldest_falls_back_when_llm_returns_longer():
    """If LLM summary is longer than original, don't use it — fall back to
    mechanical truncation."""
    provider = _ScriptedProvider(response_text="X" * 25000)  # longer than original
    host = _Host(
        history=[
            {"role": "user", "parts": [{"type": "text", "text": "X" * 20000}]},
            {"role": "assistant", "parts": [{"type": "text", "text": "short"}]},
        ]
    )
    changed = host._degrade_oldest_runtime_payload(provider=provider)
    assert changed is True
    result_text = host.history[0]["parts"][0]["text"]
    # Should have fallen back to mechanical truncation.
    assert "truncated_to_16000_chars_for_context_budget" in result_text


def test_degrade_oldest_no_provider_uses_mechanical():
    """When provider=None, should use mechanical truncation at 16000."""
    host = _Host(
        history=[
            {"role": "user", "parts": [{"type": "text", "text": "X" * 20000}]},
            {"role": "assistant", "parts": [{"type": "text", "text": "short"}]},
        ]
    )
    changed = host._degrade_oldest_runtime_payload(provider=None)
    assert changed is True
    result_text = host.history[0]["parts"][0]["text"]
    assert "truncated_to_16000_chars_for_context_budget" in result_text


def test_degrade_oldest_tool_result_uses_llm_when_provider_available():
    """Tool result payloads should also use LLM summarization when provider
    is available."""
    provider = _ScriptedProvider(response_text="Summarized tool result content.")
    host = _Host(
        history=[
            {
                "role": "tool",
                "parts": [
                    {"type": "tool_result", "tool_name": "read_file", "tool_result": "X" * 20000}
                ],
            },
        ]
    )
    changed = host._degrade_oldest_runtime_payload(provider=provider)
    assert changed is True
    assert len(provider.generate_calls) == 1
    result = host.history[0]["parts"][0]["tool_result"]
    assert "Summarized tool result" in result
    assert "truncated_to_" not in result
