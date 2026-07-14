"""Tests for the trace parser/series/snapshot (``mu/trace/parser.py``,
``mu/trace/snapshot.py``).

Feeds a synthetic JSONL fixture through ``parse_trace`` → ``build_series`` →
``build_summary`` → ``build_trace_snapshot`` and asserts the derived series
match expectations: drift series, compaction timeline, tool histogram,
redundant-read flags, nudge efficacy, and the snapshot grid shape.
"""

import json

import pytest

from mu.trace import (
    build_series,
    build_summary,
    build_trace_snapshot,
    parse_trace,
)


def _write_fixture(tmp_path, records):
    path = tmp_path / "fake_session_run_abc123.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return str(path)


def _fixture():
    return [
        {
            "type": "run_start", "run_id": "run_abc123",
            "session": "fake", "model": "glm5.2", "provider": "OpenAIProvider",
            "mode": "loop", "context_limit": 128000, "max_iterations": 1000,
        },
        # iter 0: read a.py, high drift (actual >> est)
        {
            "type": "iter", "run_id": "run_abc123", "iter": 0, "max_iter": 1000,
            "wall_ms": 120,
            "context": {"l0": 500, "l1": 200, "l1b": 100, "l2": 0, "l3": 0,
                        "l4b": 0, "l5": 800, "total_est": 1600,
                        "prompt_tokens_actual": 2000, "drift_pct": 20.0},
            "tokens": {"in": 2000, "out": 50, "cached": 0, "reasoning": 0,
                       "cost_delta": 0.001},
            "has_text": False, "has_tool_call": True, "assistant_preview": "",
            "subagents": {"active": 0, "stuck": 0, "stall": 0, "children": []},
            "memory": {"task_memory_count": 1, "by_status": {"active": 1},
                       "scratchpad_count": 0},
            "compaction": None, "status": "running",
        },
        {"type": "tool", "run_id": "run_abc123", "iter": 0, "name": "read_file",
         "arg_fp": "read_file:abcd", "ok": True, "error_code": None,
         "latency_ms": 30, "cache_hit": False, "result_bytes": 4000,
         "path": "a.py", "preview": "content"},
        # iter 1: compaction (mechanical), re-read a.py (redundant), a nudge
        {
            "type": "iter", "run_id": "run_abc123", "iter": 1, "max_iter": 1000,
            "wall_ms": 200,
            "context": {"l0": 500, "l1": 200, "l1b": 100, "l2": 300, "l3": 0,
                        "l4b": 0, "l5": 400, "total_est": 1500,
                        "prompt_tokens_actual": 2400, "drift_pct": 37.5},
            "tokens": {"in": 2400, "out": 60, "cached": 0, "reasoning": 100,
                       "cost_delta": 0.002},
            "has_text": False, "has_tool_call": True, "assistant_preview": "",
            "subagents": {"active": 1, "stuck": 0, "stall": 0, "children": []},
            "memory": {"task_memory_count": 2, "by_status": {"active": 2},
                       "scratchpad_count": 1},
            "compaction": {"kind": "auto_hook", "tokens_before": 5000,
                           "tokens_after": 1500, "summarizer": "mechanical"},
            "status": "running",
        },
        {"type": "compaction", "run_id": "run_abc123", "iter": 1,
         "kind": "auto_hook", "tokens_before": 5000, "tokens_after": 1500,
         "tokens_saved": 3500, "summarizer": "mechanical", "keep_recent": 2,
         "budget": 3000, "anchor_delta": 1},
        {"type": "tool", "run_id": "run_abc123", "iter": 1, "name": "read_file",
         "arg_fp": "read_file:abcd", "ok": True, "error_code": None,
         "latency_ms": 5, "cache_hit": False, "result_bytes": 4000,
         "path": "a.py", "preview": "content"},
        {"type": "nudge", "run_id": "run_abc123", "kind": "recoverage_stall",
         "iteration": 1, "stall_iters": 2},
        # iter 2: a write (breaks the nudge), completion
        {
            "type": "iter", "run_id": "run_abc123", "iter": 2, "max_iter": 1000,
            "wall_ms": 90,
            "context": {"l0": 500, "l1": 200, "l1b": 100, "l2": 300, "l3": 0,
                        "l4b": 0, "l5": 500, "total_est": 1600,
                        "prompt_tokens_actual": 2200, "drift_pct": 27.27},
            "tokens": {"in": 2200, "out": 40, "cached": 0, "reasoning": 0,
                       "cost_delta": 0.0015},
            "has_text": True, "has_tool_call": True, "assistant_preview": "done",
            "subagents": {"active": 0, "stuck": 0, "stall": 0, "children": []},
            "memory": {"task_memory_count": 2, "by_status": {"active": 2},
                       "scratchpad_count": 1},
            "compaction": None, "status": "running",
        },
        {"type": "tool", "run_id": "run_abc123", "iter": 2, "name": "write_file",
         "arg_fp": "write_file:effg", "ok": True, "error_code": None,
         "latency_ms": 12, "cache_hit": False, "result_bytes": 20,
         "path": "b.py", "preview": "wrote"},
        {"type": "turn_end", "run_id": "run_abc123", "status": "completed",
         "total_in": 6600, "total_out": 150, "total_cost": 0.0045,
         "tool_calls": 3, "tool_results": 3, "error": None,
         "session_totals": {}, "iters": 3},
    ]


