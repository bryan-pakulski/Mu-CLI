"""Tests for LLM-generated structured compaction summaries.

Covers:
  * _generate_llm_summary calls provider.generate and returns structured output with ### headers
  * Fallback to _summarize_history_batch on provider exception
  * _merge_structured_summary correctly merges new content into existing sections
  * Backward compat: provider=None uses mechanical truncation
  * roll_history_summary_to_token_budget with provider uses LLM path
"""

from __future__ import annotations

from typing import List

import pytest

from mu.session.history import HistoryMixin
from providers.base import (
    LLMProvider,
    Message,
    MessagePart,
    ProviderResponse,
)


# ---------------------------------------------------------- stubs


class _ScriptedProvider(LLMProvider):
    """Provider that returns a canned response and records call args."""

    def __init__(self, response_text: str = "", raise_exc: Exception | None = None):
        super().__init__("scripted")
        self.name = "scripted"
        self._response_text = response_text
        self._raise = raise_exc
        self.generate_calls: list[dict] = []

    def get_available_models(self) -> List[str]:
        return ["scripted"]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        self.generate_calls.append(
            {
                "system_prompt": system_prompt,
                "messages": messages,
                "tools": tools,
            }
        )
        if self._raise is not None:
            raise self._raise
        return ProviderResponse(
            text=self._response_text,
            parts=[MessagePart(type="text", text=self._response_text)],
            input_tokens=10,
            output_tokens=20,
            total_tokens=30,
        )

    def upload_file(self, *a, **kw):
        return None


class _Host(HistoryMixin):
    """Minimal host class for HistoryMixin testing."""

    def __init__(self, history=None, summary_anchor=0, conversation_summary=""):
        self.history = history or []
        self.summary_anchor = summary_anchor
        self.conversation_summary = conversation_summary


def _sample_entries() -> list[dict]:
    """Conversation entries representative of an exploration turn."""
    return [
        {"role": "user", "parts": [{"type": "text", "text": "Refactor the auth layer to use JWT"}]},
        {
            "role": "assistant",
            "parts": [
                {"type": "text", "text": "I'll start by reading the auth module."},
                {"type": "tool_call", "tool_name": "read_file", "tool_args": {"filename": "auth.py"}},
            ],
        },
        {
            "role": "tool",
            "parts": [
                {"type": "tool_result", "tool_name": "read_file", "tool_result": "def login(user, pw): ..."},
            ],
        },
        {
            "role": "assistant",
            "parts": [
                {"type": "text", "text": "Found the login function. Need to add JWT token generation."},
            ],
        },
    ]


_LLM_SUMMARY_OUTPUT = """### Task
Refactor the auth layer to use JWT tokens instead of session cookies.

### Progress
Read auth.py and found the login function. Identified where JWT token generation needs to be added.

### Key decisions
None

### Current state
auth.py contains login(user, pw) function. No JWT logic yet.

### Open items
Implement JWT token generation in login function. Add token verification middleware."""


# ============================================================ _generate_llm_summary


def test_generate_llm_summary_calls_provider_and_returns_structured_output():
    provider = _ScriptedProvider(response_text=_LLM_SUMMARY_OUTPUT)
    host = _Host()
    result = host._generate_llm_summary(provider, _sample_entries())

    assert result is not None
    assert "### Task" in result
    assert "### Progress" in result
    assert "### Key decisions" in result
    assert "### Current state" in result
    assert "### Open items" in result
    # Provider was called once with the structured system prompt.
    assert len(provider.generate_calls) == 1
    call = provider.generate_calls[0]
    assert "### Task" in call["system_prompt"]
    assert call["tools"] is None  # no tools during summarization


def test_generate_llm_summary_returns_none_on_provider_exception():
    provider = _ScriptedProvider(raise_exc=RuntimeError("provider down"))
    host = _Host()
    result = host._generate_llm_summary(provider, _sample_entries())
    # Should return None so caller falls back to mechanical summarization.
    assert result is None


def test_generate_llm_summary_returns_none_for_empty_entries():
    provider = _ScriptedProvider(response_text="### Task\nstuff")
    host = _Host()
    result = host._generate_llm_summary(provider, [])
    assert result is None


def test_generate_llm_summary_returns_none_when_provider_is_none():
    host = _Host()
    result = host._generate_llm_summary(None, _sample_entries())
    assert result is None


def test_generate_llm_summary_rejects_output_without_section_headers():
    """If the model doesn't produce ### headers, it didn't follow the
    prompt — fall back to mechanical."""
    provider = _ScriptedProvider(response_text="Just a plain summary without headers.")
    host = _Host()
    result = host._generate_llm_summary(provider, _sample_entries())
    assert result is None


def test_generate_llm_summary_rejects_empty_response_text():
    provider = _ScriptedProvider(response_text="")
    host = _Host()
    result = host._generate_llm_summary(provider, _sample_entries())
    assert result is None


