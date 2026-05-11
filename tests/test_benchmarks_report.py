"""Unit tests for the Markdown report renderer."""

import json
import os

import pytest

from benchmarks.recorder import save_baseline
from benchmarks.report import (
    render_baseline,
    render_comparison,
    report_path_for,
    write_report,
)
from benchmarks.rubrics import RubricResult
from benchmarks.spec import BenchmarkResult, BenchmarkRun


def _make_result(
    spec_name="spec_a",
    *,
    passed=True,
    score=1.0,
    mode="default",
    tokens=100,
    elapsed=10.0,
    tool_calls=3,
    final_response="all done",
    rubric_messages=None,
    error=None,
):
    run = BenchmarkRun(
        spec_name=spec_name,
        mode=mode,
        provider="openai",
        model="gpt-4o-mini",
        started_at_unix=0.0,
        elapsed_seconds=elapsed,
        iterations=2,
        tool_call_count=tool_calls,
        tokens={"input": tokens // 2, "output": tokens // 2, "total": tokens},
        cost_estimate=0.001,
        final_response=final_response,
        workspace_path="/tmp/x",
        status="completed" if not error else "error",
        error=error,
    )
    rubric_results = [
        RubricResult(name="r1", passed=True, weight=1.0, score=1.0, message="ok"),
        RubricResult(
            name="r2",
            passed=passed,
            weight=1.0,
            score=1.0 if passed else 0.0,
            message=(rubric_messages or {}).get("r2", "detail"),
        ),
    ]
    return BenchmarkResult(
        run=run,
        rubric_results=rubric_results,
        score=score,
        max_score=2.0,
        passed=passed,
    )


# ============================================================ render_baseline


def test_render_baseline_returns_markdown_string():
    results = [_make_result()]
    md = render_baseline([r.to_dict() for r in results])
    assert isinstance(md, str)
    assert md.startswith("# ")  # markdown heading
    assert "## Per-spec aggregates" in md
    assert "## Run details" in md


def test_render_baseline_contains_provider_and_model_in_header():
    md = render_baseline([_make_result().to_dict()])
    assert "openai" in md
    assert "gpt-4o-mini" in md


def test_render_baseline_summary_row_per_spec():
    records = [
        _make_result("spec_a").to_dict(),
        _make_result("spec_b", mode="debug", passed=False, score=0.5).to_dict(),
    ]
    md = render_baseline(records)
    assert "`spec_a`" in md
    assert "`spec_b`" in md
    assert "default" in md
    assert "debug" in md


def test_render_baseline_handles_empty_records():
    md = render_baseline([])
    assert "no records" in md.lower()


def test_render_baseline_passes_pct_calculation():
    """2 pass, 1 fail → 67%."""
    records = [
        _make_result("a", passed=True).to_dict(),
        _make_result("b", passed=True).to_dict(),
        _make_result("c", passed=False, score=0.3).to_dict(),
    ]
    md = render_baseline(records)
    # The exact "(67%)" or "(66%)" depends on rounding; just ensure pass count line is present.
    assert "2 / 3" in md
    # Failed run should be marked with a fail emoji or label
    assert "❌" in md or "fail" in md.lower()


def test_render_baseline_includes_rubric_breakdown():
    rec = _make_result(
        rubric_messages={"r2": "found 'subtract' in calc.py"},
    ).to_dict()
    md = render_baseline([rec])
    # Each rubric appears as a table row
    assert "| `r1` |" in md or "`r1`" in md
    assert "found 'subtract' in calc.py" in md


def test_render_baseline_truncates_long_final_response():
    long = "X" * 5000
    rec = _make_result(final_response=long).to_dict()
    md = render_baseline([rec])
    # Truncation marker appears
    assert "(truncated)" in md
    # The full 5000-char string is NOT inlined
    assert long not in md


def test_render_baseline_marks_errors():
    rec = _make_result(error="something broke", passed=False, score=0.0).to_dict()
    md = render_baseline([rec])
    assert "something broke" in md
    assert "❌" in md


# ============================================================ render_comparison


def test_render_comparison_no_regressions():
    a = [_make_result("s1").to_dict()]
    b = [_make_result("s1").to_dict()]
    md = render_comparison(a, b)
    assert "No regressions" in md


def test_render_comparison_flags_regression():
    a = [_make_result("s1", passed=True, score=1.0).to_dict()]
    b = [_make_result("s1", passed=False, score=0.3).to_dict()]
    md = render_comparison(a, b, label_a="main", label_b="head")
    assert "regression" in md.lower()
    assert "`s1`" in md
    assert "⚠️" in md
    # Negative score delta surfaces
    assert "-0.70" in md or "-0.7" in md


def test_render_comparison_token_delta_signed():
    a = [_make_result("s1", tokens=100).to_dict()]
    b = [_make_result("s1", tokens=250).to_dict()]
    md = render_comparison(a, b)
    # Signed integer with sign
    assert "+150" in md


# ============================================================ filenames


def test_report_path_for_replaces_jsonl_extension():
    assert report_path_for("/x/y/foo.jsonl") == "/x/y/foo.md"


def test_report_path_for_appends_md_for_other_extensions():
    assert report_path_for("/x/y/foo.txt") == "/x/y/foo.txt.md"


# ============================================================ write_report end-to-end


def test_write_report_creates_md_next_to_jsonl(tmp_path):
    jsonl = tmp_path / "baseline.jsonl"
    save_baseline(
        str(jsonl),
        [_make_result("spec_a"), _make_result("spec_b", mode="debug", passed=False, score=0.5)],
    )
    target = write_report(str(jsonl))
    assert os.path.exists(target)
    with open(target) as fh:
        body = fh.read()
    assert "Benchmark report" in body
    assert "`spec_a`" in body
    assert "`spec_b`" in body


def test_write_report_explicit_output_path(tmp_path):
    jsonl = tmp_path / "b.jsonl"
    md = tmp_path / "custom.md"
    save_baseline(str(jsonl), [_make_result("x")])
    target = write_report(str(jsonl), str(md))
    assert target == str(md)
    assert os.path.exists(str(md))


def test_render_baseline_is_deterministic_across_calls():
    """Same input → same output. Important: lets users diff reports in git."""
    records = [
        _make_result("s1", score=0.8).to_dict(),
        _make_result("s2", score=0.5, mode="debug").to_dict(),
    ]
    # The header contains a timestamp, so strip the "Generated:" line for
    # the determinism comparison.
    def _strip_timestamp(md):
        return "\n".join(
            line for line in md.splitlines() if not line.startswith("- **Generated:**")
        )

    a = _strip_timestamp(render_baseline(records))
    b = _strip_timestamp(render_baseline(records))
    assert a == b