@pytest.fixture
def run(tmp_path):
    path = _write_fixture(tmp_path, _fixture())
    return parse_trace(path)


def test_parse_trace_structure(run):
    assert run.run_id == "run_abc123"
    assert run.header["model"] == "glm5.2"
    assert run.iter_count == 3
    assert len(run.tools) == 3
    assert len(run.nudges) == 1
    assert len(run.compactions) == 1
    assert run.turn_end is not None
    assert run.turn_end["status"] == "completed"


def test_build_series_drift(run):
    s = build_series(run)
    assert s["n"] == 3
    drift = s["drift"]
    assert [d["iter"] for d in drift] == [0, 1, 2]
    assert drift[0]["drift_pct"] == 20.0
    assert drift[1]["drift_pct"] == 37.5
    # layers_stacked has all seven layer keys, length 3 each
    assert set(s["layers_stacked"].keys()) == {
        "l0", "l1", "l1b", "l2", "l3", "l4b", "l5"
    }
    assert len(s["layers_stacked"]["l5"]) == 3


def test_build_series_tool_histogram(run):
    s = build_series(run)
    hist = {h["name"]: h for h in s["tool_histogram"]}
    assert hist["read_file"]["count"] == 2
    assert hist["read_file"]["ok"] == 2
    assert hist["write_file"]["count"] == 1
    # latency_series attached
    assert "latency_series" in hist["read_file"]
    assert len(hist["read_file"]["latency_series"]) == 2


def test_build_series_compaction_timeline(run):
    s = build_series(run)
    ct = s["compaction_timeline"]
    assert len(ct) == 1
    assert ct[0]["kind"] == "auto_hook"
    assert ct[0]["summarizer"] == "mechanical"
    assert ct[0]["tokens_saved"] == 3500


def test_build_series_redundant_reads(run):
    s = build_series(run)
    rr = s["redundant_reads"]
    # a.py read at iter 0, re-read at iter 1 with no intervening write → flagged.
    assert len(rr) == 1
    assert rr[0]["path"] == "a.py"
    assert rr[0]["iter"] == 1
    assert rr[0]["prev_iter"] == 0


def test_build_series_nudge_efficacy(run):
    s = build_series(run)
    eff = s["nudge_efficacy"]
    assert len(eff) == 1
    # The nudge at iter 1 is followed by a write_file at iter 2 → broke.
    assert eff[0]["broke"] is True
    assert eff[0]["how"] == "write"