def test_render_entries_for_llm_preserves_more_content_than_mechanical():
    """The LLM renderer should preserve much more than 140 chars per part."""
    host = _Host()
    long_text = "x" * 400
    entries = [{"role": "user", "parts": [{"type": "text", "text": long_text}]}]
    rendered = host._render_entries_for_llm(entries)
    # 500-char budget means the 400-char text should survive intact.
    assert long_text in rendered


# ============================================================ _merge_structured_summary


def test_merge_structured_summary_first_merge_into_empty():
    host = _Host(conversation_summary="")
    host._merge_structured_summary(_LLM_SUMMARY_OUTPUT)
    summary = host.conversation_summary
    assert "### Task" in summary
    assert "### Progress" in summary
    assert "JWT" in summary


def test_merge_structured_summary_appends_to_existing_sections():
    existing = """### Task
Original task.

### Progress
Did step 1.

### Key decisions
None

### Current state
File A modified.

### Open items
Do step 2."""
    host = _Host(conversation_summary=existing)
    new = """### Progress
Did step 2 and step 3.

### Current state
File A and File B modified. Tests passing.

### Open items
Do step 4."""
    host._merge_structured_summary(new)
    summary = host.conversation_summary
    # Task section preserved from existing.
    assert "Original task." in summary
    # Progress section has both old and new content.
    assert "Did step 1." in summary
    assert "Did step 2 and step 3." in summary
    # Current state merged.
    assert "File A modified." in summary
    assert "File B modified." in summary
    # Open items updated.
    assert "Do step 4." in summary


def test_merge_structured_summary_preserves_untouched_sections():
    existing = """### Task
Important task.

### Progress
Step 1 done.

### Key decisions
Chose JWT over sessions.

### Current state
auth.py modified.

### Open items
Step 2."""
    host = _Host(conversation_summary=existing)
    # New summary only touches Progress.
    new = """### Progress
Step 1 and 2 done."""
    host._merge_structured_summary(new)
    summary = host.conversation_summary
    # Task, Key decisions, Current state, Open items preserved.
    assert "Important task." in summary
    assert "Chose JWT over sessions." in summary
    assert "auth.py modified." in summary
    assert "Step 2." in summary
    # Progress updated.
    assert "Step 1 and 2 done." in summary


def test_merge_structured_summary_adds_new_sections():
    existing = "### Task\nOriginal task.\n"
    host = _Host(conversation_summary=existing)
    new = """### Progress
New progress entry.

### Open items
New open item."""
    host._merge_structured_summary(new)
    summary = host.conversation_summary
    assert "### Task" in summary
    assert "Original task." in summary
    assert "### Progress" in summary
    assert "New progress entry." in summary
    assert "### Open items" in summary
    assert "New open item." in summary


def test_merge_structured_summary_falls_back_for_legacy_summary():
    """If existing summary has no ### headers, blind-append the new one."""
    existing = "This is a legacy mechanical summary with no sections."
    host = _Host(conversation_summary=existing)
    new = "### Task\nNew structured summary."
    host._merge_structured_summary(new)
    summary = host.conversation_summary
    assert "legacy mechanical summary" in summary
    assert "### Task" in summary
    assert "New structured summary." in summary


def test_merge_structured_summary_skips_none_content():
    """New summary sections with 'None' content should not pollute the merge."""
    existing = "### Task\nReal task.\n"
    host = _Host(conversation_summary=existing)
    new = """### Task
None

### Progress
Real progress."""
    host._merge_structured_summary(new)
    summary = host.conversation_summary
    # Existing Task preserved (incoming Task was "None" → skipped).
    assert "Real task." in summary
    assert "Real progress." in summary


def test_merge_structured_summary_maintains_canonical_order():
    """After merge, sections should appear in canonical order:
    Task, Progress, Key decisions, Current state, Open items."""
    existing = "### Open items\nStep 3.\n### Task\nMy task.\n"
    host = _Host(conversation_summary=existing)
    new = "### Progress\nStep 1 done."
    host._merge_structured_summary(new)
    summary = host.conversation_summary
    task_pos = summary.index("### Task")
    progress_pos = summary.index("### Progress")
    open_items_pos = summary.index("### Open items")
    assert task_pos < progress_pos < open_items_pos


# ============================================================ roll_history_summary with provider


