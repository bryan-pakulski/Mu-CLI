"""Benchmark CLI.

Usage:

    python -m benchmarks list
    python -m benchmarks list --mode debug
    python -m benchmarks run [--name NAME] [--mode MODE] [--provider P] [--model M]
                              [--baseline PATH] [--keep-workspace]
    python -m benchmarks compare PATH_A PATH_B
    python -m benchmarks summary PATH

`run` requires real LLM credentials (OPENAI_API_KEY, GEMINI_API_KEY, or
a local Ollama instance) and is therefore OPT-IN — never invoked by the
default pytest run.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from .recorder import (
    BenchmarkRecorder,
    default_baseline_path,
    diff,
    load_baseline,
    summarize,
)
from .report import (
    render_baseline,
    render_comparison,
    report_path_for,
    write_report,
)
from .specs import ALL_SPECS, by_mode, find


# --------------------------------------------------------------- providers


def _build_provider(provider_name: str, model: Optional[str]):
    name = (provider_name or "").lower()
    if name == "openai":
        from providers.openai import OpenAIProvider

        return OpenAIProvider(model_name=model or "gpt-4o-mini")
    if name == "gemini":
        from providers.gemini import GeminiProvider

        return GeminiProvider(model_name=model or "gemini-2.5-flash")
    if name == "ollama":
        from providers.ollama import OllamaProvider

        return OllamaProvider(model_name=model or "llama3")
    raise SystemExit(
        f"unknown provider {provider_name!r}; supported: openai, gemini, ollama"
    )


# --------------------------------------------------------------- subcommands


def _cmd_list(args: argparse.Namespace) -> int:
    specs = by_mode(args.mode) if args.mode else ALL_SPECS
    if not specs:
        print(f"No benchmarks for mode {args.mode!r}.")
        return 1
    print(f"{'mode':<10} {'name':<35} description")
    print("-" * 100)
    for s in specs:
        desc = s.description.replace("\n", " ")
        if len(desc) > 60:
            desc = desc[:57] + "..."
        print(f"{s.mode:<10} {s.name:<35} {desc}")
    print(f"\n{len(specs)} spec(s).")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    from .harness import run_benchmark

    # Resolve target specs
    if args.name:
        try:
            specs = [find(args.name)]
        except KeyError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    elif args.mode:
        specs = by_mode(args.mode)
    else:
        specs = list(ALL_SPECS)

    if not specs:
        print("No matching benchmarks.", file=sys.stderr)
        return 1

    provider = _build_provider(args.provider, args.model)
    baseline_path = args.baseline or default_baseline_path(
        provider.name, provider.model_name
    )
    recorder = BenchmarkRecorder(baseline_path)
    print(f"recording → {baseline_path}")

    fail_count = 0
    for spec in specs:
        print(f"\n=== {spec.mode}/{spec.name} ===")
        print(f"task: {spec.task[:120]}")
        result = run_benchmark(spec, provider, keep_workspace=args.keep_workspace)
        recorder.append(result)

        marker = "PASS" if result.passed else "FAIL"
        print(
            f"[{marker}] score={result.score:.2f} "
            f"tokens={result.run.tokens.get('total', 0)} "
            f"elapsed={result.run.elapsed_seconds:.1f}s "
            f"tool_calls={result.run.tool_call_count}"
        )
        if args.verbose:
            for r in result.rubric_results:
                tick = "✓" if r.passed else "✗"
                print(f"   {tick} {r.name:<55} {r.message}")
        if not result.passed:
            fail_count += 1
        if args.keep_workspace:
            print(f"   workspace retained: {result.run.workspace_path}")

    print(f"\n{len(specs) - fail_count}/{len(specs)} passed.")

    # Auto-write the human-parsable Markdown report alongside the JSONL.
    try:
        md_path = write_report(baseline_path)
        print(f"report → {md_path}")
    except Exception as exc:  # pragma: no cover — defensive
        print(f"warning: failed to write Markdown report: {exc}", file=sys.stderr)

    return 0 if fail_count == 0 else 3


def _cmd_report(args: argparse.Namespace) -> int:
    if not os.path.exists(args.path):
        print(f"baseline not found: {args.path}", file=sys.stderr)
        return 1
    target = args.output or report_path_for(args.path)
    written = write_report(args.path, target)
    if args.print:
        with open(written, "r", encoding="utf-8") as fh:
            print(fh.read())
    else:
        print(f"wrote {written}")
    return 0


def _cmd_summary(args: argparse.Namespace) -> int:
    records = load_baseline(args.path)
    if not records:
        print(f"empty or missing baseline: {args.path}", file=sys.stderr)
        return 1
    summaries = summarize(records)
    print(f"{'spec':<35} {'mode':<10} {'pass':<6} {'score':<6} {'tokens':<8} {'sec':<6} {'tools':<6}")
    print("-" * 90)
    for name, s in sorted(summaries.items()):
        print(
            f"{name:<35} {s.mode:<10} "
            f"{s.passes}/{s.runs:<4} "
            f"{s.mean_score:<6.2f} "
            f"{int(s.mean_tokens_total):<8} "
            f"{s.mean_elapsed_seconds:<6.1f} "
            f"{s.mean_tool_calls:<6.1f}"
        )
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    a = load_baseline(args.baseline_a)
    b = load_baseline(args.baseline_b)
    if not a or not b:
        print("one or both baselines are empty.", file=sys.stderr)
        return 1
    deltas = diff(a, b)
    print(f"{'spec':<35} {'Δscore':>8} {'Δtokens':>9} {'Δsec':>7} {'Δtools':>7} {'regressed':>11}")
    print("-" * 85)
    any_regression = False
    for name, d in sorted(deltas.items()):
        flag = "REGRESS" if d.regressed else ""
        any_regression = any_regression or d.regressed
        print(
            f"{name:<35} {d.score_delta:+8.2f} {int(d.tokens_delta):+9} "
            f"{d.elapsed_delta:+7.1f} {d.tool_calls_delta:+7.1f} {flag:>11}"
        )
    return 4 if any_regression else 0


# --------------------------------------------------------------- argparse


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="benchmarks", description="μCLI agent benchmark suite")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="List available benchmark specs")
    p_list.add_argument("--mode", choices=["default", "debug", "feature", "research", "loop"])
    p_list.set_defaults(func=_cmd_list)

    p_run = sub.add_parser("run", help="Run benchmarks (requires LLM credentials)")
    p_run.add_argument("--name", help="Run exactly one spec by name")
    p_run.add_argument("--mode", choices=["default", "debug", "feature", "research", "loop"])
    p_run.add_argument(
        "--provider",
        choices=["openai", "gemini", "ollama"],
        required=True,
    )
    p_run.add_argument("--model", help="Override the provider's default model")
    p_run.add_argument("--baseline", help="Output baseline JSONL path (default: auto-named)")
    p_run.add_argument("--keep-workspace", action="store_true", help="Don't clean up tmpdirs")
    p_run.add_argument("-v", "--verbose", action="store_true")
    p_run.set_defaults(func=_cmd_run)

    p_sum = sub.add_parser("summary", help="Show per-spec aggregates from a baseline file")
    p_sum.add_argument("path")
    p_sum.set_defaults(func=_cmd_summary)

    p_cmp = sub.add_parser("compare", help="Diff two baselines (exit 4 if regressions)")
    p_cmp.add_argument("baseline_a")
    p_cmp.add_argument("baseline_b")
    p_cmp.set_defaults(func=_cmd_compare)

    p_rep = sub.add_parser("report", help="Render a baseline JSONL as a Markdown report")
    p_rep.add_argument("path", help="Path to a baseline .jsonl file")
    p_rep.add_argument(
        "-o",
        "--output",
        help="Where to write the .md (default: sibling of the .jsonl)",
    )
    p_rep.add_argument(
        "-p",
        "--print",
        action="store_true",
        help="Also print the report to stdout after writing",
    )
    p_rep.set_defaults(func=_cmd_report)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
