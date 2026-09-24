"""Governance: every history shrink path leaves a compaction record.

Prior audit: L5 fell 578k -> 17k between iterations with no compaction
record and no run_end — the trace could not explain where the tokens went.
`HistoryMixin.record_history_shrink` is the single ledger every shrink path
(budget roll, post-turn collapse, manual compact, result clearing, provider
error rollback) feeds; the emitter drains it per iteration AND at turn_end.
"""

from __future__ import annotations

import json

import pytest

from providers.base import LLMProvider, MessagePart, ProviderResponse


class _TwoIterProvider(LLMProvider):
    def __init__(self, model_name="dummy"):
        self.calls = 0
        self.model_name = model_name

    def get_available_models(self):
        return ["dummy"]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        self.calls += 1
        if self.calls == 1:
            return ProviderResponse(
                text="",
                parts=[MessagePart(type="tool_call", tool_name="todo_list", tool_args={}, tool_call_id="c1")],
                input_tokens=100, output_tokens=2, total_tokens=102,
            )
        return ProviderResponse(
            text="done", parts=[MessagePart(type="text", text="done")],
            input_tokens=120, output_tokens=1, total_tokens=121,
        )

    def upload_file(self, *a, **kw):
        return None


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.config.HISTORY_DIR", str(tmp_path / "history"))
    from mu.session.session import Session, SessionManager

    sm = SessionManager()
    sess = Session(_TwoIterProvider(), False, "system", sm)
    sess.variables["agent_mode"] = "default"
    return sess


def _trace_records(tmp_path):
    root = tmp_path / "history" / "trace"
    files = sorted(root.glob("*.jsonl")) if root.exists() else []
    assert len(files) == 1, files
    out = []
    with open(files[0], encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                out.append(json.loads(line))
    return out


def _msg(role, text, n=40):
    return {"role": role, "parts": [{"type": "text", "text": text * n}]}


def test_record_history_shrink_appends_full_entry():
    from mu.session.session import SessionManager

    sm = SessionManager()
    sm.history = [_msg("user", "a"), _msg("assistant", "b")]
    sm.summary_anchor = 0
    sm._pending_compaction_iter = 7
    sm.record_history_shrink(
        kind="unit", tokens_before=1000, tokens_after=400, msgs_before=5, anchor_before=0, extra_field="x"
    )
    log = sm._compaction_log
    assert len(log) == 1
    e = log[0]
    assert e["kind"] == "unit"
    assert e["iter"] == 7
    assert (e["tokens_before"], e["tokens_after"], e["tokens_saved"]) == (1000, 400, 600)
    assert (e["msgs_before"], e["msgs_after"]) == (5, 2)
    assert e["extra_field"] == "x"


def test_shrink_since_is_a_noop_when_nothing_changed():
    from mu.session.session import SessionManager

    sm = SessionManager()
    sm.history = [_msg("user", "a")]
    snap = sm._shrink_snapshot()
    sm._record_shrink_since(snap, kind="nothing")
    assert not getattr(sm, "_compaction_log", None)


def test_turn_collapse_records_shrink():
    from mu.session.session import SessionManager

    sm = SessionManager()
    sm.history = [
        _msg("user", "ask"),
        {"role": "assistant", "parts": [{"type": "tool_call", "tool_name": "bash", "tool_args": {"command": "ls"}, "tool_call_id": "c1"}]},
        {"role": "tool", "parts": [{"type": "tool_result", "tool_name": "bash", "tool_result": "x" * 4000, "tool_call_id": "c1"}]},
        _msg("assistant", "final answer"),
    ]
    sm.summary_anchor = 0
    snap = sm._shrink_snapshot()
    sm.compact_completed_turn()
    sm._record_shrink_since(snap, kind="turn_collapse")
    assert len(sm.history) == 2
    log = sm._compaction_log
    assert len(log) == 1 and log[0]["kind"] == "turn_collapse"
    assert log[0]["msgs_before"] == 4 and log[0]["msgs_after"] == 2
    assert log[0]["tokens_saved"] > 0


def test_result_clear_records_projected_shrink(session, tmp_path):
    """`clear_tool_results` shrinks projected context without a summarizer —
    still a shrink the trace must be able to explain."""
    from mu.session.context_maintenance import candidates, edit_tool_results

    from mu.session.result_store import ResultStore

    sm = session.session_manager
    sm.tool_result_cache.set_store(ResultStore("test", root=str(tmp_path / "results")))
    big = "line\n" * 3000
    history = [_msg("user", "ask", 1)]
    # Six completed read results: the tool-result floor protects the most
    # recent few, so the oldest ones are clearable.
    for i in range(6):
        history.append({"role": "assistant", "parts": [{"type": "tool_call", "tool_name": "read_file", "tool_args": {"filename": f"a{i}.py"}, "tool_call_id": f"c{i}"}]})
        history.append({"role": "tool", "parts": [{"type": "tool_result", "tool_name": "read_file", "tool_result": big, "tool_call_id": f"c{i}"}]})
    history.append(_msg("assistant", "done", 1))
    history.append(_msg("user", "next", 1))
    sm.history = history
    sm.summary_anchor = 0
    sm._active_turn_start_index = len(history) - 1
    ids = [c["result_id"] for c in candidates(session) if not c.get("blocked_reason")]
    assert ids, "expected at least one clearable (non-floor) tool result"
    res = edit_tool_results(session, ids, action="clear")
    assert res["ok"] and res["changed"]
    log = getattr(sm, "_compaction_log", None) or []
    kinds = [e["kind"] for e in log]
    assert "result_clear" in kinds
    e = next(x for x in log if x["kind"] == "result_clear")
    assert e["tokens_basis"] == "projected"
    assert e["cleared"] == len(res["changed"])
    assert e["tokens_saved"] > 0


def test_post_turn_collapse_is_traced_at_turn_end(session, tmp_path):
    """With compact_history=True the collapse happens AFTER the last iteration
    record; the ledger is drained at turn_end so the run still explains the
    history drop with a `compaction` line before `run_end`."""
    session.variables["compact_history"] = True
    session.send_message("do the thing")

    recs = _trace_records(tmp_path)
    types = [r["type"] for r in recs]
    assert types[-1] == "run_end"
    comps = [r for r in recs if r["type"] == "compaction"]
    assert any(c["kind"] == "turn_collapse" for c in comps), types
    collapse = next(c for c in comps if c["kind"] == "turn_collapse")
    assert collapse["msgs_after"] < collapse["msgs_before"]
    # Recorded with the run's id and positioned before the terminal record.
    assert collapse["run_id"] == recs[0]["run_id"]
    assert recs.index(collapse) < types.index("run_end")
    # No unexplained-collapse alarm fired.
    assert "context_collapse" not in types