def test_build_summary(run):
    s = build_series(run)
    summ = build_summary(run, s)
    assert summ["iters"] == 3
    assert summ["status"] == "completed"
    assert summ["mechanical_fallback_count"] == 1
    assert summ["compaction_by_kind"]["auto_hook"] == 1
    assert summ["nudge_count"] == 1
    assert summ["nudge_by_kind"]["recoverage_stall"] == 1
    assert summ["nudges_broken"] == 1
    assert summ["redundant_reads"] == 1
    assert summ["peak_drift_abs"] == 37.5
    assert summ["peak_context"] == 2400
    assert summ["total_cost"] == 0.0045
    assert summ["subagent_iters"] == 1  # only iter 1 had an active child
    # wall-time summary from the per-iter wall_ms (120, 200, 90)
    assert summ["total_wall_ms"] == 410
    assert summ["peak_wall_ms"] == 200
    assert summ["mean_wall_ms"] == 136
    # median drift is more robust than mean — sorted [20, 27.27, 37.5] → middle
    assert summ["median_drift"] == 27.27


def test_build_series_latency(run):
    s = build_series(run)
    lat = s["latency"]
    assert [p["iter"] for p in lat] == [0, 1, 2]
    assert [p["wall_ms"] for p in lat] == [120, 200, 90]


def test_build_series_tool_error_codes(tmp_path):
    # A tool that errors should accumulate error_code counts in the histogram.
    records = [
        {"type": "run_start", "run_id": "r_err", "session": "x", "model": "m",
         "provider": "p", "mode": "loop", "context_limit": 8000, "max_iterations": 10},
        {"type": "iter", "run_id": "r_err", "iter": 0, "wall_ms": 10,
         "context": {"total_est": 100, "prompt_tokens_actual": 100, "drift_pct": 0},
         "tokens": {"in": 100, "out": 1, "cached": 0, "reasoning": 0, "cost_delta": 0},
         "has_text": False, "has_tool_call": True, "assistant_preview": "",
         "subagents": {"active": 0, "stuck": 0, "stall": 0, "children": []},
         "memory": {"task_memory_count": 0, "by_status": {}, "scratchpad_count": 0},
         "compaction": None, "status": "running"},
        {"type": "tool", "run_id": "r_err", "iter": 0, "name": "read_file",
         "arg_fp": "read_file:x", "ok": False, "error_code": "ENOENT",
         "latency_ms": 4, "cache_hit": False, "result_bytes": 0,
         "path": "missing.py", "preview": ""},
        {"type": "tool", "run_id": "r_err", "iter": 0, "name": "read_file",
         "arg_fp": "read_file:y", "ok": False, "error_code": "ENOENT",
         "latency_ms": 3, "cache_hit": False, "result_bytes": 0,
         "path": "gone.py", "preview": ""},
        {"type": "tool", "run_id": "r_err", "iter": 0, "name": "read_file",
         "arg_fp": "read_file:z", "ok": False, "error_code": "perm",
         "latency_ms": 2, "cache_hit": False, "result_bytes": 0,
         "path": "locked.py", "preview": ""},
    ]
    run = parse_trace(_write_fixture(tmp_path, records))
    s = build_series(run)
    h = {h["name"]: h for h in s["tool_histogram"]}["read_file"]
    assert h["error"] == 3
    assert h["ok"] == 0
    assert h["error_codes"] == {"ENOENT": 2, "perm": 1}


def test_build_trace_snapshot(run):
    snap = build_trace_snapshot(run, cols=128)
    # 3 iters < 128 → no downsample, 3 columns.
    assert snap["meta"]["rendered_cols"] == 3
    assert len(snap["grid"]) == 7  # seven layers
    for row in snap["grid"]:
        assert len(row) == 3
    assert len(snap["drift_strip"]) == 3
    # compaction at iter 1 → column index 1
    assert snap["compaction_cols"] == [1]
    assert len(snap["context_actual"]) == 3
    assert snap["context_actual"][1] == 2400


def test_build_trace_snapshot_downsamples(run):
    snap = build_trace_snapshot(run, cols=2)
    assert snap["meta"]["rendered_cols"] == 2
    for row in snap["grid"]:
        assert len(row) == 2


def test_parse_trace_robust_to_bad_lines(tmp_path):
    path = tmp_path / "bad.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n")
        fh.write("not json at all\n")
        fh.write(json.dumps({"type": "run_start", "run_id": "r1"}) + "\n")
        fh.write(json.dumps({"type": "iter", "iter": 0, "context": {}}) + "\n")
    run = parse_trace(str(path))
    assert run.run_id == "r1"
    assert run.iter_count == 1