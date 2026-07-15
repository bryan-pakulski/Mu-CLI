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

from mu.agent.loop_body import (
    _MAX_OVERFLOW_RECOVERIES_PER_TURN,
    _generate_with_overflow_recovery,
)
from mu.agent.retry import is_context_overflow_error, parse_overflow_token_counts
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
    assert session._overflow_recoveries_this_turn == 1
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
    assert session._overflow_recoveries_this_turn == 0


# ---------------------------------------- reactive recovery: circuit breaker


def test_recovery_escalation_loop_caps_attempts_per_turn():
    """If every provider call overflows, the escalation loop keeps
    compacting harder + retrying up to the per-turn cap, then re-raises
    instead of looping forever. The retry sits inside the try (it's the
    next loop iteration), so a still-overflowing retry is caught and
    re-compacted rather than surfacing immediately — the old code returned
    the retry outside the try, so it re-raised after a single retry."""
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

    # 3 compactions (escalating keep_recent 4→2→1) + 1 final call that
    # hits the cap and re-raises = cap + 1 provider calls.
    assert len(calls) == _MAX_OVERFLOW_RECOVERIES_PER_TURN + 1
    assert session._overflow_recoveries_this_turn == _MAX_OVERFLOW_RECOVERIES_PER_TURN


def test_recovery_guard_skips_compaction_when_already_recovered():
    """If the per-turn recovery cap is already reached, a fresh overflow
    re-raises with NO additional compaction (circuit breaker)."""
    session = _make_session()
    _stuff_history(session.session_manager)
    session._overflow_recoveries_this_turn = _MAX_OVERFLOW_RECOVERIES_PER_TURN
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


# ---------------------------------------- ground-truth drift parsing


def test_parse_overflow_token_counts_real_ollama_wording():
    """The actual daemon message carries the real prompt + max token
    counts — these are the ground truth that drives drift-corrected
    reactive compaction."""
    err = OllamaError(
        'Ollama context overflow for \'glm-5.2\': {"error":"The prompt is '
        'too long: 1068887, model maximum context length: 1000000 '
        '(ref: 93d337ec-22fb-49b9-8153-7603bc6a87a3)"}'
    )
    real_prompt, real_max = parse_overflow_token_counts(err)
    assert real_prompt == 1068887
    assert real_max == 1000000


def test_parse_overflow_token_counts_missing_returns_none():
    """An overflow error without the numeric counts returns None for both
    — the drift ratio then falls back to the provider safety factor."""
    err = OllamaError("Ollama context overflow for 'stub-8k': prompt too long")
    real_prompt, real_max = parse_overflow_token_counts(err)
    assert real_prompt is None
    assert real_max is None


def test_parse_overflow_token_counts_inspects_cause():
    """The real body rides on __cause__ when wrapped by a transport layer."""
    inner = OllamaError(
        "The prompt is too long: 99999, model maximum context length: 8192"
    )
    wrapped = RuntimeError("transport failed")
    wrapped.__cause__ = inner
    real_prompt, real_max = parse_overflow_token_counts(wrapped)
    assert real_prompt == 99999
    assert real_max == 8192


# ---------------------------------------- drift-corrected budget


def test_overflow_drift_ratio_uses_ground_truth():
    """Drift = real tokens / cl100k estimate of the same prompt, clamped to
    a sane band. With a tiny cl100k estimate and a huge real count, the
    ratio clamps to 6.0 rather than blowing up the budget."""
    from mu.agent.loop_body import _overflow_drift_ratio

    session = _make_session()
    err = OllamaError(
        "Ollama context overflow for 'glm-5.2': The prompt is too long: "
        "1068887, model maximum context length: 1000000"
    )
    drift = _overflow_drift_ratio(session, "sys", [], err)
    assert drift == 6.0  # clamped from ~1e6


def test_overflow_drift_ratio_falls_back_to_safety_factor():
    """When the error carries no real counts, drift falls back to the
    provider's static compaction_safety_factor (1.0 for the stub)."""
    from mu.agent.loop_body import _overflow_drift_ratio

    session = _make_session()
    err = OllamaError("Ollama context overflow for 'stub-8k': prompt too long")
    drift = _overflow_drift_ratio(session, "sys", [], err)
    assert drift == 1.0


