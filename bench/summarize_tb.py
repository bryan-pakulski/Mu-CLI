#!/usr/bin/env python3
"""Summarize per-task Terminal-Bench runs without double-counting JSON files."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


def _duration_seconds(result: dict[str, Any]) -> float | None:
    explicit = result.get("duration_sec")
    if explicit is not None:
        try:
            return float(explicit)
        except (TypeError, ValueError):
            pass
    try:
        started = datetime.fromisoformat(result["agent_started_at"])
        ended = datetime.fromisoformat(result["agent_ended_at"])
    except (KeyError, TypeError, ValueError):
        return None
    return max(0.0, (ended - started).total_seconds())


def _aggregate_files(task_dir: Path) -> Iterable[Path]:
    """Yield only run-level result files, not nested per-trial copies."""

    direct = task_dir / "results.json"
    if direct.is_file():
        yield direct
    for path in task_dir.glob("*/results.json"):
        if path.is_file():
            yield path


def load_task_results(task_dir: Path) -> list[dict[str, Any]]:
    candidates: list[tuple[int, Path, list[dict[str, Any]]]] = []
    for path in _aggregate_files(task_dir):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        results = payload.get("results") if isinstance(payload, dict) else None
        if isinstance(results, list):
            candidates.append((path.stat().st_mtime_ns, path, results))
    if not candidates:
        return []
    return max(candidates, key=lambda item: item[0])[2]


def load_execution_metrics(task_dir: Path, limit: int) -> list[dict[str, Any]]:
    """Load command-only timing emitted by a controlled benchmark adapter."""

    candidates: list[tuple[int, dict[str, Any]]] = []
    for path in task_dir.glob("**/agent-logs/*-execution.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if not isinstance(payload, dict) or payload.get("schema") != 1:
            continue
        try:
            float(payload["execution_seconds"])
            float(payload["setup_seconds"])
        except (KeyError, TypeError, ValueError):
            continue
        candidates.append((path.stat().st_mtime_ns, payload))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [payload for _, payload in candidates[:limit]]


def summarize(root: Path, task_names: list[str]) -> tuple[list[str], bool]:
    lines: list[str] = []
    healthy = True
    resolved_trials = 0
    total_trials = 0
    resolved_tasks = 0
    scored_tasks = 0
    attempt_counts: list[int] = []
    total_execution_seconds = 0.0
    total_setup_seconds = 0.0
    total_fallback_seconds = 0.0
    timed_tasks = 0
    fallback_tasks = 0
    total_input_tokens = 0
    total_output_tokens = 0

    for task_name in task_names:
        results = load_task_results(root / task_name)
        if not results:
            healthy = False
            lines.append(f"  {task_name:<28} NO-RESULT")
            continue

        total_trials += len(results)
        scored_tasks += 1
        attempt_counts.append(len(results))
        passed = sum(result.get("is_resolved") is True for result in results)
        resolved_trials += passed
        resolved_tasks += int(passed > 0)
        timing = load_execution_metrics(root / task_name, len(results))
        if timing:
            execution_seconds = sum(float(item["execution_seconds"]) for item in timing)
            setup_seconds = sum(float(item["setup_seconds"]) for item in timing)
            total_execution_seconds += execution_seconds
            total_setup_seconds += setup_seconds
            timed_tasks += 1
            if len(timing) == 1:
                duration_text = (
                    f"{execution_seconds:.1f}s execution "
                    f"(+{setup_seconds:.1f}s setup excluded)"
                )
            else:
                duration_text = (
                    f"{execution_seconds:.1f}s execution total "
                    f"({execution_seconds / len(timing):.1f}s mean; "
                    f"+{setup_seconds:.1f}s setup excluded)"
                )
        else:
            durations = [
                duration
                for result in results
                if (duration := _duration_seconds(result)) is not None
            ]
            total_fallback_seconds += sum(durations)
            fallback_tasks += int(bool(durations))
            duration_text = (
                f"{sum(durations):.1f}s TB-agent (setup included)"
                if durations
                else "duration unavailable"
            )
        if len(results) == 1:
            status = "PASS" if passed else "FAIL"
        else:
            verdict = "PASS" if passed else "FAIL"
            status = f"BEST-{len(results)} {verdict} ({passed}/{len(results)})"
        modes = sorted(
            {
                str(result.get("failure_mode"))
                for result in results
                if result.get("failure_mode") not in (None, "unset")
            }
        )
        mode_text = f" [{', '.join(modes)}]" if modes else ""
        input_tokens = sum(
            int(result.get("total_input_tokens") or 0) for result in results
        )
        output_tokens = sum(
            int(result.get("total_output_tokens") or 0) for result in results
        )
        total_input_tokens += input_tokens
        total_output_tokens += output_tokens
        token_text = f"; {input_tokens:,} in/{output_tokens:,} out tokens"
        lines.append(
            f"  {task_name:<28} {status:<19} " f"{duration_text}{mode_text}{token_text}"
        )

    attempts = max(attempt_counts, default=0)
    score_label = f"best-of-{attempts}" if attempts > 1 else "task"
    lines.append(f"{score_label} score: {resolved_tasks}/{scored_tasks} tasks")
    lines.append(f"trial outcomes: {resolved_trials}/{total_trials} resolved")
    if timed_tasks:
        lines.append(
            f"execution total: {total_execution_seconds:.1f}s; "
            f"setup total (excluded): {total_setup_seconds:.1f}s"
        )
    if fallback_tasks:
        lines.append(
            f"TB-agent fallback time (setup included): {total_fallback_seconds:.1f}s"
        )
    lines.append(
        f"tokens total: {total_input_tokens:,} input; "
        f"{total_output_tokens:,} output"
    )
    return lines, healthy


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2:
        print("usage: summarize_tb.py OUTPUT_DIR TASK [TASK ...]", file=sys.stderr)
        return 2
    lines, healthy = summarize(Path(args[0]), args[1:])
    print("\n== PACK RESULTS ==")
    print("\n".join(lines))
    return 0 if healthy else 2


if __name__ == "__main__":
    raise SystemExit(main())
