"""context_packaging_v2 / P2-T4: turn-boundary fold.

When a turn finishes, its completed tool results move into the durable
result store (retention action ``clear``, recoverable via ``recall``) so the
next turn's prompt carries stubs instead of the payloads. Regression target:
trace mucli_run_355d1b39c2a7 carried 519 verbatim messages (231k tokens)
from six finished turns into the seventh.
"""

from __future__ import annotations

from typing import List

import pytest

from providers.base import LLMProvider, MessagePart, ProviderResponse


class _Provider(LLMProvider):
    def __init__(self, model_name="dummy"):
        super().__init__(model_name)
        self.model_name = model_name

    def get_available_models(self) -> List[str]:
        return [self.model_name]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        return ProviderResponse(text="ok", parts=[MessagePart(type="text", text="ok")],
                                input_tokens=1, output_tokens=1, total_tokens=2)

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
    sess = Session(_Provider(), False, "system", sm)
    sess.variables["agent_mode"] = "default"
    sm.tool_result_cache.set_store(ResultStore("test", root=str(tmp_path / "results")))
    return sess


def _text(role, text):
    return {"role": role, "parts": [{"type": "text", "text": text}]}


def _turn(sm, *, prompt: str, n_results: int, payload: str, cid_prefix: str):
    """Append one agentic turn: user -> N (tool_call, tool_result) -> assistant."""
    start = len(sm.history)
    sm.history.append(_text("user", prompt))
    for i in range(n_results):
        cid = f"{cid_prefix}{i}"
        sm.history.append({"role": "assistant", "parts": [{
            "type": "tool_call", "tool_name": "read_file",
            "tool_args": {"filename": f"{cid_prefix}{i}.py"}, "tool_call_id": cid}]})
        sm.history.append({"role": "tool", "parts": [{
            "type": "tool_result", "tool_name": "read_file",
            "tool_result": payload, "tool_call_id": cid}]})
    sm.history.append(_text("assistant", "done"))
    sm._active_turn_start_index = start
    return start


def _result_states(session):
    from mu.session.context_maintenance import candidates
    return [(c["history_index"], c["state"]) for c in candidates(session, floor_override=0)]


# ------------------------------------------------------------------ schema


def test_turn_fold_variables_registered():
    from utils.config import VARIABLE_SCHEMA

    assert VARIABLE_SCHEMA["turn_fold_enabled"]["default"] is True
    assert VARIABLE_SCHEMA["turn_fold_enabled"]["type"] is bool
    assert VARIABLE_SCHEMA["turn_fold_keep_recent_results"]["default"] == 2
    assert VARIABLE_SCHEMA["turn_fold_keep_recent_results"]["type"] is int


# ------------------------------------------------------------------ fold behaviour


def test_fold_clears_all_but_keep_recent_and_results_stay_recallable(session):
    from mu.session.context_maintenance import (
        fold_completed_turn, projected_tokens, recall_cleared_result,
    )

    sm = session.session_manager
    big = "line\n" * 3000
    start = _turn(sm, prompt="ask", n_results=6, payload=big, cid_prefix="c")
    before = projected_tokens(session)

    result = fold_completed_turn(session, turn_start_index=start)

    assert result["reason"] == "folded"
    assert len(result["changed"]) == 4, result
    assert result["eligible"] == 4
    assert result["summarizer_calls"] == 0
    after = projected_tokens(session)
    assert after < before
    assert result["saved_tokens"] == before - after > 0

    states = _result_states(session)
    assert [s for _, s in states] == ["clear"] * 4 + ["active"] * 2

    # Every folded result is recoverable verbatim from the durable store.
    for key, entry in sm.context_retention.items():
        assert entry["action"] == "clear"
        recalled = recall_cleared_result(sm, entry["cache_key"])
        assert recalled is not None
        assert recalled.get("result") == big


def test_fold_records_turn_fold_ledger_entry(session):
    from mu.session.context_maintenance import fold_completed_turn

    sm = session.session_manager
    # 8 results: keep_recent=2 leaves 6 clearable; the projection's own
    # tool_result_floor (4) still renders the trailing 4 verbatim for the
    # next turn's opening, so the fold's projected saving covers results
    # 0-3 and the ledger sees a real shrink.
    start = _turn(sm, prompt="ask", n_results=8, payload="x\n" * 2000, cid_prefix="c")
    fold_completed_turn(session, turn_start_index=start)

    log = getattr(sm, "_compaction_log", None) or []
    kinds = [e["kind"] for e in log]
    assert "turn_fold" in kinds
    entry = next(e for e in log if e["kind"] == "turn_fold")
    assert entry["tokens_basis"] == "projected"
    assert entry["cleared"] == 6
    assert entry["tokens_saved"] > 0
    # The pending-kind marker is reset so a later model-directed clear is
    # attributed correctly.
    assert getattr(sm, "_pending_shrink_kind", None) is None


