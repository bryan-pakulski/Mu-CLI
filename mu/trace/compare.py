"""Compare mucli runs from their JSONL traces (harness latency plan §9).

    python3 -m mu.trace.compare ~/.mucli/trace/a.jsonl ~/.mucli/trace/b.jsonl

Prints one row per trace: wall time, model turns, turn latency, cache-read share, cache
writes, `flush`/fetch-back turn share, summary calls and retries, so before/after runs of the
same task are measured the same way.
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any, Dict, Iterable, List

FETCH_BACK_TOOLS = frozenset({"flush", "recall", "get_chunk", "result_range", "result_head", "result_tail",
                              "result_search", "result_json_path", "discard_deferred_context"})


def _records(path: Path) -> List[Dict[str, Any]]:
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            out.append(record)
    return out


def summarize(path: Path) -> Dict[str, Any]:
    records = _records(path)
    iters = [r for r in records if r.get("type") == "iter"]
    tools = [r for r in records if r.get("type") == "tool"]
    stamps = [float(r["ts"]) for r in records if isinstance(r.get("ts"), (int, float))]
    walls = [int(r.get("wall_ms") or 0) for r in iters if r.get("wall_ms")]
    tokens = [r.get("tokens") or {} for r in iters]
    full = sum(int(t.get("cached", 0) or 0) + int(t.get("uncached_in", 0) or 0) for t in tokens)
    cached = sum(int(t.get("cached", 0) or 0) for t in tokens)
    by_iter: Dict[Any, List[str]] = {}
    for tool in tools:
        by_iter.setdefault(tool.get("iter"), []).append(str(tool.get("name") or ""))
    fetch_turns = sum(1 for names in by_iter.values() if names and all(n in FETCH_BACK_TOOLS for n in names))
    summary_calls = sum(int(((r.get("summary_usage") or {}).get("calls")) or 0) for r in iters if r.get("summary_usage"))
    timings = [t.get("timings") or {} for t in tokens]
    reloads = sum(1 for t in timings[1:] if float(t.get("load_s") or 0) > 0.5)
    prompt_eval = [float(t["prompt_eval_s"]) for t in timings if t.get("prompt_eval_s") is not None]
    eval_s = sum(float(t.get("eval_s") or 0) for t in timings)
    eval_tokens = sum(int(tok.get("out", 0) or 0) for tok, t in zip(tokens, timings) if t.get("eval_s"))
    return {
        "trace": path.name,
        "wall_s": round(max(stamps) - min(stamps), 1) if len(stamps) > 1 else round(sum(walls) / 1000, 1),
        "turns": len(iters),
        "turn_p50_s": round(statistics.median(walls) / 1000, 2) if walls else 0.0,
        "turn_p90_s": round(sorted(walls)[int(len(walls) * 0.9) - 1 if len(walls) > 1 else 0] / 1000, 2) if walls else 0.0,
        "cache_read_pct": round(cached / full * 100, 1) if full else 0.0,
        "cache_write_tokens": sum(int(t.get("cache_write", 0) or 0) for t in tokens),
        "tools_per_turn": round(len(tools) / max(1, len(by_iter)), 2),
        "fetch_back_turn_pct": round(fetch_turns / max(1, len(iters)) * 100, 1),
        "summary_calls": summary_calls,
        "retries": sum(1 for r in records if r.get("type") == "retry"),
        "reloads": reloads,
        "prompt_eval_p50_s": round(statistics.median(prompt_eval), 2) if prompt_eval else "",
        "tokens_per_s": round(eval_tokens / eval_s, 1) if eval_s else "",
        "output_tokens": sum(int(t.get("out", 0) or 0) for t in tokens),
    }


def table(rows: Iterable[Dict[str, Any]]) -> str:
    rows = list(rows)
    if not rows:
        return "no traces"
    columns = list(rows[0])
    widths = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in columns}
    lines = ["  ".join(c.ljust(widths[c]) for c in columns), "  ".join("-" * widths[c] for c in columns)]
    lines += ["  ".join(str(r[c]).ljust(widths[c]) for c in columns) for r in rows]
    return "\n".join(lines)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("traces", nargs="+", type=Path)
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = parser.parse_args(argv)
    rows = [summarize(p) for p in args.traces]
    print(json.dumps(rows, indent=2) if args.json else table(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
