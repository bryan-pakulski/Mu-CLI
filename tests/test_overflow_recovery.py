"""Reactive context-overflow recovery (Claude Code Tier 5 style).

Two regressions these tests pin:

1. **The Ollama overflow classifier.** Ollama's real daemon wording is
   ``"The prompt is too long: N, model maximum context length: M"``. The
   old ``_classify_api_error_body`` only matched the literal substring
   ``"prompt too long"`` (not ``"prompt is too long"``) and an
   ``exceed``+``context`` pair — so the actual overflow body fell through
   to the generic ``"Ollama API error: …"`` branch and was never
   recognised as overflow. ``is_context_overflow_error`` must catch the
   real wording so reactive recovery can fire.

2. **Compact-and-retry instead of crashing.** When the provider rejects
   a prompt as too long (a non-transient 400/413), the agentic loop must
   aggressively compact history (keep the last ~4 messages, summarize the
   rest), rebuild, and retry **once** — not surface a hard error. A
   per-turn guard is the circuit breaker: a second overflow on the same
   turn re-raises instead of looping.
"""

from typing import List, Optional

import pytest

from mu.agent.loop_body import _generate_with_overflow_recovery
from mu.agent.retry import is_context_overflow_error
from providers.base import LLMProvider, Message, MessagePart, ProviderResponse
from providers.ollama import OllamaError, _classify_api_error_body
from mu.session.session import Session, SessionManager


# ----------------------------------------------------------- stub provider


class _StubProvider(LLMProvider):
    """Provider whose `generate` returns a structured summary (so real
    compaction runs) and declares a small context window. `stream` is
    unused here — the recovery wrapper calls `_provider_generate_with_retry`,
    which we patch per-test to control raise/succeed."""

    def __init__(self, name: str = "stub-8k", *, window: int = 8192):
        super().__init__(name)
        self._window = window
        self.model_name = name

    def get_available_models(self) -> List[str]:
        return ["stub-8k"]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        # Return a structured summary so the LLM summarizer path is used
        # (has ### sections) rather than the mechanical fallback.
        return ProviderResponse(
            text="### Task\ndo thing\n### Progress\nmade progress\n### Key decisions\nnone",
            parts=[],
            input_tokens=10,
            output_tokens=10,
            total_tokens=20,
        )

    def stream(self, messages, system_prompt=None, thinking=False, tools=None):
        yield {"type": "text", "text": "ok"}

    def upload_file(self, file_path, mime_type):
        return None

    def effective_context_window(self, model_name: Optional[str] = None):
        return self._window

    def effective_response_reserve(self, model_name: Optional[str] = None):
        return 512

    def compaction_safety_factor(self):
        return 1.0


def _make_session(provider=None):
    sm = SessionManager()
    session = Session(provider or _StubProvider(), False, "sys", sm)
    session.agentic = False
    return session


def _stuff_history(sm, n_turns=20, size=1500):
    sm.history.clear()
    sm.summary_anchor = 0
    sm.conversation_summary = ""
    for _ in range(n_turns):
        sm.history.append(
            {"role": "user", "parts": [{"type": "text", "text": "u" * size}]}
        )
        sm.history.append(
            {"role": "assistant", "parts": [{"type": "text", "text": "a" * size}]}
        )


# ---------------------------------------------------- overflow classifier


def test_classify_real_ollama_wording_is_overflow():
    """The actual daemon message ('The prompt is too long: N, model
    maximum context length: M') must classify as overflow, not fall
    through to the generic 'Ollama API error' branch."""
    body = (
        '{"error":"The prompt is too long: 1041908, '
        'model maximum context length: 1000000 '
        '(ref: b23c3c51-87d8-4e17-b6bf-c9d309996d05)"}'
    )
    err = _classify_api_error_body("http://localhost:11434", "stub-8k", body)
    assert "context overflow" in str(err).lower()
    assert is_context_overflow_error(err)


def test_is_context_overflow_error_real_ollama_message():
    err = OllamaError(
        'Ollama API error: {"error":"The prompt is too long: 1041908, '
        'model maximum context length: 1000000"}'
    )
    assert is_context_overflow_error(err)


def test_is_context_overflow_error_classified_message():
    err = OllamaError("Ollama context overflow for 'stub-8k': prompt too long")
    assert is_context_overflow_error(err)


def test_is_context_overflow_error_http_413():
    assert is_context_overflow_error(RuntimeError("HTTP Error: 413"))
    assert is_context_overflow_error(RuntimeError("413 Payload Too Large"))


def test_is_context_overflow_error_variants():
    assert is_context_overflow_error(RuntimeError("prompt is too long"))
    assert is_context_overflow_error(RuntimeError("maximum context length exceeded"))
    assert is_context_overflow_error(RuntimeError("request entity too large"))


