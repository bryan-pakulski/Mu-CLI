"""context_packaging_v2 / P3-T7: summarizer-free in-turn working-set cap.

With ``auto_compaction_enabled=False`` the old compactor hook returned before
its cheap clearing step, so a long turn rode to the provider ceiling with
every completed tool payload verbatim (trace mucli_run_355d1b39c2a7: 152k
tokens of tool results at 91% real fill, zero clears). The clearing step now
runs regardless of the opt-out once the drift-corrected fill reaches the
pressure threshold; summarization stays gated.
"""

from __future__ import annotations

from typing import List

import pytest

from mu.agent.hooks import HookContext
from providers.base import LLMProvider, MessagePart, ProviderResponse


class _Provider(LLMProvider):
    def __init__(self, window: int):
        super().__init__("p")
        self.model_name = "p"
        self._window = window
        self.generate_calls = 0

    def get_available_models(self) -> List[str]:
        return [self.model_name]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        self.generate_calls += 1
        return ProviderResponse(text="### Progress\nsummary", parts=[], input_tokens=1,
                                output_tokens=1, total_tokens=2)

    def stream(self, messages, system_prompt=None, thinking=False, tools=None):
        yield {"type": "text", "text": "ok"}

    def upload_file(self, file_path, mime_type):
        return None

    def effective_context_window(self, model_name=None):
        return self._window

    def effective_response_reserve(self, model_name=None):
        return 256


WINDOW = 40_000


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.config.HISTORY_DIR", str(tmp_path / "history"))
    from mu.session.result_store import ResultStore
    from mu.session.session import Session, SessionManager

    sm = SessionManager()
    sess = Session(_Provider(WINDOW), False, "system", sm)
    sess.variables["agent_mode"] = "default"
    sess.variables["auto_compaction_enabled"] = False
    sess.variables["context_token_limit"] = WINDOW
    sm.tool_result_cache.set_store(ResultStore("test", root=str(tmp_path / "results")))
    return sess


def _fill_turn(sm, *, n_results: int, payload: str, start_turn: bool = True):
    if start_turn:
        sm._active_turn_start_index = len(sm.history)
        sm.history.append({"role": "user", "parts": [{"type": "text", "text": "ask"}]})
    for i in range(n_results):
        cid = f"c{len(sm.history)}"
        sm.history.append({"role": "assistant", "parts": [{
            "type": "tool_call", "tool_name": "read_file",
            "tool_args": {"filename": f"{cid}.py"}, "tool_call_id": cid}]})
        sm.history.append({"role": "tool", "parts": [{
            "type": "tool_result", "tool_name": "read_file",
            "tool_result": payload, "tool_call_id": cid}]})


def _fill_pct(session):
    from mu.session.budgets import effective_fill
    from mu.session.context_maintenance import projected_tokens
    return effective_fill(session, projected_tokens(session))["fill_pct"]


def _ctx(session):
    return HookContext(point="pre_provider_call", session=session,
                       variables=session.variables, system_prompt="sys", tools=None)


def _cleared(session):
    return [v for v in session.session_manager.context_retention.values() if v["action"] == "clear"]


def test_pressure_clear_runs_with_auto_compaction_disabled(session):
    from mu.agent.compactor import _compact_history

    sm = session.session_manager
    _fill_turn(sm, n_results=20, payload="tok " * 1800)  # ~36k tokens on a 40k window
    assert _fill_pct(session) >= 80
    before = _fill_pct(session)

    result = _compact_history(_ctx(session))

    assert result is not None and result.data.get("compaction") is True
    clearing = result.data["clearing"]
    assert clearing["summarizer_calls"] == 0
    assert session.provider.generate_calls == 0  # no summarizer
    assert clearing["changed"]
    assert sm.summary_anchor == 0  # no summarization happened
    after = _fill_pct(session)
    assert after < before
    assert after <= 70 or len(_cleared(session)) == len(clearing["changed"])
    kinds = [e["kind"] for e in (getattr(sm, "_compaction_log", None) or [])]
    assert "pressure_clear" in kinds
    assert "result_clear" not in kinds


