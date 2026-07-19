"""Tests for the agent-facing trace tools (``mu/tools/trace/handlers.py``).

Writes a synthetic trace JSONL fixture (the same shape the emitter produces)
and drives the four `@tool` handlers directly: `list_traces`, `trace_summary`,
`trace_series`, `trace_iteration`. The handlers are thin wrappers over
``mu/trace/parser.py``, so these tests also cover the `list_trace_runs` /
`find_trace_path` discovery helpers.
"""

import json
import os

import pytest

from mu.tools.trace import handlers


def _write_fixture(tmp_path, records, name="fake_session_run_abc123.jsonl"):
    """Write a trace JSONL into a *separate* trace dir and point $MUCLI_HOME at it.

    The trace tools resolve runs via ``trace_dir()`` = ``$MUCLI_HOME/trace``,
    so we set MUCLI_HOME to the tmp dir for the test process. (``utils.config``
    reads HISTORY_DIR from the env at import; the helper re-reads each call.)
    """
    tdir = tmp_path / "trace"
    tdir.mkdir(parents=True, exist_ok=True)
    path = tdir / name
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
        {
            "type": "iter", "run_id": "run_abc123", "iter": 0, "wall_ms": 120,
            "context": {"l0": 500, "l5": 800, "total_est": 1600,
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
        {
            "type": "iter", "run_id": "run_abc123", "iter": 1, "wall_ms": 200,
            "context": {"l0": 500, "l5": 400, "total_est": 1500,
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
        {
            "type": "iter", "run_id": "run_abc123", "iter": 2, "wall_ms": 90,
            "context": {"l0": 500, "l5": 500, "total_est": 1600,
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


class _Ctx:
    """Minimal ToolExecutionContext stand-in — trace tools don't use it."""


@pytest.fixture
def trace_env(tmp_path, monkeypatch):
    """Point MUCLI_HOME at a tmp trace dir holding the fixture, and return run_id."""
    _write_fixture(tmp_path, _fixture())
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path))
    # utils.config caches HISTORY_DIR at import; force it to re-read the env.
    import utils.config as _cfg
    monkeypatch.setattr(_cfg, "HISTORY_DIR", str(tmp_path), raising=False)
    return "run_abc123"


def _call(fn, **kwargs):
    return json.loads(fn(kwargs, _Ctx()))


def test_list_traces_finds_run(trace_env):
    out = _call(handlers.list_traces)
    assert out["count"] >= 1
    run = next(r for r in out["runs"] if r["run_id"] == trace_env)
    assert run["session"] == "fake"
    assert run["model"] == "glm5.2"
    assert run["iters"] == 3
    assert run["file"].endswith("run_abc123.jsonl")


def test_list_traces_session_filter(trace_env):
    out = _call(handlers.list_traces, session="fake")
    assert all("fake" in r["session"].lower() for r in out["runs"])
    assert any(r["run_id"] == trace_env for r in out["runs"])
    none = _call(handlers.list_traces, session="nope-never")
    assert none["count"] == 0


def test_trace_summary_overview(trace_env):
    out = _call(handlers.trace_summary, run_id=trace_env)
    assert out["iters"] == 3
    assert out["status"] == "completed"
    assert out["total_wall_ms"] == 410
    assert out["peak_wall_ms"] == 200
    assert out["median_drift"] == 27.27
    assert out["mechanical_fallback_count"] == 1
    assert out["nudge_count"] == 1
    assert out["nudges_broken"] == 1
    assert out["redundant_reads"] == 1
    assert out["subagent_iters"] == 1
    assert out["total_cost"] == 0.0045


def test_trace_summary_missing_run(trace_env):
    out = _call(handlers.trace_summary, run_id="run_does_not_exist")
    assert "not found" in out["error"]


def test_trace_series_named_and_iter_filter(trace_env):
    drift = _call(handlers.trace_series, run_id=trace_env, series="drift")
    assert [d["iter"] for d in drift] == [0, 1, 2]
    assert drift[0]["drift_pct"] == 20.0
    # iter filter narrows to the one point
    one = _call(handlers.trace_series, run_id=trace_env, series="drift", iter=1)
    assert isinstance(one, list) and len(one) == 1
    assert one[0]["iter"] == 1 and one[0]["drift_pct"] == 37.5
    # event series filtered by iter
    comps = _call(handlers.trace_series, run_id=trace_env,
                  series="compaction_timeline", iter=1)
    assert len(comps) == 1 and comps[0]["summarizer"] == "mechanical"
    comps0 = _call(handlers.trace_series, run_id=trace_env,
                   series="compaction_timeline", iter=0)
    assert comps0 == []


def test_trace_series_unknown_name(trace_env):
    out = _call(handlers.trace_series, run_id=trace_env, series="bogus")
    assert "unknown series" in out["error"]
    assert "drift" in out["valid"]


def test_trace_series_full_when_no_name(trace_env):
    out = _call(handlers.trace_series, run_id=trace_env)
    assert set(out.keys()) >= {"context", "drift", "tokens", "latency",
                               "tool_histogram", "compaction_timeline"}


def test_trace_iteration_drill_down(trace_env):
    out = _call(handlers.trace_iteration, run_id=trace_env, iter=1)
    assert out["run_id"] == trace_env
    rec = out["iter"]
    assert rec["iter"] == 1
    assert rec["wall_ms"] == 200
    assert rec["context"]["drift_pct"] == 37.5
    assert rec["memory"]["task_memory_count"] == 2
    # tools at iter 1: one read_file
    assert len(out["tools"]) == 1
    t = out["tools"][0]
    assert t["name"] == "read_file"
    assert t["path"] == "a.py"
    assert t["ok"] is True
    assert set(t.keys()) == {"name", "path", "ok", "error_code",
                             "latency_ms", "cache_hit", "result_bytes", "preview"}
    # nudge + compaction at iter 1
    assert len(out["nudges"]) == 1 and out["nudges"][0]["kind"] == "recoverage_stall"
    assert len(out["compactions"]) == 1 and out["compactions"][0]["kind"] == "auto_hook"


def test_trace_iteration_not_found(trace_env):
    out = _call(handlers.trace_iteration, run_id=trace_env, iter=99)
    assert "not found" in out["error"]
    assert out["iters"] == [0, 1, 2]


def test_find_trace_path_substring_and_exact(trace_env, tmp_path):
    from mu.trace import find_trace_path

    # substring of the filename resolves
    p = find_trace_path("abc123")
    assert p is not None and p.endswith("run_abc123.jsonl")
    # exact filename resolves
    p2 = find_trace_path("fake_session_run_abc123.jsonl")
    assert p2 is not None and os.path.basename(p2) == "fake_session_run_abc123.jsonl"
    # miss
    assert find_trace_path("run_nope") is None