def test_is_not_context_overflow_error_for_transient():
    """Transient/network errors must NOT be classified as overflow —
    they're handled by the retry loop, not reactive compaction."""
    assert not is_context_overflow_error(ConnectionError("timeout"))
    assert not is_context_overflow_error(RuntimeError("503 service unavailable"))
    assert not is_context_overflow_error(RuntimeError("connection reset"))
    assert not is_context_overflow_error(RuntimeError("model not found"))


def test_is_not_context_overflow_error_for_unrelated_400():
    """A 400 without a prompt/context marker is ambiguous — not overflow."""
    assert not is_context_overflow_error(RuntimeError("HTTP Error: 400 bad request shape"))
    assert not is_context_overflow_error(RuntimeError("model not found"))


def test_is_context_overflow_error_inspects_cause():
    """Overflow wrapped by a transport layer (the cause chain) is still
    detected — the real body rides on __cause__."""
    inner = OllamaError("Ollama context overflow for 'm': prompt too long")
    wrapped = RuntimeError("transport failed")
    wrapped.__cause__ = inner
    assert is_context_overflow_error(wrapped)


# ---------------------------------------- reactive recovery: happy path


def test_recovery_compacts_and_retries_on_overflow():
    """First provider call overflows → compaction fires → retry succeeds.
    No error surfaces; older history is summarized (anchor advances)."""
    session = _make_session()
    _stuff_history(session.session_manager)
    anchor_before = session.session_manager.summary_anchor

    calls = []

    def fake_generate(*, messages, system_prompt, thinking, tools):
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            raise OllamaError(
                "Ollama context overflow for 'stub-8k': "
                '{"error":"The prompt is too long: 99999, '
                'model maximum context length: 8192"}'
            )
        return ProviderResponse(
            text="recovered ok", parts=[], input_tokens=5, output_tokens=5, total_tokens=10
        )

    session._provider_generate_with_retry = fake_generate

    resp = _generate_with_overflow_recovery(
        session,
        messages=[],
        system_prompt="sys",
        thinking=False,
        tools=None,
        turn_start_index=0,
    )

    assert resp.text == "recovered ok"
    assert len(calls) == 2, "expected exactly one retry after compaction"
    assert session._overflow_recovered_this_turn is True
    # Compaction advanced the rolling-summary anchor (history was
    # summarized down to the last ~4 messages).
    assert session.session_manager.summary_anchor > anchor_before


def test_recovery_does_not_compact_on_non_overflow_error():
    """A non-overflow provider error re-raises immediately with no
    compaction — we don't summarise history for unrelated failures."""
    session = _make_session()
    _stuff_history(session.session_manager)
    anchor_before = session.session_manager.summary_anchor

    def fake_generate(*, messages, system_prompt, thinking, tools):
        raise ConnectionError("timeout")

    session._provider_generate_with_retry = fake_generate

    with pytest.raises(ConnectionError):
        _generate_with_overflow_recovery(
            session,
            messages=[],
            system_prompt="sys",
            thinking=False,
            tools=None,
            turn_start_index=0,
        )

    # No compaction should have run for a non-overflow error.
    assert session.session_manager.summary_anchor == anchor_before
    assert session._overflow_recovered_this_turn is False


# ---------------------------------------- reactive recovery: circuit breaker


def test_recovery_circuit_breaker_one_attempt_per_turn():
    """If the retry also overflows, the per-turn guard re-raises instead
    of compacting again (no compact-and-fail loop)."""
    session = _make_session()
    _stuff_history(session.session_manager)

    calls = []

    def always_overflow(*, messages, system_prompt, thinking, tools):
        calls.append(len(calls) + 1)
        raise OllamaError("Ollama context overflow for 'stub-8k': prompt too long")

    session._provider_generate_with_retry = always_overflow

    with pytest.raises(OllamaError):
        _generate_with_overflow_recovery(
            session,
            messages=[],
            system_prompt="sys",
            thinking=False,
            tools=None,
            turn_start_index=0,
        )

    # First call raises (caught), recovery compacts + retries once
    # (second call) which also raises → re-raise. Exactly 2 attempts.
    assert len(calls) == 2
    assert session._overflow_recovered_this_turn is True


def test_recovery_guard_skips_compaction_when_already_recovered():
    """If the guard is already set (recovery already ran this turn), a
    fresh overflow re-raises with NO additional compaction."""
    session = _make_session()
    _stuff_history(session.session_manager)
    session._overflow_recovered_this_turn = True
    anchor_before = session.session_manager.summary_anchor

    def always_overflow(*, messages, system_prompt, thinking, tools):
        raise OllamaError("Ollama context overflow for 'stub-8k': prompt too long")

    session._provider_generate_with_retry = always_overflow

    with pytest.raises(OllamaError):
        _generate_with_overflow_recovery(
            session,
            messages=[],
            system_prompt="sys",
            thinking=False,
            tools=None,
            turn_start_index=0,
        )

    # Guard short-circuited before compaction — anchor unchanged.
    assert session.session_manager.summary_anchor == anchor_before