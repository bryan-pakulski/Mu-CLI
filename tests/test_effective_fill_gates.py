"""context_packaging_v2 / P1-T3: every pressure gate reasons about the REAL
provider prompt size.

Regression target: trace mucli_run_355d1b39c2a7 — Anthropic 480k window,
cl100k estimate peaked at 263k (55%) while the provider billed 435k (91%).
Drift 1.67x was learned but ignored: the 80% nudge never fired and preflight
would only have tripped at ~795k real tokens.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import List, Optional
from unittest.mock import MagicMock

from mu.session.manager import SessionManager
from mu.session.session import Session
from providers.base import LLMProvider, MessagePart, ProviderResponse


class _Anthropicish(LLMProvider):
    """Factor-1.0 provider with a 480k window (Anthropic/OpenAI shape)."""

    def __init__(self, window: int = 480_000):
        super().__init__("big")
        self._window = window
        self.model_name = "big"

    def get_available_models(self) -> List[str]:
        return [self.model_name]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        return ProviderResponse(text="ok", parts=[], input_tokens=1, output_tokens=1, total_tokens=2)

    def stream(self, messages, system_prompt=None, thinking=False, tools=None):
        yield {"type": "text", "text": "ok"}

    def upload_file(self, file_path, mime_type):
        return None

    def effective_context_window(self, model_name: Optional[str] = None):
        return self._window

    def effective_response_reserve(self, model_name: Optional[str] = None):
        return 4096


def _session(window: int = 480_000) -> Session:
    sm = SessionManager()
    session = Session(_Anthropicish(window), False, "sys", sm)
    session.agentic = False
    session.variables["context_token_limit"] = window
    return session


# ------------------------------------------------------------------ effective_fill


def test_effective_fill_projects_cl100k_by_learned_drift():
    from mu.session.budgets import effective_fill, update_observed_drift

    session = _session()
    info = effective_fill(session, 263_000)
    assert info["drift_ratio"] == 1.0
    assert info["real_tokens"] == 263_000
    assert info["real_limit"] == 480_000
    assert round(info["fill_pct"]) == 55
    assert info["source"] == "cl100k_drift"

    update_observed_drift(session, 1.67)
    info = effective_fill(session, 263_000)
    assert info["real_tokens"] == int(263_000 * 1.67)  # ~439k
    assert info["real_limit"] == 480_000  # window is the real-frame ceiling
    assert round(info["fill_pct"]) == 92
    assert info["corrected_limit"] == int(480_000 / 1.67)


def test_effective_fill_prefers_same_iteration_provider_count():
    from mu.session.budgets import effective_fill

    session = _session()
    session._trace_current_iter = 12
    session._last_prompt_tokens_real = 435_000
    session._last_prompt_tokens_real_iter = 12
    info = effective_fill(session, 263_000)
    assert info["source"] == "provider_usage"
    assert info["real_tokens"] == 435_000
    assert round(info["fill_pct"]) == 91

    # Stale (previous iteration) ground truth is not trusted over the projection.
    session._trace_current_iter = 13
    info = effective_fill(session, 263_000)
    assert info["source"] == "cl100k_drift"
    assert info["real_tokens"] == 263_000


def test_effective_fill_frames_agree_on_percentage():
    """cl100k total vs corrected limit == real total vs window."""
    from mu.session.budgets import (
        drift_corrected_context_limit,
        effective_fill,
        update_observed_drift,
    )

    session = _session()
    update_observed_drift(session, 1.67)
    cl100k = 263_000
    corrected = drift_corrected_context_limit(session)
    cl100k_frame = cl100k / corrected * 100
    real_frame = effective_fill(session, cl100k)["fill_pct"]
    assert abs(cl100k_frame - real_frame) < 0.5


# ------------------------------------------------------------------ nudge gate


def _nudge_session(drift: float):
    from mu.session.budgets import drift_corrected_context_limit, update_observed_drift

    session = _session()
    session.session_manager.history = []
    if drift != 1.0:
        update_observed_drift(session, drift)
    return session, drift_corrected_context_limit(session)


def _count_nudges(session) -> int:
    return sum(
        1 for m in session.session_manager.history if "CONTEXT PRESSURE" in str(m.get("parts"))
    )


def test_nudge_fires_when_drift_corrected_fill_crosses_threshold():
    """263k cl100k / 480k = 55% (no nudge under the old rule); with the
    learned 1.67x drift the real fill is ~92% -> nudge fires."""
    from mu.agent.context_guard import _maybe_nudge_context_pressure

    session, limit = _nudge_session(1.67)
    assert limit == int(480_000 / 1.67)
    _maybe_nudge_context_pressure(session, limit=limit, manifest={"total": 263_000})
    assert _count_nudges(session) == 1
    text = session.session_manager.history[-1]["parts"][0]["text"]
    assert "92%" in text
    assert "480,000 provider tokens" in text
    assert "439," in text  # ~439k real tokens, not the 263k cl100k figure


def test_nudge_silent_without_drift_at_same_cl100k_total():
    from mu.agent.context_guard import _maybe_nudge_context_pressure

    session, limit = _nudge_session(1.0)
    assert limit == 480_000
    _maybe_nudge_context_pressure(session, limit=limit, manifest={"total": 263_000})
    assert _count_nudges(session) == 0


# ------------------------------------------------------------------ preflight gate


def _preflight_stub(window: int, drift: float):
    """MagicMock session in the shape tests/test_preflight_context_check.py
    uses, with a real learned drift ratio attached."""
    session = MagicMock()
    session.variables = {"context_token_limit": window}
    provider = MagicMock()
    provider.effective_context_window.return_value = window
    provider.effective_response_reserve.return_value = 100
    provider.compaction_safety_factor.return_value = 1.0
    session.provider = provider
    session._observed_drift_ratio = drift
    session.session_manager = MagicMock()
    session.session_manager.roll_history_summary_to_token_budget = MagicMock(return_value=True)
    session.session_manager.history = []
    session._build_messages_from_history = MagicMock(return_value=[])
    session.system_instruction = "stub system"
    session._inject_hierarchical_context = MagicMock(side_effect=lambda base, cached_skills=None: base)
    return session


def test_preflight_triggers_when_drift_corrected_total_exceeds_window():
    """cl100k total ~2k on a 3k window: fits at drift 1.0, overflows once a
    1.7x drift is learned (real ~3.4k > 3k - reserve)."""
    from mu.agent.context_guard import _preflight_context_check
    from providers.base import Message

    prompt = "x " * 1000  # ~1000 cl100k tokens
    messages = [Message(role="user", parts=[MessagePart(type="text", text="y " * 1000)])]

    calm = _preflight_stub(3_000, drift=1.0)
    _preflight_context_check(calm, prompt, messages)
    assert calm.session_manager.roll_history_summary_to_token_budget.call_count == 0

    hot = _preflight_stub(3_000, drift=1.7)
    _preflight_context_check(hot, prompt, messages)
    assert hot.session_manager.roll_history_summary_to_token_budget.call_count >= 1
    # Guard-effective limit is the drift-corrected one.
    assert hot._last_effective_limit == int(3_000 / 1.7)


# ------------------------------------------------------------------ context_status


def test_context_status_reports_real_frame_fields():
    from mu.session.budgets import update_observed_drift
    from mu.session.context_maintenance import context_status

    session = _session()
    update_observed_drift(session, 1.67)
    session._request_estimate_manifest = {"system": 7_000, "tools": 13_000, "total": 20_000}
    status = context_status(session)
    for key in ("fill_pct_real", "real_request_tokens", "real_context_limit", "drift_ratio", "fill_source"):
        assert key in status, key
    assert status["drift_ratio"] == 1.67
    assert status["real_context_limit"] == 480_000
    assert status["real_request_tokens"] == int(status["request_tokens"] * 1.67)
    assert status["effective_context_limit"] == int(480_000 / 1.67)


# ------------------------------------------------------------------ trace iter record


def test_iter_record_carries_fill_pct_real():
    from mu.session.budgets import update_observed_drift
    from mu.trace.emitter import build_iter_record

    session = _session()
    update_observed_drift(session, 1.67)
    session._last_effective_limit = int(480_000 / 1.67)

    resp = SimpleNamespace(
        input_tokens=2_000, output_tokens=5, cached_tokens=430_000, reasoning_tokens=0,
        cache_read_tokens=430_000, cache_creation_tokens=3_000,
        parts=[MessagePart(type="text", text="hi")],
    )
    rec = build_iter_record(
        session, iteration=47, max_iter=100, response=resp, total_in=0, total_out=0,
        total_cost=0.0, has_text=True, has_tool_call=False, iter_start=time.monotonic(),
        cost_delta=0.0, request_token_estimate=263_000,
    )
    ctx = rec["context"]
    assert "fill_pct_real" in ctx
    assert round(ctx["fill_pct_real"]) == 92
    assert ctx["drift_ratio"] == 1.67
    assert ctx["prompt_tokens_real_est"] == int(263_000 * 1.67)
