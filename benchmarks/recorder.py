"""Persistence layer for benchmark results.

Format: append-only JSONL at `benchmarks/baselines/<filename>.jsonl`.
One line per `BenchmarkResult`. Filenames embed the provider, model,
and ISO date so a baseline file represents a single calibration run
of a model on a date.

Comparison ops:
  * `load_baseline(path)` -> list of result dicts
  * `summarize(results)` -> per-spec aggregates (pass rate, mean score,
    mean tokens, mean elapsed)
  * `diff(baseline_a, baseline_b)` -> per-spec delta (score, tokens,
    elapsed, pass-status change)

Designed so a CI job can:
  1. Run the full benchmark suite against the current HEAD.
  2. Compare against a checked-in baseline.
  3. Fail the build if regressions appear (per-spec score drops below
     a threshold).
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from .spec import BenchmarkResult


BASELINES_DIR = os.path.join(os.path.dirname(__file__), "baselines")


def _safe_token(value: str) -> str:
    """Sanitize provider / model names so they're safe path components."""
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in str(value))


def default_baseline_path(provider: str, model: str, *, date: Optional[str] = None) -> str:
    """Compose the conventional baseline path for a provider/model/date."""
    if date is None:
        date = _dt.date.today().isoformat()
    fname = f"{_safe_token(provider)}__{_safe_token(model)}__{date}.jsonl"
    return os.path.join(BASELINES_DIR, fname)


class BenchmarkRecorder:
    """Append BenchmarkResults to a JSONL baseline file."""

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    def append(self, result: BenchmarkResult) -> None:
        payload = result.to_dict()
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, default=str, sort_keys=True))
            fh.write("\n")

    def append_many(self, results: Iterable[BenchmarkResult]) -> None:
        for r in results:
            self.append(r)


def load_baseline(path: str) -> List[Dict[str, Any]]:
    """Load all result records from a JSONL baseline file."""
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def save_baseline(path: str, results: Iterable[BenchmarkResult]) -> None:
    """Write a baseline file from scratch (overwrites)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps(r.to_dict(), default=str, sort_keys=True))
            fh.write("\n")


# ----------------------------------------------------------------- analytics


@dataclass
class SpecSummary:
    spec_name: str
    mode: str
    runs: int
    passes: int
    pass_rate: float
    mean_score: float
    mean_tokens_total: float
    mean_elapsed_seconds: float
    mean_tool_calls: float


def summarize(records: List[Dict[str, Any]]) -> Dict[str, SpecSummary]:
    """Compute per-spec aggregates across all records in a baseline."""
    by_spec: Dict[str, List[Dict[str, Any]]] = {}
    for rec in records:
        name = rec.get("run", {}).get("spec_name", "?")
        by_spec.setdefault(name, []).append(rec)
    out: Dict[str, SpecSummary] = {}
    for spec_name, runs in by_spec.items():
        n = len(runs)
        passes = sum(1 for r in runs if r.get("passed"))
        score_sum = sum(float(r.get("score") or 0.0) for r in runs)
        tokens_sum = sum(
            int((r.get("run", {}).get("tokens") or {}).get("total", 0) or 0)
            for r in runs
        )
        elapsed_sum = sum(
            float(r.get("run", {}).get("elapsed_seconds") or 0.0) for r in runs
        )
        tc_sum = sum(
            int(r.get("run", {}).get("tool_call_count") or 0) for r in runs
        )
        out[spec_name] = SpecSummary(
            spec_name=spec_name,
            mode=str(runs[0].get("run", {}).get("mode") or ""),
            runs=n,
            passes=passes,
            pass_rate=passes / n if n else 0.0,
            mean_score=score_sum / n if n else 0.0,
            mean_tokens_total=tokens_sum / n if n else 0.0,
            mean_elapsed_seconds=elapsed_sum / n if n else 0.0,
            mean_tool_calls=tc_sum / n if n else 0.0,
        )
    return out


@dataclass
class SpecDelta:
    spec_name: str
    score_delta: float
    tokens_delta: float
    elapsed_delta: float
    tool_calls_delta: float
    pass_rate_delta: float
    regressed: bool


def diff(baseline_a: List[Dict[str, Any]], baseline_b: List[Dict[str, Any]]) -> Dict[str, SpecDelta]:
    """Per-spec delta from baseline A → baseline B.

    Negative `score_delta` is a regression; positive `tokens_delta` /
    `elapsed_delta` are also regressions (more cost / slower).
    A `regressed=True` flag is set when score dropped by >0.05 or
    pass_rate dropped at all.
    """
    a = summarize(baseline_a)
    b = summarize(baseline_b)
    out: Dict[str, SpecDelta] = {}
    for name in sorted(set(a.keys()) | set(b.keys())):
        sa = a.get(name)
        sb = b.get(name)
        if sa is None:
            # New spec in B; treat A as zero.
            score_delta = sb.mean_score
            tokens_delta = sb.mean_tokens_total
            elapsed_delta = sb.mean_elapsed_seconds
            tc_delta = sb.mean_tool_calls
            pass_rate_delta = sb.pass_rate
        elif sb is None:
            score_delta = -sa.mean_score
            tokens_delta = -sa.mean_tokens_total
            elapsed_delta = -sa.mean_elapsed_seconds
            tc_delta = -sa.mean_tool_calls
            pass_rate_delta = -sa.pass_rate
        else:
            score_delta = sb.mean_score - sa.mean_score
            tokens_delta = sb.mean_tokens_total - sa.mean_tokens_total
            elapsed_delta = sb.mean_elapsed_seconds - sa.mean_elapsed_seconds
            tc_delta = sb.mean_tool_calls - sa.mean_tool_calls
            pass_rate_delta = sb.pass_rate - sa.pass_rate
        regressed = score_delta < -0.05 or pass_rate_delta < 0
        out[name] = SpecDelta(
            spec_name=name,
            score_delta=score_delta,
            tokens_delta=tokens_delta,
            elapsed_delta=elapsed_delta,
            tool_calls_delta=tc_delta,
            pass_rate_delta=pass_rate_delta,
            regressed=regressed,
        )
    return out


__all__ = [
    "BASELINES_DIR",
    "BenchmarkRecorder",
    "SpecDelta",
    "SpecSummary",
    "default_baseline_path",
    "diff",
    "load_baseline",
    "save_baseline",
    "summarize",
]
