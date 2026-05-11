"""Unit tests for the benchmark recorder + baseline analytics."""

import json
import os

import pytest

from benchmarks.recorder import (
    BenchmarkRecorder,
    default_baseline_path,
    diff,
    load_baseline,
    save_baseline,
    summarize,
)
from benchmarks.rubrics import RubricResult
from benchmarks.spec import BenchmarkResult, BenchmarkRun


def _make_result(spec_name, *, passed, score, tokens=100, elapsed=10.0, tool_calls=5, mode="default"):
    run = BenchmarkRun(
        spec_name=spec_name,
        mode=mode,
        provider="fake",
        model="fake-1",
        started_at_unix=0.0,
        elapsed_seconds=elapsed,
        iterations=2,
        tool_call_count=tool_calls,
        tokens={"total": tokens},
        cost_estimate=0.0,
        final_response="",
        workspace_path="/tmp/x",
        status="completed",
    )
    return BenchmarkResult(
        run=run,
        rubric_results=[
            RubricResult(name="r1", passed=True, weight=1.0, score=1.0, message="ok"),
        ],
        score=score,
        max_score=1.0,
        passed=passed,
    )


# ============================================================ record + load


def test_recorder_round_trip(tmp_path):
    path = str(tmp_path / "baseline.jsonl")
    rec = BenchmarkRecorder(path)
    r1 = _make_result("spec_a", passed=True, score=1.0)
    r2 = _make_result("spec_b", passed=False, score=0.4)
    rec.append(r1)
    rec.append(r2)

    loaded = load_baseline(path)
    assert len(loaded) == 2
    assert loaded[0]["run"]["spec_name"] == "spec_a"
    assert loaded[0]["passed"] is True
    assert loaded[1]["run"]["spec_name"] == "spec_b"
    assert loaded[1]["passed"] is False


def test_recorder_append_is_idempotent(tmp_path):
    path = str(tmp_path / "b.jsonl")
    rec = BenchmarkRecorder(path)
    for i in range(3):
        rec.append(_make_result(f"s{i}", passed=True, score=1.0))
    loaded = load_baseline(path)
    assert len(loaded) == 3


def test_save_baseline_overwrites(tmp_path):
    path = str(tmp_path / "x.jsonl")
    save_baseline(path, [_make_result("a", passed=True, score=1.0)])
    save_baseline(path, [_make_result("b", passed=True, score=0.5)])
    loaded = load_baseline(path)
    assert len(loaded) == 1
    assert loaded[0]["run"]["spec_name"] == "b"


def test_load_baseline_missing_path_returns_empty(tmp_path):
    assert load_baseline(str(tmp_path / "absent.jsonl")) == []


def test_load_baseline_skips_corrupt_lines(tmp_path):
    path = str(tmp_path / "b.jsonl")
    with open(path, "w") as fh:
        rec = _make_result("a", passed=True, score=1.0)
        fh.write(json.dumps(rec.to_dict(), sort_keys=True) + "\n")
        fh.write("not json\n")
        fh.write(json.dumps(rec.to_dict(), sort_keys=True) + "\n")
    loaded = load_baseline(path)
    assert len(loaded) == 2


def test_default_baseline_path_sanitizes_identifiers():
    path = default_baseline_path("openai", "gpt-4o/v1", date="2026-05-11")
    assert "openai__gpt-4o_v1__2026-05-11" in path
    # No slashes leaked into the filename.
    base = os.path.basename(path)
    assert "/" not in base


# ============================================================ summarize


def test_summarize_aggregates_per_spec():
    records = [
        _make_result("s1", passed=True, score=1.0, tokens=100, elapsed=10.0, tool_calls=5).to_dict(),
        _make_result("s1", passed=False, score=0.5, tokens=200, elapsed=20.0, tool_calls=10).to_dict(),
        _make_result("s2", passed=True, score=0.8, tokens=300, elapsed=30.0, tool_calls=8, mode="debug").to_dict(),
    ]
    summaries = summarize(records)
    assert set(summaries) == {"s1", "s2"}

    s1 = summaries["s1"]
    assert s1.runs == 2
    assert s1.passes == 1
    assert s1.pass_rate == 0.5
    assert s1.mean_score == pytest.approx(0.75)
    assert s1.mean_tokens_total == 150
    assert s1.mean_elapsed_seconds == 15.0
    assert s1.mean_tool_calls == 7.5

    s2 = summaries["s2"]
    assert s2.runs == 1
    assert s2.passes == 1
    assert s2.mode == "debug"


def test_summarize_empty_records():
    assert summarize([]) == {}


# ============================================================ diff


def test_diff_flags_score_regression():
    a = [_make_result("s1", passed=True, score=1.0).to_dict()]
    b = [_make_result("s1", passed=False, score=0.5).to_dict()]
    deltas = diff(a, b)
    assert "s1" in deltas
    d = deltas["s1"]
    assert d.score_delta == -0.5
    assert d.regressed is True


def test_diff_no_regression_for_score_improvement():
    a = [_make_result("s1", passed=False, score=0.5).to_dict()]
    b = [_make_result("s1", passed=True, score=1.0).to_dict()]
    d = diff(a, b)["s1"]
    assert d.score_delta == 0.5
    assert d.regressed is False


def test_diff_flags_pass_rate_drop_even_if_score_steady():
    # Two runs each, second baseline drops one pass without changing total score
    # (e.g. pass_threshold change).
    a = [
        _make_result("s1", passed=True, score=0.9).to_dict(),
        _make_result("s1", passed=True, score=0.9).to_dict(),
    ]
    b = [
        _make_result("s1", passed=True, score=0.9).to_dict(),
        _make_result("s1", passed=False, score=0.9).to_dict(),
    ]
    d = diff(a, b)["s1"]
    assert d.pass_rate_delta == -0.5
    assert d.regressed is True


def test_diff_new_spec_in_b_treated_as_no_baseline_in_a():
    a = []
    b = [_make_result("new_spec", passed=True, score=1.0).to_dict()]
    d = diff(a, b)["new_spec"]
    assert d.score_delta == 1.0  # full credit toward "improvement"
    assert d.regressed is False


def test_diff_removed_spec_in_b_is_regression():
    a = [_make_result("dropped", passed=True, score=1.0).to_dict()]
    b = []
    d = diff(a, b)["dropped"]
    assert d.score_delta == -1.0
    assert d.regressed is True


def test_diff_token_and_elapsed_deltas():
    a = [_make_result("s", passed=True, score=1.0, tokens=100, elapsed=10.0).to_dict()]
    b = [_make_result("s", passed=True, score=1.0, tokens=150, elapsed=8.0).to_dict()]
    d = diff(a, b)["s"]
    assert d.tokens_delta == 50  # got more expensive
    assert d.elapsed_delta == -2.0  # got faster
    # Score unchanged → not flagged as regression even though tokens went up.
    assert d.regressed is False
