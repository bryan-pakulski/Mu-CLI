"""Tests for the combined multi-run session view (``mu/trace/parser.py``:
``load_session_runs`` / ``combine_runs`` / ``build_session_view``).

A *session* spans multiple runs (one agent-loop invocation each). These tests
write two run files for one session plus a run for a *different* session into
a tmp trace dir, then assert the discovery + merge + per-run-bounds behaviour
that the Trace Analyzer's combined session view relies on.
"""

import json
import os
import time

import pytest

from mu.trace import (
    build_session_view,
    combine_runs,
    load_session_runs,
    parse_trace,
)


def _iter(run_id, i, drift=20.0, wall=100, status="running"):
    return {
        "type": "iter", "run_id": run_id, "iter": i, "wall_ms": wall,
        "context": {"l0": 500, "l5": 800, "total_est": 1600,
                    "prompt_tokens_actual": 2000, "drift_pct": drift},
        "tokens": {"in": 2000, "out": 50, "cached": 0, "reasoning": 0,
                   "cost_delta": 0.001},
        "has_text": False, "has_tool_call": True, "assistant_preview": "",
        "subagents": {"active": 0, "stuck": 0, "stall": 0, "children": []},
        "memory": {"task_memory_count": 1, "by_status": {"active": 1},
                   "scratchpad_count": 0},
        "compaction": None, "status": status,
    }


def _tool(run_id, i, name="read_file"):
    return {"type": "tool", "run_id": run_id, "iter": i, "name": name,
            "arg_fp": name + ":x", "ok": True, "error_code": None,
            "latency_ms": 10, "cache_hit": False, "result_bytes": 100,
            "path": "a.py", "preview": "c"}


def _run_records(run_id, session, n_iters, status="completed"):
    recs = [{
        "type": "run_start", "run_id": run_id, "session": session,
        "model": "glm5.2", "provider": "OpenAIProvider", "mode": "loop",
        "context_limit": 128000, "max_iterations": 1000,
    }]
    for i in range(n_iters):
        recs.append(_iter(run_id, i))
        recs.append(_tool(run_id, i))
    recs.append({
        "type": "turn_end", "run_id": run_id, "status": status,
        "total_in": 2000 * n_iters, "total_out": 50 * n_iters,
        "total_cost": 0.001 * n_iters, "tool_calls": n_iters,
        "tool_results": n_iters, "error": None, "session_totals": {},
        "iters": n_iters,
    })
    return recs


def _write(tmp_path, name, records, mtime_offset=0.0):
    tdir = tmp_path / "trace"
    tdir.mkdir(parents=True, exist_ok=True)
    path = tdir / name
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    # Stagger mtimes so chronological (oldest-first) order is deterministic —
    # load_session_runs sorts by mtime because run_start carries no timestamp.
    ts = time.time() + mtime_offset
    os.utime(path, (ts, ts))
    return str(path)


@pytest.fixture
def session_env(tmp_path, monkeypatch):
    """Two runs for session `mysess` + one run for `othersess` in a tmp trace dir."""
    _write(tmp_path, "mysess_run_aaa111.jsonl", _run_records("run_aaa111", "mysess", 2),
           mtime_offset=0.0)
    _write(tmp_path, "mysess_run_bbb222.jsonl", _run_records("run_bbb222", "mysess", 3),
           mtime_offset=10.0)
    _write(tmp_path, "othersess_run_ccc333.jsonl", _run_records("run_ccc333", "othersess", 1),
           mtime_offset=20.0)
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path))
    import utils.config as _cfg
    monkeypatch.setattr(_cfg, "HISTORY_DIR", str(tmp_path), raising=False)
    return "mysess"


def test_load_session_runs_filters_and_orders(session_env):
    runs = load_session_runs(session_env)
    assert [r.run_id for r in runs] == ["run_aaa111", "run_bbb222"]


def test_load_session_runs_empty_for_unknown_session(session_env):
    assert load_session_runs("nope") == []


def test_combine_runs_global_iter_numbering(session_env):
    runs = load_session_runs(session_env)
    merged = combine_runs(runs)
    # 2 iters + 3 iters = 5 globally-numbered iters, contiguous from 0
    assert [i["iter"] for i in merged.iters] == [0, 1, 2, 3, 4]
    # Each kept its origin run_id
    assert merged.iters[0]["run_id"] == "run_aaa111"
    assert merged.iters[3]["run_id"] == "run_bbb222"
    # Tools remapped to global iters; run 2's iter 0 -> global 2
    tool_iters = sorted({t["iter"] for t in merged.tools})
    assert tool_iters == [0, 1, 2, 3, 4]


def test_build_session_view_shape_and_bounds(session_env):
    runs = load_session_runs(session_env)
    view = build_session_view(runs, cols=64)
    assert view["n_runs"] == 2
    # run_id marks a session-scoped combined view, not a single run
    assert view["run_id"] == "session:mysess"
    # run_bounds cover the full global range contiguously
    rb = view["run_bounds"]
    assert [b["run_id"] for b in rb] == ["run_aaa111", "run_bbb222"]
    assert rb[0] == {"run_id": "run_aaa111", "start_iter": 0, "end_iter": 1,
                     "iters": 2, "model": "glm5.2", "mode": "loop", "status": "completed"}
    assert rb[1]["start_iter"] == 2 and rb[1]["end_iter"] == 4 and rb[1]["iters"] == 3
    # merged iters carry the global numbering
    assert [i["iter"] for i in view["iters"]] == [0, 1, 2, 3, 4]


def test_build_session_view_sums_token_cost_totals(session_env):
    runs = load_session_runs(session_env)
    view = build_session_view(runs, cols=64)
    s = view["summary"]
    # run A: 2 iters * (2000 in, 50 out, 0.001 cost); run B: 3 iters likewise
    assert s["total_in"] == 2000 * 5
    assert s["total_out"] == 50 * 5
    assert abs(s["total_cost"] - round(0.001 * 5, 6)) < 1e-9
    assert s["iters"] == 5
    assert s["status"] == "completed"


def test_build_session_view_status_partial_when_a_run_incomplete(session_env, tmp_path):
    # Rewrite run B as still running (no completed turn_end status).
    _write(tmp_path, "mysess_run_bbb222.jsonl",
           _run_records("run_bbb222", "mysess", 3, status="running"),
           mtime_offset=10.0)
    runs = load_session_runs("mysess")
    view = build_session_view(runs, cols=64)
    # Not every run completed -> not "completed"; last run was running.
    assert view["summary"]["status"] != "completed"


def test_build_session_view_empty_runs():
    view = build_session_view([], cols=64)
    assert view["n_runs"] == 0
    assert view["run_bounds"] == []
    assert view["iters"] == []