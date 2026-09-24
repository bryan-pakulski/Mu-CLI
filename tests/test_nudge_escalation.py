"""context_packaging_v2 / P3-T8: pressure nudge escalation ladder.

The single nudge fired once per episode and went silent; a model that
ignored it rode to the ceiling. Sustained pressure now climbs
L1 nudge -> L2 stronger nudge -> L3 harness-forced compaction (once per
episode); an anchor advance resets the ladder.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import List

import pytest

from providers.base import LLMProvider, MessagePart, ProviderResponse


def _sn_session():
    _sm = SimpleNamespace(summary_anchor=0, history=[])
    return SimpleNamespace(variables={}, session_manager=_sm)


def _nudges(session):
    return [m["parts"][0]["text"] for m in session.session_manager.history
            if "CONTEXT PRESSURE" in m["parts"][0]["text"]]


def _hot(session, n=1):
    from mu.agent.context_guard import _maybe_nudge_context_pressure
    for _ in range(n):
        _maybe_nudge_context_pressure(session, limit=480_000, manifest={"total": 400_000})


def test_escalation_variable_registered():
    from utils.config import VARIABLE_SCHEMA

    assert VARIABLE_SCHEMA["context_pressure_escalate_after"]["default"] == 3


def test_ladder_climbs_l1_l2_l3_and_forces_once(monkeypatch):
    session = _sn_session()
    calls = []

    def fake_manual_compact(sess, **kw):
        calls.append(kw)
        sess.session_manager.summary_anchor  # unchanged: nothing to roll
        return {"ok": True, "compacted": False}

    monkeypatch.setattr("mu.agent.compactor.manual_compact", fake_manual_compact)
    emitted = []
    monkeypatch.setattr("mu.trace.emitter.emit_nudge",
                        lambda s, kind, it, **extra: emitted.append(kind))

    _hot(session, 1)
    assert len(_nudges(session)) == 1
    assert session._pressure_nudge_level == 1
    assert emitted == ["context_pressure"]

    _hot(session, 2)  # iters 2,3 -> still L1
    assert len(_nudges(session)) == 1
    _hot(session, 1)  # iter 4 -> L2
    assert len(_nudges(session)) == 2
    assert "escalated" in _nudges(session)[-1]
    assert session._pressure_nudge_level == 2
    assert emitted[-1] == "context_pressure_l2"

    _hot(session, 2)  # iters 5,6 -> still L2
    assert len(_nudges(session)) == 2
    _hot(session, 1)  # iter 7 -> L3 forced
    assert len(_nudges(session)) == 3
    assert "forced" in _nudges(session)[-1]
    assert session._pressure_nudge_level == 3
    assert emitted[-1] == "context_pressure_forced"
    assert len(calls) == 1

    _hot(session, 10)  # stays hot, no anchor move: nothing more, no second force
    assert len(_nudges(session)) == 3
    assert len(calls) == 1


def test_anchor_advance_resets_ladder(monkeypatch):
    session = _sn_session()
    monkeypatch.setattr("mu.trace.emitter.emit_nudge", lambda *a, **k: None)
    _hot(session, 4)  # L1 + L2
    assert session._pressure_nudge_level == 2
    session.session_manager.summary_anchor = 9  # compaction happened
    _hot(session, 1)
    assert session._pressure_nudge_level == 1  # fresh episode: L1 again
    assert session._pressure_nudge_iters_over == 1
    assert session._pressure_forced_this_episode is False
    assert len(_nudges(session)) == 3


def test_fill_drop_resets_ladder(monkeypatch):
    from mu.agent.context_guard import _maybe_nudge_context_pressure

    session = _sn_session()
    monkeypatch.setattr("mu.trace.emitter.emit_nudge", lambda *a, **k: None)
    _hot(session, 4)
    assert session._pressure_nudge_level == 2
    _maybe_nudge_context_pressure(session, limit=480_000, manifest={"total": 100_000})
    assert session._pressure_nudge_level == 0
    assert session._pressure_nudge_fired is False
    _hot(session, 1)
    assert session._pressure_nudge_level == 1


def test_escalation_disabled_keeps_single_nudge(monkeypatch):
    session = _sn_session()
    session.variables["context_pressure_escalate_after"] = 0
    monkeypatch.setattr("mu.trace.emitter.emit_nudge", lambda *a, **k: None)
    _hot(session, 12)
    assert len(_nudges(session)) == 1
    assert session._pressure_nudge_level == 1


# ------------------------------------------------------------------ real forced compaction


class _Summarizer(LLMProvider):
    def __init__(self):
        super().__init__("s")
        self.model_name = "s"

    def get_available_models(self) -> List[str]:
        return [self.model_name]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        text = "### Task\nt\n### Progress\np"
        return ProviderResponse(text=text, parts=[MessagePart(type="text", text=text)],
                                input_tokens=1, output_tokens=1, total_tokens=2)

    def stream(self, messages, system_prompt=None, thinking=False, tools=None):
        yield {"type": "text", "text": "ok"}

    def upload_file(self, file_path, mime_type):
        return None

    def effective_context_window(self, model_name=None):
        return 480_000


@pytest.fixture
def real_session(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.config.HISTORY_DIR", str(tmp_path / "history"))
    from mu.session.session import Session, SessionManager

    sm = SessionManager()
    sess = Session(_Summarizer(), False, "system", sm)
    sess.variables["agent_mode"] = "default"
    sess.variables["context_token_limit"] = 480_000
    return sess


def test_forced_level_runs_manual_compact_and_ledger_records_it(real_session, monkeypatch):
    sm = real_session.session_manager
    monkeypatch.setattr("mu.trace.emitter.emit_nudge", lambda *a, **k: None)
    # Two finished turns of chatter so manual_compact has something to roll.
    for t in range(2):
        sm.history.append({"role": "user", "parts": [{"type": "text", "text": f"ask {t}"}]})
        for i in range(6):
            sm.history.append({"role": "assistant", "parts": [{"type": "text", "text": "words " * 200}]})
        sm.history.append({"role": "assistant", "parts": [{"type": "text", "text": "done"}]})
    sm.history.append({"role": "user", "parts": [{"type": "text", "text": "current"}]})
    sm._active_turn_start_index = len(sm.history) - 1
    real_session._trace_current_iter = 5

    _hot(real_session, 7)  # L1 @1, L2 @4, L3 @7
    assert real_session._pressure_nudge_level == 3
    assert sm.summary_anchor > 0
    assert sm.conversation_summary
    kinds = [e["kind"] for e in (getattr(sm, "_compaction_log", None) or [])]
    assert "pressure_forced" in kinds
    forced_text = _nudges(real_session)[-1]
    assert "history summarized" in forced_text