def test_pressure_clear_below_threshold_is_noop(session):
    from mu.agent.compactor import _compact_history

    sm = session.session_manager
    _fill_turn(sm, n_results=4, payload="tok " * 500)  # well under 80%
    assert _fill_pct(session) < 80
    assert _compact_history(_ctx(session)) is None
    assert not _cleared(session)
    assert not (getattr(sm, "_compaction_log", None) or [])


def test_pressure_clear_uses_drift_corrected_fill(session):
    """Same cl100k history: under threshold at drift 1.0, over it at 1.6x."""
    from mu.agent.compactor import _compact_history
    from mu.session.budgets import update_observed_drift

    sm = session.session_manager
    _fill_turn(sm, n_results=14, payload="tok " * 1500)  # ~21k cl100k ≈ 53%
    assert _fill_pct(session) < 80
    assert _compact_history(_ctx(session)) is None

    update_observed_drift(session, 1.6)  # real ≈ 85%
    assert _fill_pct(session) >= 80
    result = _compact_history(_ctx(session))
    assert result is not None and result.data["clearing"]["changed"]


def test_pressure_clear_respects_protected_recent_and_signed(session):
    from mu.agent.compactor import _compact_history

    sm = session.session_manager
    _fill_turn(sm, n_results=20, payload="tok " * 1800)
    # index layout: 0 user, then (call, result) pairs at (1,2),(3,4),...
    sm.history[2]["parts"][0]["tool_result"] = "Error: failed"        # failed -> protected
    sm.history[4]["parts"][0]["thought_signature"] = "sig"            # provider-signed
    sm.protected_indices = {6}                                        # pinned
    result = _compact_history(_ctx(session))
    assert result is not None
    touched = {v["history_index"] for v in _cleared(session)}
    assert not touched & {2, 4, 6}
    # The tool_result_floor (last 4 results of the active turn) stays verbatim.
    floor_indexes = {len(sm.history) - 1, len(sm.history) - 3, len(sm.history) - 5, len(sm.history) - 7}
    assert not touched & floor_indexes


def test_pressure_clear_stops_once_comfortably_under_threshold(session):
    """Clearing proceeds oldest-first in batches and stops ~10 points below
    the threshold instead of archiving everything eligible."""
    from mu.agent.compactor import _compact_history

    sm = session.session_manager
    _fill_turn(sm, n_results=40, payload="tok " * 900)  # ~36k tokens, 40 results
    result = _compact_history(_ctx(session))
    assert result is not None
    changed = result.data["clearing"]["changed"]
    assert 0 < len(changed) < 36  # 36 eligible (40 minus floor 4); not all cleared
    touched = sorted(v["history_index"] for v in _cleared(session))
    assert touched == sorted(touched)
    assert touched[0] == 2  # oldest first
    assert result.data["clearing"]["fill_after"] <= 70.5


def test_pressure_clear_requires_durable_store(session):
    from mu.agent.compactor import _compact_history

    sm = session.session_manager
    sm.tool_result_cache.set_store(None)
    _fill_turn(sm, n_results=20, payload="tok " * 1800)
    assert _compact_history(_ctx(session)) is None
    assert not _cleared(session)


def test_pressure_clear_then_summarization_when_auto_enabled(session):
    """With auto compaction ON the cheap clear still runs first; the hook
    result reports a compaction either way."""
    from mu.agent.compactor import _compact_history

    sm = session.session_manager
    session.variables["auto_compaction_enabled"] = True
    _fill_turn(sm, n_results=20, payload="tok " * 1800)
    result = _compact_history(_ctx(session))
    assert result is not None and result.data.get("compaction") is True
    kinds = [e["kind"] for e in (getattr(sm, "_compaction_log", None) or [])]
    assert "pressure_clear" in kinds
