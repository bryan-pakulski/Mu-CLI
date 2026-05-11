"""Human-readable Markdown reports for benchmark baselines.

Renders a `.jsonl` baseline into a `.md` report with three sections:

  * **Summary** — one-line aggregate (provider, model, total, pass rate,
    median tokens, median elapsed).
  * **Per-spec table** — every spec scored, sortable by mode/score.
  * **Detail blocks** — per-run rubric breakdown so a human can see
    *which* rubrics fired and read the rubric messages.

The report is deterministic given the same input baseline, so it can be
checked in alongside the JSONL and diffed across commits.

A diff report (`render_comparison`) renders the output of
`recorder.diff(a, b)` as a regression table, marking dropped specs and
score regressions explicitly.
"""

from __future__ import annotations

import datetime as _dt
import statistics
from typing import Any, Dict, Iterable, List

from .recorder import diff, load_baseline, summarize


def _fmt_pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def _fmt_int(x: float) -> str:
    try:
        return f"{int(x):,}"
    except (TypeError, ValueError):
        return "?"


def _fmt_seconds(x: float) -> str:
    return f"{x:.1f}s"


def render_baseline(records: List[Dict[str, Any]], *, title: str = "Benchmark Report") -> str:
    """Render a baseline (list of result dicts) as a Markdown document."""
    if not records:
        return f"# {title}\n\n_no records_\n"

    # ---- header -----------------------------------------------------------
    first_run = records[0].get("run", {}) or {}
    provider = first_run.get("provider", "?")
    model = first_run.get("model", "?")
    now = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    total = len(records)
    passes = sum(1 for r in records if r.get("passed"))
    pass_rate = passes / total if total else 0.0
    total_tokens = sum(
        int((r.get("run", {}).get("tokens") or {}).get("total", 0) or 0) for r in records
    )
    elapsed_values = [
        float(r.get("run", {}).get("elapsed_seconds") or 0.0) for r in records
    ]
    total_elapsed = sum(elapsed_values)
    median_elapsed = statistics.median(elapsed_values) if elapsed_values else 0.0
    tokens_values = [
        int((r.get("run", {}).get("tokens") or {}).get("total", 0) or 0) for r in records
    ]
    median_tokens = statistics.median(tokens_values) if tokens_values else 0

    lines: List[str] = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"- **Generated:** {now}")
    lines.append(f"- **Provider:** `{provider}`")
    lines.append(f"- **Model:** `{model}`")
    lines.append(f"- **Runs:** {total}")
    lines.append(f"- **Passed:** {passes} / {total} ({_fmt_pct(pass_rate)})")
    lines.append(f"- **Tokens (total):** {_fmt_int(total_tokens)}")
    lines.append(f"- **Tokens (median per run):** {_fmt_int(median_tokens)}")
    lines.append(f"- **Wall time (total):** {_fmt_seconds(total_elapsed)}")
    lines.append(f"- **Wall time (median per run):** {_fmt_seconds(median_elapsed)}")
    lines.append("")

    # ---- per-spec aggregate table ----------------------------------------
    summaries = summarize(records)
    lines.append("## Per-spec aggregates")
    lines.append("")
    lines.append("| spec | mode | runs | pass | score | tokens (mean) | elapsed (mean) | tool calls (mean) |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
    for name in sorted(summaries):
        s = summaries[name]
        pass_marker = "✅" if s.pass_rate == 1.0 else ("⚠️" if s.pass_rate > 0 else "❌")
        lines.append(
            f"| `{s.spec_name}` | {s.mode} | {s.runs} | "
            f"{pass_marker} {s.passes}/{s.runs} | {s.mean_score:.2f} | "
            f"{_fmt_int(s.mean_tokens_total)} | "
            f"{_fmt_seconds(s.mean_elapsed_seconds)} | "
            f"{s.mean_tool_calls:.1f} |"
        )
    lines.append("")

    # ---- per-run detail blocks -------------------------------------------
    lines.append("## Run details")
    lines.append("")
    for i, rec in enumerate(records, start=1):
        run = rec.get("run", {}) or {}
        spec_name = run.get("spec_name", "?")
        mode = run.get("mode", "?")
        score = float(rec.get("score") or 0.0)
        passed = bool(rec.get("passed"))
        marker = "✅ pass" if passed else "❌ fail"
        tokens = run.get("tokens") or {}
        elapsed = float(run.get("elapsed_seconds") or 0.0)
        tool_calls = int(run.get("tool_call_count") or 0)
        status = run.get("status") or "?"
        error = run.get("error") or ""

        lines.append(f"### {i}. `{spec_name}` ({mode}) — {marker} (score {score:.2f})")
        lines.append("")
        lines.append(f"- **Status:** `{status}`")
        if error:
            lines.append(f"- **Error:** `{error}`")
        lines.append(
            f"- **Iterations:** {run.get('iterations', 0)} | "
            f"**Tool calls:** {tool_calls} | "
            f"**Elapsed:** {_fmt_seconds(elapsed)}"
        )
        lines.append(
            f"- **Tokens:** in {_fmt_int(tokens.get('input', 0))}, "
            f"out {_fmt_int(tokens.get('output', 0))}, "
            f"total {_fmt_int(tokens.get('total', 0))}"
        )
        lines.append("")
        lines.append("**Rubric:**")
        lines.append("")
        lines.append("| ✓ | weight | score | rubric | detail |")
        lines.append("|---|---:|---:|---|---|")
        for r in rec.get("rubric_results", []) or []:
            tick = "✅" if r.get("passed") else "❌"
            lines.append(
                f"| {tick} | {r.get('weight', 0):.1f} | "
                f"{r.get('score', 0):.1f} | `{r.get('name', '?')}` | "
                f"{(r.get('message') or '').strip()[:120]} |"
            )
        lines.append("")

        final = (run.get("final_response") or "").strip()
        if final:
            # Cap the response preview so the report stays readable for long
            # research-mode answers.
            preview = final if len(final) <= 1500 else final[:1500] + "\n…(truncated)"
            lines.append("**Final response:**")
            lines.append("")
            lines.append("```")
            lines.append(preview)
            lines.append("```")
            lines.append("")

    return "\n".join(lines)


def render_comparison(
    baseline_a_records: List[Dict[str, Any]],
    baseline_b_records: List[Dict[str, Any]],
    *,
    label_a: str = "A",
    label_b: str = "B",
    title: str = "Benchmark Comparison",
) -> str:
    """Render a Markdown diff between two baselines."""
    deltas = diff(baseline_a_records, baseline_b_records)

    lines: List[str] = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"- **A:** {label_a} ({len(baseline_a_records)} runs)")
    lines.append(f"- **B:** {label_b} ({len(baseline_b_records)} runs)")
    lines.append("")
    regressed = [name for name, d in deltas.items() if d.regressed]
    if regressed:
        lines.append(f"⚠️ **{len(regressed)} regression(s)**: " + ", ".join(f"`{n}`" for n in regressed))
    else:
        lines.append("✅ No regressions.")
    lines.append("")

    lines.append("| spec | Δscore | Δtokens | Δelapsed | Δtools | Δpass rate | regressed |")
    lines.append("|---|---:|---:|---:|---:|---:|:---:|")
    for name in sorted(deltas):
        d = deltas[name]
        marker = "⚠️" if d.regressed else ""
        lines.append(
            f"| `{name}` | {d.score_delta:+.2f} | {int(d.tokens_delta):+,} | "
            f"{d.elapsed_delta:+.1f}s | {d.tool_calls_delta:+.1f} | "
            f"{d.pass_rate_delta:+.0%} | {marker} |"
        )
    lines.append("")
    return "\n".join(lines)


def report_path_for(jsonl_path: str) -> str:
    """Conventional `.md` path beside a `.jsonl` baseline."""
    if jsonl_path.endswith(".jsonl"):
        return jsonl_path[: -len(".jsonl")] + ".md"
    return jsonl_path + ".md"


def write_report(jsonl_path: str, md_path: str = None) -> str:
    """Render the report for a baseline JSONL and write it to disk.

    Returns the path written. `md_path` defaults to the conventional
    sibling of the JSONL file.
    """
    records = load_baseline(jsonl_path)
    md = render_baseline(records, title=f"Benchmark report: {jsonl_path}")
    target = md_path or report_path_for(jsonl_path)
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(md)
    return target


__all__ = [
    "render_baseline",
    "render_comparison",
    "report_path_for",
    "write_report",
]