# ---------------------------------------- per-turn recovery cap


def test_recovery_allows_multiple_recoveries_per_turn():
    """A later iteration that overflows again gets its own recovery — the
    per-turn guard is a capped count, not a boolean, so the second overflow
    compacts + retries instead of re-raising immediately (the old boolean
    guard would have re-raised it)."""
    session = _make_session()
    _stuff_history(session.session_manager)

    calls = []

    def fake_generate(*, messages, system_prompt, thinking, tools):
        calls.append(len(calls) + 1)
        # Every odd call (the first in each invocation) overflows; the
        # retry (even call) succeeds.
        if len(calls) % 2 == 1:
            raise OllamaError("Ollama context overflow for 'stub-8k': prompt too long")
        return ProviderResponse(
            text="ok", parts=[], input_tokens=5, output_tokens=5, total_tokens=10
        )

    session._provider_generate_with_retry = fake_generate
    for _ in range(2):
        resp = _generate_with_overflow_recovery(
            session, messages=[], system_prompt="sys",
            thinking=False, tools=None, turn_start_index=0,
        )
        assert resp.text == "ok"

    assert session._overflow_recoveries_this_turn == 2
    # 2 invocations × (1 overflow + 1 success) = 4 provider calls.
    assert len(calls) == 4


def test_recovery_cap_re_raises_after_max_per_turn():
    """After _MAX_OVERFLOW_RECOVERIES_PER_TURN recoveries, a further
    overflow re-raises with no compaction (circuit breaker)."""
    session = _make_session()
    _stuff_history(session.session_manager)

    calls = []

    def fake_generate(*, messages, system_prompt, thinking, tools):
        calls.append(len(calls) + 1)
        if len(calls) % 2 == 1:
            raise OllamaError("Ollama context overflow for 'stub-8k': prompt too long")
        return ProviderResponse(
            text="ok", parts=[], input_tokens=5, output_tokens=5, total_tokens=10
        )

    session._provider_generate_with_retry = fake_generate
    # Up to the cap, each overflow recovers (2 calls each).
    for _ in range(_MAX_OVERFLOW_RECOVERIES_PER_TURN):
        _generate_with_overflow_recovery(
            session, messages=[], system_prompt="sys",
            thinking=False, tools=None, turn_start_index=0,
        )
    assert session._overflow_recoveries_this_turn == _MAX_OVERFLOW_RECOVERIES_PER_TURN

    # The next overflow hits the cap → re-raises before compaction (only
    # the single raising call, no retry).
    with pytest.raises(OllamaError):
        _generate_with_overflow_recovery(
            session, messages=[], system_prompt="sys",
            thinking=False, tools=None, turn_start_index=0,
        )
    assert len(calls) == _MAX_OVERFLOW_RECOVERIES_PER_TURN * 2 + 1


# ---------------------------------------- escalation ladder


def test_recovery_escalation_ladder_compacts_harder_each_retry():
    """Each still-overflowing retry in a single invocation compacts
    harder: keep_recent shrinks (4→2→1), the drift margin grows
    (0.20→0.30→0.40), and the tool-result floor is lifted from the 2nd
    recovery on. A turn that overflows twice then succeeds recovers (no
    surface) with the escalation params applied in order."""
    import mu.agent.loop_body as lb

    session = _make_session()
    _stuff_history(session.session_manager)

    calls = []

    def overflows_twice_then_succeeds(*, messages, system_prompt, thinking, tools):
        calls.append(len(calls) + 1)
        if len(calls) <= 2:
            raise OllamaError("Ollama context overflow for 'stub-8k': prompt too long")
        return ProviderResponse(
            text="ok", parts=[], input_tokens=5, output_tokens=5, total_tokens=10
        )

    session._provider_generate_with_retry = overflows_twice_then_succeeds

    compact_calls = []
    orig = lb._aggressive_compact_for_overflow

    def spy(sess, sp, msgs, *, overflow_error=None, keep_recent=4,
            margin=0.20, lift_floor=False):
        compact_calls.append((keep_recent, round(margin, 2), lift_floor))
        return orig(
            sess, sp, msgs, overflow_error=overflow_error,
            keep_recent=keep_recent, margin=margin, lift_floor=lift_floor,
        )

    lb._aggressive_compact_for_overflow = spy
    try:
        resp = _generate_with_overflow_recovery(
            session, messages=[], system_prompt="sys",
            thinking=False, tools=None, turn_start_index=0,
        )
    finally:
        lb._aggressive_compact_for_overflow = orig

    assert resp.text == "ok"
    # Escalation ladder in order: keep_recent shrinks, margin grows, floor
    # lifted from the 2nd recovery on.
    assert compact_calls == [(4, 0.20, False), (2, 0.30, True)]
    assert session._overflow_recoveries_this_turn == 2
    # 2 overflows + 1 success = 3 provider calls.
    assert len(calls) == 3


