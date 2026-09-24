"""context_packaging_v2 / P5-T11: multi-turn replay regression.

Synthetic replay of the shape recorded in trace mucli_run_355d1b39c2a7:
7 turns x ~40 iterations, ~4k-token tool results, ~3k-token write_file
arguments every 5th call, Anthropic-style provider (480k window, factor
1.0) with a learned 1.67x cl100k->real drift, ``auto_compaction_enabled``
OFF (the recorded session's saved opt-out).

The recorded run peaked at 435k real tokens (91%) with summary_anchor=0
and L2 empty for the whole run. The replay drives the same seams the agent
loop uses — the pre-provider compactor hook (pressure clear), arg stubs in
the projection, and the turn-end fold + roll — and asserts the peak
drift-corrected projected request stays under 50% of the window while the
finished turns leave L2 non-empty.
"""

from __future__ import annotations

from typing import List

import pytest

from mu.agent.hooks import HookContext
from providers.base import LLMProvider, MessagePart, ProviderResponse

WINDOW = 480_000
DRIFT = 1.67
TURNS = 7
ITERS_PER_TURN = 40
RESULT_PAYLOAD = "tok " * 3000          # ~3k cl100k tokens per result
WRITE_BODY = "line of code\n" * 1000    # ~3k cl100k tokens per write_file
FIXED_PROMPT = "policy " * 20_000       # ~20k cl100k tokens (system + tools)


class _Summarizer(LLMProvider):
    def __init__(self):
        super().__init__("anthropic-ish")
        self.model_name = "anthropic-ish"
        self.summaries = 0

    def get_available_models(self) -> List[str]:
        return [self.model_name]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        self.summaries += 1
        text = "### Task\nreplay\n### Progress\nturn summarised\n### Key decisions\nkeep going"
        return ProviderResponse(text=text, parts=[MessagePart(type="text", text=text)],
                                input_tokens=1, output_tokens=1, total_tokens=2)

    def stream(self, messages, system_prompt=None, thinking=False, tools=None):
        yield {"type": "text", "text": "ok"}

    def upload_file(self, file_path, mime_type):
        return None

    def effective_context_window(self, model_name=None):
        return WINDOW

    def effective_response_reserve(self, model_name=None):
        return 4096


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.config.HISTORY_DIR", str(tmp_path / "history"))
    from mu.session.budgets import update_observed_drift
    from mu.session.result_store import ResultStore
    from mu.session.session import Session, SessionManager

    sm = SessionManager()
    sess = Session(_Summarizer(), False, "system", sm)
    sess.variables["agent_mode"] = "default"
    sess.variables["auto_compaction_enabled"] = False   # the recorded opt-out
    sess.variables["context_token_limit"] = WINDOW
    sm.tool_result_cache.set_store(ResultStore("replay", root=str(tmp_path / "results")))
    update_observed_drift(sess, DRIFT)
    return sess


def _real_fill(session) -> float:
    from mu.agent.context_guard import _estimate_request_tokens
    from mu.session.budgets import effective_fill
    from mu.session.context_maintenance import projected_tokens

    fixed = _estimate_request_tokens(FIXED_PROMPT, [], None)["total"]
    return effective_fill(session, fixed + projected_tokens(session))["fill_pct"]


def _replay(session, *, turns=TURNS, iters=ITERS_PER_TURN):
    """Drive the harness seams exactly as loop_body does, without a provider
    round-trip per iteration. Returns per-iteration real fill% samples."""
    from mu.agent import loop_body
    from mu.agent.compactor import _compact_history

    sm = session.session_manager
    fills = []
    call_no = 0
    for t in range(turns):
        turn_start = len(sm.history)
        sm.history.append({"role": "user", "parts": [{"type": "text", "text": f"turn {t}: keep working"}]})
        sm._active_turn_start_index = turn_start
        session._current_turn_start_index = turn_start
        for i in range(iters):
            call_no += 1
            session._trace_current_iter = i + 1
            # pre_provider_call hook (pressure clear / auto compaction).
            _compact_history(HookContext(point="pre_provider_call", session=session,
                                         variables=session.variables,
                                         system_prompt=FIXED_PROMPT, tools=None))
            fills.append(_real_fill(session))
            cid = f"t{t}i{i}"
            if call_no % 5 == 0:
                call = {"type": "tool_call", "tool_name": "write_file",
                        "tool_args": {"filename": f"f{call_no}.py", "content": WRITE_BODY},
                        "tool_call_id": cid}
                result = "Successfully wrote file."
            else:
                call = {"type": "tool_call", "tool_name": "read_file",
                        "tool_args": {"filename": f"f{call_no}.py"}, "tool_call_id": cid}
                result = RESULT_PAYLOAD
            sm.history.append({"role": "assistant", "parts": [call]})
            sm.history.append({"role": "tool", "parts": [{
                "type": "tool_result", "tool_name": call["tool_name"],
                "tool_result": result, "tool_call_id": cid}]})
        sm.history.append({"role": "assistant", "parts": [{"type": "text", "text": f"turn {t} done"}]})
        # Turn-end seam: fold + roll.
        loop_body._fold_turn_context(session, turn_start, iters)
    return fills


