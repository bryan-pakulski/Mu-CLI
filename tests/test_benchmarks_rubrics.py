"""Unit tests for benchmark rubrics.

Each rubric is evaluated against a synthetic BenchmarkRun (no real
agent involved). Pins: pass/fail logic, weight propagation, error
handling (timeouts, missing files, etc.).
"""

import os

import pytest

from benchmarks.rubrics import (
    CommandSucceeds,
    FileContains,
    FileNotContains,
    FileRegex,
    MaxSeconds,
    MaxToolCalls,
    ResponseContains,
    ResponseMatches,
)
from benchmarks.spec import BenchmarkRun


def _run(workspace, **overrides):
    base = dict(
        spec_name="t",
        mode="default",
        provider="fake",
        model="fake",
        started_at_unix=0.0,
        elapsed_seconds=1.0,
        iterations=1,
        tool_call_count=1,
        tokens={"total": 100},
        cost_estimate=0.0,
        final_response="",
        workspace_path=workspace,
        status="completed",
    )
    base.update(overrides)
    return BenchmarkRun(**base)


# ============================================================ FileContains


def test_file_contains_passes_when_substring_present(tmp_path):
    (tmp_path / "f.txt").write_text("hello world goodbye")
    rubric = FileContains("f.txt", "world")
    res = rubric.evaluate(_run(str(tmp_path)))
    assert res.passed
    assert res.score == 1.0
    assert res.weight == 1.0


def test_file_contains_fails_when_substring_absent(tmp_path):
    (tmp_path / "f.txt").write_text("hello world")
    res = FileContains("f.txt", "missing").evaluate(_run(str(tmp_path)))
    assert not res.passed
    assert res.score == 0.0


def test_file_contains_fails_for_missing_file(tmp_path):
    res = FileContains("nope.txt", "x").evaluate(_run(str(tmp_path)))
    assert not res.passed
    assert "not found" in res.message


def test_file_contains_respects_weight(tmp_path):
    (tmp_path / "f.txt").write_text("hi")
    res = FileContains("f.txt", "hi", weight=2.5).evaluate(_run(str(tmp_path)))
    assert res.passed
    assert res.score == 2.5


# ============================================================ FileNotContains


def test_file_not_contains_passes_when_anti_pattern_absent(tmp_path):
    (tmp_path / "f.py").write_text("def add(a, b): return a + b")
    res = FileNotContains("f.py", "+ 1").evaluate(_run(str(tmp_path)))
    assert res.passed


def test_file_not_contains_fails_when_anti_pattern_present(tmp_path):
    (tmp_path / "f.py").write_text("def add(a, b): return a + b + 1")
    res = FileNotContains("f.py", "+ 1").evaluate(_run(str(tmp_path)))
    assert not res.passed


# ============================================================ FileRegex


def test_file_regex_passes(tmp_path):
    (tmp_path / "f.py").write_text("def subtract(a, b):\n    return a - b\n")
    res = FileRegex("f.py", r"def\s+subtract").evaluate(_run(str(tmp_path)))
    assert res.passed


def test_file_regex_fails_no_match(tmp_path):
    (tmp_path / "f.py").write_text("def add(a, b): pass")
    res = FileRegex("f.py", r"def\s+subtract").evaluate(_run(str(tmp_path)))
    assert not res.passed


# ============================================================ CommandSucceeds


def test_command_succeeds_passes_on_zero_exit(tmp_path):
    res = CommandSucceeds("true", timeout=5).evaluate(_run(str(tmp_path)))
    assert res.passed


def test_command_succeeds_fails_on_nonzero_exit(tmp_path):
    res = CommandSucceeds("false", timeout=5).evaluate(_run(str(tmp_path)))
    assert not res.passed
    assert "exit" in res.message.lower()


def test_command_succeeds_handles_timeout(tmp_path):
    res = CommandSucceeds("sleep 3", timeout=0.2).evaluate(_run(str(tmp_path)))
    assert not res.passed
    assert "timeout" in res.message.lower()


def test_command_runs_in_workspace_cwd(tmp_path):
    (tmp_path / "marker.txt").write_text("here")
    # Use ls + grep to assert the cwd is the workspace.
    res = CommandSucceeds("ls | grep -q marker.txt", timeout=5).evaluate(
        _run(str(tmp_path))
    )
    assert res.passed


# ============================================================ MaxToolCalls / MaxSeconds


def test_max_tool_calls_passes_under_limit():
    res = MaxToolCalls(10).evaluate(_run("/tmp", tool_call_count=5))
    assert res.passed


def test_max_tool_calls_fails_over_limit():
    res = MaxToolCalls(10).evaluate(_run("/tmp", tool_call_count=15))
    assert not res.passed


def test_max_tool_calls_at_exact_limit_passes():
    res = MaxToolCalls(10).evaluate(_run("/tmp", tool_call_count=10))
    assert res.passed


def test_max_seconds_passes_under_limit():
    res = MaxSeconds(60.0).evaluate(_run("/tmp", elapsed_seconds=42.5))
    assert res.passed


def test_max_seconds_fails_over_limit():
    res = MaxSeconds(60.0).evaluate(_run("/tmp", elapsed_seconds=120.0))
    assert not res.passed


# ============================================================ ResponseContains / Matches


def test_response_contains_case_insensitive_by_default():
    res = ResponseContains("Pass").evaluate(_run("/tmp", final_response="all tests pass!"))
    assert res.passed


def test_response_contains_case_sensitive_when_requested():
    res = ResponseContains("Pass", case_sensitive=True).evaluate(
        _run("/tmp", final_response="pass")
    )
    assert not res.passed


def test_response_matches_regex():
    res = ResponseMatches(r"off[- ]by[- ]one").evaluate(
        _run("/tmp", final_response="I found an off-by-one bug")
    )
    assert res.passed


def test_response_matches_regex_failure():
    res = ResponseMatches(r"off[- ]by[- ]one").evaluate(
        _run("/tmp", final_response="I found a thing")
    )
    assert not res.passed
