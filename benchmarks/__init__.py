"""Benchmark suite for measuring agent performance over time.

Goal: establish per-mode baselines that can be compared across:
  * different LLM providers + models (gpt-4o vs. gemini vs. ollama variants)
  * different mu-cli versions (HEAD vs. tagged release)
  * different prompt / configuration tweaks

This is NOT a pytest suite — benchmarks make real LLM calls (slow,
expensive, non-deterministic). They are opt-in via `python -m benchmarks
run`. The pytest suite (`tests/test_benchmarks_*.py`) covers the *harness
machinery* with fake providers; it runs every CI.

See `benchmarks/README.md` for usage and `benchmarks/specs/` for the
example benchmarks per agent mode.
"""

from .recorder import BenchmarkRecorder, load_baseline, save_baseline
from .report import render_baseline, render_comparison, report_path_for, write_report
from .rubrics import (
    CommandSucceeds,
    FileContains,
    FileNotContains,
    FileRegex,
    MaxSeconds,
    MaxToolCalls,
    ResponseContains,
    ResponseMatches,
    Rubric,
    RubricResult,
)
from .spec import BenchmarkResult, BenchmarkRun, BenchmarkSpec
from .harness import run_benchmark

__all__ = [
    "BenchmarkRecorder",
    "BenchmarkResult",
    "BenchmarkRun",
    "BenchmarkSpec",
    "CommandSucceeds",
    "FileContains",
    "FileNotContains",
    "FileRegex",
    "MaxSeconds",
    "MaxToolCalls",
    "ResponseContains",
    "ResponseMatches",
    "Rubric",
    "RubricResult",
    "load_baseline",
    "render_baseline",
    "render_comparison",
    "report_path_for",
    "run_benchmark",
    "save_baseline",
    "write_report",
]