def test_replay_keeps_peak_real_fill_under_half_the_window(session):
    """Full 7 x 40 replay: peak real fill < 50% (recorded run: 91%), every
    finished turn folded + rolled into L2, anchor tracks the live tail."""
    from mu.session.context_maintenance import projected_tokens

    sm = session.session_manager
    fills = _replay(session)
    peak = max(fills)
    assert peak < 50.0, f"peak real fill {peak:.1f}% (recorded run: 91%)"
    # Sanity: the replay carried real load — the raw, unmanaged shape would
    # have been far beyond the window (7 x 40 x ~3k x 1.67 ≈ 1.4M tokens).
    assert peak > 5.0

    # Every finished turn was rolled: one summarizer call per turn, L2
    # non-empty, anchor right behind the final answer.
    assert session.provider.summaries == TURNS
    assert sm.summary_anchor == len(sm.history) - 1
    assert sm.conversation_summary.startswith("### Task")  # structured L2, non-empty
    assert projected_tokens(session) < 5_000
    kinds = [e["kind"] for e in (getattr(sm, "_compaction_log", None) or [])]
    assert kinds.count("turn_roll") == TURNS
    assert kinds.count("turn_fold") == TURNS


def test_replay_anchor_advances_after_first_turn(session):
    sm = session.session_manager
    _replay(session, turns=1)
    assert sm.summary_anchor > 0
    assert sm.conversation_summary
    assert sm.summary_anchor == len(sm.history) - 1


def test_replay_pressure_clear_engages_inside_long_turn(session):
    """Within one very long turn (before any turn-end seam), the in-turn
    pressure clear alone must hold the real fill below the nudge line."""
    from mu.agent.compactor import _compact_history

    sm = session.session_manager
    sm.history.append({"role": "user", "parts": [{"type": "text", "text": "long turn"}]})
    sm._active_turn_start_index = 0
    fills = []
    for i in range(90):
        _compact_history(HookContext(point="pre_provider_call", session=session,
                                     variables=session.variables,
                                     system_prompt=FIXED_PROMPT, tools=None))
        fills.append(_real_fill(session))
        cid = f"c{i}"
        sm.history.append({"role": "assistant", "parts": [{
            "type": "tool_call", "tool_name": "read_file",
            "tool_args": {"filename": f"{cid}.py"}, "tool_call_id": cid}]})
        sm.history.append({"role": "tool", "parts": [{
            "type": "tool_result", "tool_name": "read_file",
            "tool_result": RESULT_PAYLOAD, "tool_call_id": cid}]})
    assert max(fills) < 85.0, max(fills)
    assert sm.summary_anchor == 0  # no summarizer: cleared, not compacted
    assert session.provider.summaries == 0
    kinds = {e["kind"] for e in (getattr(sm, "_compaction_log", None) or [])}
    assert kinds == {"pressure_clear"}


def test_replay_without_packaging_would_exceed_the_window(session):
    """Control: disable every new mechanism and confirm the same load blows
    past the window — proves the assertions above are load-bearing."""
    session.variables.update({
        "turn_fold_enabled": False,
        "turn_roll_enabled": False,
        "tool_call_arg_stub_threshold_chars": 0,
        "context_pressure_nudge_pct": 0,   # disables the pressure clear
    })
    fills = _replay(session, turns=3)
    assert max(fills) > 100.0, max(fills)