def test_fold_disabled_is_a_noop(session):
    from mu.session.context_maintenance import fold_completed_turn, projected_tokens

    sm = session.session_manager
    session.variables["turn_fold_enabled"] = False
    start = _turn(sm, prompt="ask", n_results=5, payload="x\n" * 2000, cid_prefix="c")
    before = projected_tokens(session)
    result = fold_completed_turn(session, turn_start_index=start)
    assert result["reason"] == "disabled"
    assert result["changed"] == []
    assert projected_tokens(session) == before
    assert not getattr(sm, "context_retention", {})
    assert not (getattr(sm, "_compaction_log", None) or [])


def test_fold_requires_durable_store(session):
    from mu.session.context_maintenance import fold_completed_turn

    sm = session.session_manager
    sm.tool_result_cache.set_store(None)
    start = _turn(sm, prompt="ask", n_results=3, payload="x\n" * 500, cid_prefix="c")
    result = fold_completed_turn(session, turn_start_index=start)
    assert result["reason"] == "durable_store_unavailable"
    assert result["changed"] == []


def test_fold_only_touches_the_finished_turn(session):
    """An earlier turn's still-verbatim results are outside the fold's scope;
    the fold is bounded by turn_start_index."""
    from mu.session.context_maintenance import fold_completed_turn

    sm = session.session_manager
    session.variables["turn_fold_keep_recent_results"] = 0
    _turn(sm, prompt="first", n_results=3, payload="a\n" * 1000, cid_prefix="a")
    start2 = _turn(sm, prompt="second", n_results=3, payload="b\n" * 1000, cid_prefix="b")
    result = fold_completed_turn(session, turn_start_index=start2)
    assert len(result["changed"]) == 3
    touched = {e["history_index"] for e in sm.context_retention.values()}
    assert all(i >= start2 for i in touched)


def test_fold_respects_protected_failed_and_signed_results(session):
    from mu.session.context_maintenance import fold_completed_turn

    sm = session.session_manager
    session.variables["turn_fold_keep_recent_results"] = 0
    start = _turn(sm, prompt="ask", n_results=4, payload="x\n" * 1000, cid_prefix="c")
    # Result 0 failed; result 1 is a provider-signed bundle; result 2 pinned.
    sm.history[start + 2]["parts"][0]["tool_result"] = "Error: boom"
    sm.history[start + 3]["parts"][0]["thought_signature"] = "sig"
    sm.protected_indices = {start + 6}
    result = fold_completed_turn(session, turn_start_index=start)
    # Only result 3 (index start+8) is clearable.
    assert len(result["changed"]) == 1
    assert {e["history_index"] for e in sm.context_retention.values()} == {start + 8}


def test_fold_is_idempotent_across_repeat_calls(session):
    from mu.session.context_maintenance import fold_completed_turn

    sm = session.session_manager
    start = _turn(sm, prompt="ask", n_results=5, payload="x\n" * 1000, cid_prefix="c")
    first = fold_completed_turn(session, turn_start_index=start)
    second = fold_completed_turn(session, turn_start_index=start)
    assert len(first["changed"]) == 3
    assert second["reason"] == "nothing_clearable"
    assert second["changed"] == []


# ------------------------------------------------------------------ loop seam


def test_loop_body_seam_folds_and_swallows_errors(session, monkeypatch):
    from mu.agent import loop_body

    sm = session.session_manager
    start = _turn(sm, prompt="ask", n_results=4, payload="x\n" * 1000, cid_prefix="c")
    result = loop_body._fold_turn_context(session, start, 7)
    assert result is not None and len(result["changed"]) == 2
    assert sm._pending_compaction_iter == 7

    def _boom(*a, **k):
        raise RuntimeError("store exploded")

    monkeypatch.setattr("mu.session.context_maintenance.fold_completed_turn", _boom)
    assert loop_body._fold_turn_context(session, start, 8) is None