# ---------------------------------------- emergency floor-lift


def _big_tool_result(label: str, n_chars: int = 100_000) -> str:
    """Realistic varied tool-output text of roughly `n_chars` characters.

    A single-character run (``"X" * 100_000``) is pathological input for
    tiktoken's BPE — cl100k_base takes ~2.3s to encode it, which made this
    test take 74s (the compactor re-estimates the whole history, including
    four such payloads, on every pass). Real tool output is varied text
    that tiktoken encodes ~200x faster, so this keeps the floor-lift
    exercise intact without the pathological slowdown.
    """
    import json as _json

    unit = _json.dumps(
        {"tool": label, "path": "/a/b/c.py", "line": 1, "content": "def foo(): return 42\n"}
    )
    repeats = max(1, n_chars // len(unit) + 1)
    return (unit * repeats)[:n_chars]


def test_recovery_floor_lift_when_protected_tool_results_block_compaction():
    """When the per-turn tool-result floor protects recent large tool
    results that alone exceed the budget, a compaction with the floor in
    place can't reach the budget (the floor clamps the anchor + skips them
    from degradation). The escalation path lifts the floor
    (`lift_floor=True`), letting those protected results be summarized so
    L5 fits — better to lose a just-read payload than crash the turn with a
    hard overflow. The floor is restored afterward so normal compaction
    keeps FM-8 protection.
    """
    from mu.agent.loop_body import _aggressive_compact_for_overflow

    provider = _StubProvider("stub-100k", window=100_000)
    session = _make_session(provider)
    sm = session.session_manager
    session.variables["tool_result_floor"] = 4

    # Active turn starts at index 2; four large tool results at the tail.
    # Each ~100k chars of varied text ≈ ~35k cl100k tokens, so the four
    # together (~140k) exceed the ~79.5k budget and the floor keeps them
    # verbatim unless lifted.
    sm.history = [
        {"role": "user", "parts": [{"type": "text", "text": "old q"}]},
        {"role": "assistant", "parts": [{"type": "text", "text": "old a"}]},
        {"role": "user", "parts": [{"type": "text", "text": "q"}]},
        {"role": "tool", "parts": [{"type": "tool_result", "tool_result": _big_tool_result("X")}]},
        {"role": "assistant", "parts": [{"type": "text", "text": "a"}]},
        {"role": "tool", "parts": [{"type": "tool_result", "tool_result": _big_tool_result("Y")}]},
        {"role": "assistant", "parts": [{"type": "text", "text": "a"}]},
        {"role": "tool", "parts": [{"type": "tool_result", "tool_result": _big_tool_result("Z")}]},
        {"role": "assistant", "parts": [{"type": "text", "text": "a"}]},
        {"role": "tool", "parts": [{"type": "tool_result", "tool_result": _big_tool_result("W")}]},
    ]
    sm.summary_anchor = 0
    sm.conversation_summary = ""
    sm._active_turn_start_index = 2

    # With the floor in place (lift_floor=False) the four protected tool
    # results keep L5 ~140k — well over the ~79.5k budget — so the
    # compaction can't reach it. Lifting the floor lets the oldest two be
    # summarized into L2, dropping L5 to ~70k (the remaining two stay in
    # the keep_recent=4 tail).
    err = OllamaError("Ollama context overflow for 'stub-100k': prompt too long")
    _aggressive_compact_for_overflow(
        session, "sys", messages=[], overflow_error=err, lift_floor=True
    )

    after_tokens = sm.estimate_runtime_history_tokens()
    assert after_tokens < 80_000, after_tokens
    # The floor was restored so later in-turn compaction keeps FM-8 protection.
    assert sm._tool_result_floor == 4