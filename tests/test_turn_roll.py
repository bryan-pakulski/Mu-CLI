"""context_packaging_v2 / P2-T6: roll an over-budget finished turn into L2.

After the fold and arg stubs, a long turn still leaves dozens of
assistant/tool messages verbatim. At turn end, if the finished turn projects
above ``turn_keep_budget_tokens`` it is summarized into the conversation
summary and the anchor advances past it. Regression target: trace
mucli_run_355d1b39c2a7 had summary_anchor=0 and L2=0 tokens for the whole
7-turn run.
"""

from __future__ import annotations

from typing import List

import pytest

from providers.base import LLMProvider, MessagePart, ProviderResponse


class _Summarizer(LLMProvider):
    """Returns a structured summary so roll_history_summary takes the LLM path."""

    def __init__(self, model_name="summ"):
        super().__init__(model_name)
        self.model_name = model_name
        self.calls = 0

    def get_available_models(self) -> List[str]:
        return [self.model_name]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        self.calls += 1
        text = "### Task\nFix compaction.\n### Progress\nRead files, edited budgets.\n### Key decisions\nDrift applies to all providers."
        return ProviderResponse(text=text, parts=[MessagePart(type="text", text=text)],
                                input_tokens=10, output_tokens=10, total_tokens=20)

    def stream(self, messages, system_prompt=None, thinking=False, tools=None):
        yield {"type": "text", "text": "ok"}

    def upload_file(self, file_path, mime_type):
        return None

    def effective_context_window(self, model_name=None):
        return 480_000


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.config.HISTORY_DIR", str(tmp_path / "history"))
    from mu.session.result_store import ResultStore
    from mu.session.session import Session, SessionManager

    sm = SessionManager()
    sess = Session(_Summarizer(), False, "system", sm)
    sess.variables["agent_mode"] = "default"
    sm.tool_result_cache.set_store(ResultStore("test", root=str(tmp_path / "results")))
    return sess


def _text(role, text):
    return {"role": role, "parts": [{"type": "text", "text": text}]}


def _turn(sm, *, n_results: int, payload: str, prefix: str):
    start = len(sm.history)
    sm.history.append(_text("user", f"{prefix} ask"))
    for i in range(n_results):
        cid = f"{prefix}{i}"
        sm.history.append({"role": "assistant", "parts": [{
            "type": "tool_call", "tool_name": "read_file",
            "tool_args": {"filename": f"{cid}.py"}, "tool_call_id": cid}]})
        sm.history.append({"role": "tool", "parts": [{
            "type": "tool_result", "tool_name": "read_file",
            "tool_result": payload, "tool_call_id": cid}]})
    sm.history.append(_text("assistant", f"{prefix} done"))
    sm._active_turn_start_index = start
    return start


def test_variables_registered():
    from utils.config import VARIABLE_SCHEMA

    assert VARIABLE_SCHEMA["turn_roll_enabled"]["default"] is True
    assert VARIABLE_SCHEMA["turn_keep_budget_tokens"]["default"] == 6000


def test_over_budget_turn_is_rolled_into_l2(session):
    from mu.session.context_maintenance import roll_completed_turn

    sm = session.session_manager
    session.variables["turn_keep_budget_tokens"] = 500
    start = _turn(sm, n_results=6, payload="line\n" * 400, prefix="a")
    assert sm.summary_anchor == 0
    assert not sm.conversation_summary

    report = roll_completed_turn(session, turn_start_index=start)

    assert report["rolled"] is True
    assert report["reason"] == "rolled"
    assert report["turn_tokens"] > 500
    assert report["anchor_after"] > report["anchor_before"]
    assert sm.summary_anchor == len(sm.history) - 1  # final answer stays live
    assert "### Progress" in sm.conversation_summary
    assert session.provider.calls == 1
    log = getattr(sm, "_compaction_log", None) or []
    entry = next(e for e in log if e["kind"] == "turn_roll")
    assert entry["anchor_delta"] > 0
    assert entry["summarizer"] == "llm"
    assert entry["turn_tokens"] == report["turn_tokens"]


def test_under_budget_turn_is_not_rolled(session):
    from mu.session.context_maintenance import roll_completed_turn

    sm = session.session_manager
    start = _turn(sm, n_results=1, payload="tiny", prefix="a")
    report = roll_completed_turn(session, turn_start_index=start)
    assert report["rolled"] is False
    assert report["reason"] == "under_budget"
    assert sm.summary_anchor == 0
    assert session.provider.calls == 0
    assert not (getattr(sm, "_compaction_log", None) or [])


def test_roll_disabled_is_a_noop(session):
    from mu.session.context_maintenance import roll_completed_turn

    sm = session.session_manager
    session.variables["turn_roll_enabled"] = False
    session.variables["turn_keep_budget_tokens"] = 100
    start = _turn(sm, n_results=6, payload="line\n" * 400, prefix="a")
    report = roll_completed_turn(session, turn_start_index=start)
    assert report["reason"] == "disabled"
    assert sm.summary_anchor == 0
    assert session.provider.calls == 0


def test_roll_only_covers_the_finished_turn_and_rearms_nudge(session):
    from mu.agent.context_guard import _maybe_nudge_context_pressure
    from mu.session.context_maintenance import roll_completed_turn

    sm = session.session_manager
    session.variables["turn_keep_budget_tokens"] = 500
    _turn(sm, n_results=3, payload="x\n" * 300, prefix="a")
    start_b = _turn(sm, n_results=6, payload="line\n" * 400, prefix="b")

    # A pressure nudge fired earlier in the turn and latched.
    session._pressure_nudge_fired = True
    session._pressure_nudge_armed_at = 0

    report = roll_completed_turn(session, turn_start_index=start_b)
    assert report["rolled"]
    assert sm.summary_anchor == len(sm.history) - 1
    # Everything through the finished turn is summarised; the nudge re-arms
    # on the next evaluation because the anchor advanced.
    _maybe_nudge_context_pressure(session, limit=480_000, manifest={"total": 1})
    assert session._pressure_nudge_fired is False
    assert session._pressure_nudge_armed_at == sm.summary_anchor


def test_mechanical_fallback_when_no_provider(session):
    from mu.session.context_maintenance import roll_completed_turn

    sm = session.session_manager
    session.variables["turn_keep_budget_tokens"] = 500
    start = _turn(sm, n_results=6, payload="line\n" * 400, prefix="a")
    session.provider = None
    report = roll_completed_turn(session, turn_start_index=start, provider=None)
    assert report["rolled"] is True
    assert sm.summary_anchor > 0
    assert sm.conversation_summary
    log = getattr(sm, "_compaction_log", None) or []
    assert any(e["kind"] == "turn_roll" and e["summarizer"] == "mechanical" for e in log)


def test_loop_seam_runs_fold_then_roll(session):
    from mu.agent import loop_body

    sm = session.session_manager
    session.variables["turn_keep_budget_tokens"] = 500
    start = _turn(sm, n_results=8, payload="line\n" * 400, prefix="a")
    result = loop_body._fold_turn_context(session, start, 3)
    assert result is not None
    assert len(result["changed"]) == 6  # fold ran first
    assert result["roll"]["rolled"] is True
    assert sm.summary_anchor == len(sm.history) - 1
    kinds = [e["kind"] for e in (getattr(sm, "_compaction_log", None) or [])]
    assert kinds.index("turn_fold") < kinds.index("turn_roll")