def test_roll_history_summary_uses_llm_when_provider_available():
    """When provider is passed, roll_history_summary should call
    _generate_llm_summary and produce a structured summary."""
    provider = _ScriptedProvider(response_text=_LLM_SUMMARY_OUTPUT)
    history = [
        {"role": "user", "parts": [{"type": "text", "text": "Refactor auth"}]},
        {"role": "assistant", "parts": [{"type": "text", "text": "Reading auth.py"}]},
        {"role": "user", "parts": [{"type": "text", "text": "continue"}]},
        {"role": "assistant", "parts": [{"type": "text", "text": "Done with step 1"}]},
        {"role": "user", "parts": [{"type": "text", "text": "next step"}]},
        {"role": "assistant", "parts": [{"type": "text", "text": "Working on step 2"}]},
    ]
    host = _Host(history=list(history))
    changed = host.roll_history_summary(keep_recent=2, provider=provider)

    assert changed is True
    assert provider.generate_calls  # LLM was called
    assert "### Task" in host.conversation_summary
    assert "### Progress" in host.conversation_summary
    # Summary anchor advanced past the summarized entries.
    assert host.summary_anchor > 0


def test_roll_history_summary_falls_back_to_mechanical_on_llm_failure():
    """If the LLM call fails, roll_history_summary should fall back to
    mechanical _summarize_history_batch."""
    provider = _ScriptedProvider(raise_exc=RuntimeError("LLM unavailable"))
    history = [
        {"role": "user", "parts": [{"type": "text", "text": "Do something"}]},
        {"role": "assistant", "parts": [{"type": "text", "text": "Working on it"}]},
        {"role": "user", "parts": [{"type": "text", "text": "continue"}]},
        {"role": "assistant", "parts": [{"type": "text", "text": "Done"}]},
    ]
    host = _Host(history=list(history))
    changed = host.roll_history_summary(keep_recent=2, provider=provider)

    assert changed is True
    # Provider was called but failed.
    assert len(provider.generate_calls) == 1
    # Summary should have mechanical content (no ### Task header).
    assert host.conversation_summary
    assert "### Task" not in host.conversation_summary


def test_roll_history_summary_uses_mechanical_when_provider_is_none():
    """Backward compat: provider=None uses mechanical truncation."""
    history = [
        {"role": "user", "parts": [{"type": "text", "text": "Do something"}]},
        {"role": "assistant", "parts": [{"type": "text", "text": "Working on it"}]},
        {"role": "user", "parts": [{"type": "text", "text": "continue"}]},
        {"role": "assistant", "parts": [{"type": "text", "text": "Done"}]},
    ]
    host = _Host(history=list(history))
    changed = host.roll_history_summary(keep_recent=2, provider=None)

    assert changed is True
    assert host.conversation_summary
    assert "### Task" not in host.conversation_summary  # mechanical has no ###


# ============================================================ roll_history_summary_to_token_budget


def test_roll_history_summary_to_token_budget_uses_llm_path():
    """roll_history_summary_to_token_budget should pass provider through
    to roll_history_summary so the LLM path is used."""
    provider = _ScriptedProvider(response_text=_LLM_SUMMARY_OUTPUT)
    # Build history large enough to trigger compaction.
    history = []
    for i in range(20):
        role = "user" if i % 2 == 0 else "assistant"
        history.append(
            {"role": role, "parts": [{"type": "text", "text": f"turn {i} " + "x" * 200}]}
        )
    host = _Host(history=history)
    changed = host.roll_history_summary_to_token_budget(
        token_budget=100, keep_recent=2, provider=provider
    )
    assert changed is True
    assert provider.generate_calls  # LLM was called
    assert "### Task" in host.conversation_summary


def test_roll_history_summary_to_token_budget_backward_compat_no_provider():
    """Without provider, should still work via mechanical truncation."""
    history = []
    for i in range(20):
        role = "user" if i % 2 == 0 else "assistant"
        history.append(
            {"role": role, "parts": [{"type": "text", "text": f"turn {i} " + "x" * 200}]}
        )
    host = _Host(history=history)
    changed = host.roll_history_summary_to_token_budget(
        token_budget=100, keep_recent=2, provider=None
    )
    assert changed is True
    assert host.conversation_summary
    assert "### Task" not in host.conversation_summary


# ============================================================ char limit changes


def test_clip_conversation_summary_uses_12000_limit():
    """_clip_conversation_summary default limit should now be 12000."""
    host = _Host(conversation_summary="x" * 11000)
    host._clip_conversation_summary()
    # 11000 < 12000 → no clipping.
    assert len(host.conversation_summary) == 11000

    host.conversation_summary = "x" * 25000
    host._clip_conversation_summary()
    assert len(host.conversation_summary) <= 24100  # 24000 + truncation marker


def test_degrade_oldest_uses_16000_max_chars():
    """_degrade_oldest_runtime_payload default max_chars should now be 16000."""
    host = _Host(
        history=[
            {"role": "user", "parts": [{"type": "text", "text": "X" * 20000}]},
            {"role": "assistant", "parts": [{"type": "text", "text": "short"}]},
        ]
    )
    changed = host._degrade_oldest_runtime_payload()
    assert changed is True
    truncated = host.history[0]["parts"][0]["text"]
    assert len(truncated) <= 16100  # 16000 + marker
    assert "truncated_to_16000_chars_for_context_budget" in truncated