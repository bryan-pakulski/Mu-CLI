"""Ingest + analyze a per-run trace JSONL (the read side of the emitter).

``parse_trace(path)`` streams a trace file into a typed :class:`TraceRun`.
``build_series(run)`` derives the dashboard series from it — context-growth,
tokenizer drift, compaction/nudge/tool/subagent timelines, tool histograms,
redundant-read events, nudge efficacy, memory counts, token breakdown.

Everything here is a pure function over the parsed run, so it is unit-testable
and shared by the GUI router (``mu/gui/routers/traces.py``) and the
``mucli trace analyze`` CLI. Robust to truncated/blank lines and missing
fields — a malformed line is skipped, never raised.
"""

from __future__ import annotations

import glob
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from .emitter import trace_dir


# ----------------------------------------------------------- parsing


@dataclass
class TraceRun:
    """One parsed trace file."""

    path: str
    run_id: str = ""
    header: Dict[str, Any] = field(default_factory=dict)
    iters: List[Dict[str, Any]] = field(default_factory=list)
    tools: List[Dict[str, Any]] = field(default_factory=list)
    nudges: List[Dict[str, Any]] = field(default_factory=list)
    compactions: List[Dict[str, Any]] = field(default_factory=list)
    requests: List[Dict[str, Any]] = field(default_factory=list)
    context_artifacts: List[Dict[str, Any]] = field(default_factory=list)
    turn_end: Optional[Dict[str, Any]] = None
    bytes: int = 0

    @property
    def iter_count(self) -> int:
        return len(self.iters)


def _iter_jsonl(path: str) -> Iterable[Dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(obj, dict):
                    yield obj
    except OSError:
        return


def parse_trace(path: str) -> TraceRun:
    """Stream a trace JSONL into a :class:`TraceRun`. Never raises on bad data."""
    run = TraceRun(path=path)
    try:
        run.bytes = os.path.getsize(path)
    except OSError:
        run.bytes = 0
    for obj in _iter_jsonl(path):
        t = obj.get("type")
        if t == "run_start":
            run.header = obj
            run.run_id = obj.get("run_id", "") or run.run_id
        elif t == "iter":
            run.iters.append(obj)
            if not run.run_id:
                run.run_id = obj.get("run_id", "")
        elif t == "tool":
            run.tools.append(obj)
        elif t == "nudge":
            run.nudges.append(obj)
        elif t == "compaction":
            run.compactions.append(obj)
        elif t == "request":
            run.requests.append(obj)
        elif t == "context_artifact":
            run.context_artifacts.append(obj)
        elif t == "turn_end":
            run.turn_end = obj
            if not run.run_id:
                run.run_id = obj.get("run_id", "")
    return run


# ----------------------------------------------------------- discovery / resolve


def _trace_files() -> List[str]:
    """All trace JSONL files, newest first (by mtime via sorted basename)."""
    return sorted(glob.glob(os.path.join(trace_dir(), "*.jsonl")), reverse=True)


def _read_header(path: str) -> Dict[str, Any]:
    """Read only the first JSON line (run_start) for the list view."""
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    return json.loads(line)
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return {}


def _iter_count_fast(path: str) -> int:
    """Count ``"type":"iter"`` lines without a full parse — cheap for listing."""
    try:
        with open(path, encoding="utf-8") as fh:
            return sum(
                1
                for line in fh
                if '"type": "iter"' in line or '"type":"iter"' in line
            )
    except OSError:
        return 0


def list_trace_runs() -> List[Dict[str, Any]]:
    """List every trace run (newest first) with header metadata + iter count.

    Single source of truth shared by the GUI router's ``GET /api/traces`` and
    the agent-facing ``list_traces`` tool. Reads only the first line per file
    (the ``run_start`` header) plus a cheap iter-line count — no full parse —
    so a directory of large traces stays cheap to list.
    """
    out: List[Dict[str, Any]] = []
    for path in _trace_files():
        header = _read_header(path)
        if not header:
            continue
        out.append(
            {
                "run_id": header.get("run_id", ""),
                "session": header.get("session", ""),
                "model": header.get("model", ""),
                "provider": header.get("provider", ""),
                "mode": header.get("mode", ""),
                "context_limit": header.get("context_limit", 0),
                "max_iterations": header.get("max_iterations", 0),
                "iters": _iter_count_fast(path),
                "bytes": os.path.getsize(path) if os.path.exists(path) else 0,
                "file": os.path.basename(path),
            }
        )
    return out


def find_trace_path(run_id: str) -> Optional[str]:
    """Resolve a run_id to its trace file path, or ``None`` if not found.

    Matches the run_id anywhere in the filename (traces are named
    ``<session>_run_<id>.jsonl``), then falls back to an exact filename match
    under the trace dir. Callers that need a raise-on-miss (the GUI router)
    wrap this; the agent tools return an error envelope on ``None``.
    """
    for path in _trace_files():
        if run_id and run_id in os.path.basename(path):
            return path
    target = os.path.join(trace_dir(), run_id)
    if os.path.exists(target):
        return target
    return None


def load_session_runs(session_name: str) -> List["TraceRun"]:
    """Parse every trace run for one session, oldest-first (chronological).

    A session spans multiple runs (each agent-loop invocation = one run).
    Order is by file mtime ascending — ``run_start`` carries no timestamp, and
    the run-id hex is a uuid (not monotonic), so mtime is the only chronological
    proxy. Matches on the header ``session`` field (accurate even when session
    names are substrings of one another), not the filename prefix.
    """
    selected: List[tuple] = []
    for path in glob.glob(os.path.join(trace_dir(), "*.jsonl")):
        header = _read_header(path)
        if header.get("session") == session_name:
            try:
                selected.append((os.path.getmtime(path), path))
            except OSError:
                continue
    selected.sort(key=lambda x: x[0])  # oldest first
    return [parse_trace(p) for _, p in selected]


def combine_runs(runs: List["TraceRun"]) -> "TraceRun":
    """Merge a chronologically-ordered list of runs into one TraceRun with
    globally-numbered iterations, so :func:`build_series` /
    :func:`build_summary` / :func:`build_trace_snapshot` produce a combined
    *session* view. Each record keeps its original ``run_id`` so the UI can
    draw run boundaries.

    Iterations are renumbered by *order* (not by adding an offset to the local
    iter value), so runs whose iters start at 1 or have gaps still lay out
    contiguously. Tools / nudges / compactions are remapped to the global iter
    via a per-run {local: global} map built from that run's iters; an event
    whose iter isn't in the map falls back to the run's last global iter.
    """
    merged = TraceRun(path="")
    if not runs:
        return merged
    merged.run_id = runs[0].run_id
    merged.header = dict(runs[0].header)
    merged.turn_end = runs[-1].turn_end
    global_iter = 0
    for run in runs:
        iter_map: Dict[Any, int] = {}
        for i in run.iters:
            local = i.get("iter")
            iter_map[local] = global_iter
            new_i = dict(i)
            new_i["iter"] = global_iter
            merged.iters.append(new_i)
            global_iter += 1
        # Fall-back global iter for events whose local iter isn't recorded as
        # an iteration (defensive — shouldn't normally happen).
        fallback = global_iter - 1 if run.iters else global_iter
        for t in run.tools:
            nt = dict(t)
            nt["iter"] = iter_map.get(t.get("iter"), fallback)
            merged.tools.append(nt)
        for n in run.nudges:
            nn = dict(n)
            local = n.get("iteration", n.get("iter"))
            if local in iter_map:
                gi = iter_map[local]
                nn["iteration"] = gi
                if "iter" in nn:
                    nn["iter"] = gi
            merged.nudges.append(nn)
        for c in run.compactions:
            nc = dict(c)
            nc["iter"] = iter_map.get(c.get("iter"), fallback)
            merged.compactions.append(nc)
        for req in run.requests:
            nr = dict(req); nr["iter"] = iter_map.get(req.get("iter"), fallback); merged.requests.append(nr)
        for artifact in run.context_artifacts:
            na = dict(artifact); na["iter"] = iter_map.get(artifact.get("iter"), fallback); merged.context_artifacts.append(na)
    return merged


def build_session_view(
    runs: List["TraceRun"], cols: int = 128
) -> Dict[str, Any]:
    """Combined multi-run view for one session: merged series + summary +
    snapshot + per-run bounds, in the same shape as the single-run endpoint so
    the frontend reuses one render path.

    ``run_bounds`` marks each run's global [start_iter, end_iter] so the UI can
    draw run-boundary dividers; token/cost totals are summed across all runs'
    ``turn_end`` records (the merged run's ``turn_end`` is only the last run's,
    so ``build_summary`` alone would undercount).
    """
    from .snapshot import build_trace_snapshot

    merged = combine_runs(runs)
    series = build_series(merged)
    summary = build_summary(merged, series)
    snapshot = build_trace_snapshot(merged, cols=cols)

    # Token / cost totals: sum every run's turn_end (build_summary only sees the
    # merged run's = the last run's turn_end).
    total_in = sum(_num((r.turn_end or {}).get("total_in")) for r in runs)
    total_out = sum(_num((r.turn_end or {}).get("total_out")) for r in runs)
    total_cost = sum(_num((r.turn_end or {}).get("total_cost")) for r in runs)
    summary["total_in"] = int(total_in)
    summary["total_out"] = int(total_out)
    summary["total_cost"] = round(total_cost, 6)
    # Session status: completed only if every run completed.
    statuses = [(r.turn_end or {}).get("status") for r in runs]
    summary["status"] = "completed" if all(s == "completed" for s in statuses) else (
        statuses[-1] if statuses and statuses[-1] else "running"
    )

    run_bounds: List[Dict[str, Any]] = []
    gi = 0
    for run in runs:
        n = len(run.iters)
        run_bounds.append(
            {
                "run_id": run.run_id,
                "start_iter": gi,
                "end_iter": gi + n - 1,
                "iters": n,
                "model": run.header.get("model", ""),
                "mode": run.header.get("mode", ""),
                "status": (run.turn_end or {}).get("status", "running"),
            }
        )
        gi += n

    return {
        "run_id": "session:" + (runs[0].header.get("session", "") if runs else ""),
        "header": merged.header,
        "iters": merged.iters,
        "tools": merged.tools,
        "nudges": merged.nudges,
        "compactions": merged.compactions,
        "requests": merged.requests,
        "context_artifacts": merged.context_artifacts,
        "turn_end": merged.turn_end,
        "series": series,
        "snapshot": snapshot,
        "summary": summary,
        "run_bounds": run_bounds,
        "n_runs": len(runs),
        "path": None,
    }


# ----------------------------------------------------------- series derivation


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _iter_of(v: Any) -> int:
    """Coerce an iteration field to int, treating only missing/None as -1.

    ``int(v or -1)`` would wrongly map a legitimate ``0`` to ``-1`` (0 is
    falsy), so handle None/missing explicitly.
    """
    if v is None:
        return -1
    try:
        return int(v)
    except (TypeError, ValueError):
        return -1


def build_series(run: TraceRun) -> Dict[str, Any]:
    """Derive the dashboard-ready series from a parsed run.

    Returns a dict of named series, each a list of per-iteration points (or
    event lists for timelines). All pure over ``run`` — no I/O.
    """
    iters = run.iters
    n = len(iters)
    xs = [int(i.get("iter", k)) for k, i in enumerate(iters)]

    # --- context growth (total_est vs prompt_tokens_actual) + per-layer ---
    context = []
    layers_stacked: Dict[str, List[float]] = {
        "l0": [], "l1": [], "l1b": [], "l2": [], "l3": [], "l4b": [], "l5": []
    }
    drift: List[Dict[str, Any]] = []
    for i in iters:
        ctx = i.get("context", {}) or {}
        context.append(
            {
                "iter": i.get("iter"),
                "total_est": _num(ctx.get("total_est")),
                "actual": _num(ctx.get("prompt_tokens_actual")),
                # Drift-corrected real-prompt estimate — the representative
                # real fill (Ollama's prompt_tokens_actual is the cached
                # delta, near-zero in a warm loop). The GUI "Context growth"
                # chart plots this as the real line so a looming overflow is
                # visible instead of hidden behind a near-zero actual.
                "real_est": _num(ctx.get("prompt_tokens_real_est")),
                "drift_ratio": _num(ctx.get("drift_ratio")),
                "drift_pct": _num(ctx.get("drift_pct")),
                # Representative real fill for the chart's solid line: the
                # drift-corrected estimate, but never below the provider's own
                # reported count (frontier providers report the true full
                # prompt; Ollama reports the cached delta, which real_est
                # dominates).
                "real": max(_num(ctx.get("prompt_tokens_actual")),
                            _num(ctx.get("prompt_tokens_real_est"))),
            }
        )
        for key in layers_stacked:
            layers_stacked[key].append(_num(ctx.get(key)))
        drift.append(
            {
                "iter": i.get("iter"),
                "drift_pct": _num(ctx.get("drift_pct")),
                "actual": _num(ctx.get("prompt_tokens_actual")),
                "total_est": _num(ctx.get("total_est")),
            }
        )

    # --- model-directed context artifact lifecycle ---
    context_artifacts = sorted(run.context_artifacts, key=lambda a: (_iter_of(a.get("iter")), str(a.get("artifact_id", ""))))
    artifact_counts = {}
    for artifact in context_artifacts:
        state = str(artifact.get("state") or "unknown")
        artifact_counts[state] = artifact_counts.get(state, 0) + 1

    # --- token breakdown per iter ---
    tokens = []
    for i in iters:
        tk = i.get("tokens", {}) or {}
        tokens.append(
            {
                "iter": i.get("iter"),
                "in": _num(tk.get("in")),
                "out": _num(tk.get("out")),
                "cached": _num(tk.get("cached")),
                "reasoning": _num(tk.get("reasoning")),
                "cost_delta": _num(tk.get("cost_delta")),
            }
        )

    # --- per-iteration wall time (provider-call latency) ---
    latency = [
        {"iter": i.get("iter"), "wall_ms": _num(i.get("wall_ms"))}
        for i in iters
    ]

    # --- tool histogram + per-tool latency series ---
    tool_hist: Dict[str, Dict[str, Any]] = {}
    tool_latency: Dict[str, List[Dict[str, Any]]] = {}
    for tr in run.tools:
        name = str(tr.get("name") or "unknown")
        h = tool_hist.setdefault(
            name,
            {"name": name, "count": 0, "ok": 0, "error": 0, "latency_sum": 0.0,
             "cache_hits": 0, "result_bytes_sum": 0, "error_codes": {}},
        )
        h["count"] += 1
        if tr.get("ok"):
            h["ok"] += 1
        else:
            h["error"] += 1
            code = str(tr.get("error_code") or "unknown")
            h["error_codes"][code] = h["error_codes"].get(code, 0) + 1
        h["latency_sum"] += _num(tr.get("latency_ms"))
        if tr.get("cache_hit"):
            h["cache_hits"] += 1
        h["result_bytes_sum"] += _num(tr.get("result_bytes"))
        tool_latency.setdefault(name, []).append(
            {
                "iter": tr.get("iter"),
                "latency_ms": _num(tr.get("latency_ms")),
                "ok": bool(tr.get("ok")),
                "cache_hit": bool(tr.get("cache_hit")),
            }
        )
    for h in tool_hist.values():
        c = max(1, h["count"])
        h["avg_latency_ms"] = round(h["latency_sum"] / c, 2)
        h["cache_hit_rate"] = round(h["cache_hits"] / c, 3)
        h["avg_result_bytes"] = int(h["result_bytes_sum"] / c)
    for name, series in tool_latency.items():
        tool_hist[name]["latency_series"] = series

    # --- compaction timeline ---
    compaction_timeline = []
    for c in run.compactions:
        compaction_timeline.append(
            {
                "iter": c.get("iter"),
                "kind": c.get("kind"),
                "tokens_before": _num(c.get("tokens_before")),
                "tokens_after": _num(c.get("tokens_after")),
                "tokens_saved": _num(c.get("tokens_saved")),
                "summarizer": c.get("summarizer"),
                "keep_recent": c.get("keep_recent"),
                "budget": c.get("budget"),
                "anchor_delta": _num(c.get("anchor_delta")),
            }
        )

    # --- nudge timeline + efficacy ---
    nudge_timeline = [
        {
            "iter": nd.get("iteration"),
            "kind": nd.get("kind"),
            "extra": {k: v for k, v in nd.items()
                      if k not in {"type", "run_id", "kind", "iteration"}},
        }
        for nd in run.nudges
    ]
    nudge_efficacy = _nudge_efficacy(run, k=3)

    # --- redundant reads (same path re-read with no intervening write) ---
    redundant_reads = _redundant_reads(run)

    # --- subagent timeline (per-iter snapshot deltas) ---
    subagent_timeline = []
    for i in iters:
        sa = i.get("subagents", {}) or {}
        subagent_timeline.append(
            {
                "iter": i.get("iter"),
                "active": int(sa.get("active", 0) or 0),
                "stuck": int(sa.get("stuck", 0) or 0),
                "stall": int(sa.get("stall", 0) or 0),
                "children": sa.get("children", []) or [],
            }
        )

    # --- memory counts per iter ---
    memory_series = []
    for i in iters:
        mem = i.get("memory", {}) or {}
        memory_series.append(
            {
                "iter": i.get("iter"),
                "task_memory_count": int(mem.get("task_memory_count", 0) or 0),
                "scratchpad_count": int(mem.get("scratchpad_count", 0) or 0),
                "by_status": mem.get("by_status", {}) or {},
            }
        )

    return {
        "n": n,
        "xs": xs,
        "context": context,
        "layers_stacked": layers_stacked,
        "drift": drift,
        "tokens": tokens,
        "latency": latency,
        "tool_histogram": list(tool_hist.values()),
        "compaction_timeline": compaction_timeline,
        "nudge_timeline": nudge_timeline,
        "nudge_efficacy": nudge_efficacy,
        "redundant_reads": redundant_reads,
        "subagent_timeline": subagent_timeline,
        "memory_series": memory_series,
        "context_artifacts": context_artifacts,
        "context_artifact_counts": artifact_counts,
        "requests": sorted(run.requests, key=lambda req: _iter_of(req.get("iter"))),
    }


# ----------------------------------------------------------- efficacy / reads


_WRITE_TOOLS = {
    "write_file", "apply_diff", "search_and_replace_file", "bash",
    "bash_background", "save_memory", "update_memory_status", "todo_write",
    "todo_set_status", "todo_delete", "todo_clear",
}
_READ_TOOLS = {
    "read_file", "get_chunk", "list_dir", "search_for_string",
    "search_references", "retrieve_relevant_context", "get_workspace_details",
}


def _nudge_efficacy(run: TraceRun, k: int = 3) -> List[Dict[str, Any]]:
    """For each nudge, did a materially different action follow within k iters?

    "Materially different" = a write tool, or a tool call whose (name, arg_fp)
    differs from the tool call immediately preceding the nudge's iteration.
    Falls back to ``broke=False`` when there's not enough surrounding data.
    """
    # Index tools by iter.
    by_iter: Dict[int, List[Dict[str, Any]]] = {}
    for tr in run.tools:
        by_iter.setdefault(_iter_of(tr.get("iter")), []).append(tr)
    out = []
    for nd in run.nudges:
        it = _iter_of(nd.get("iteration"))
        # The tool calls in the nudged iteration itself.
        pre = by_iter.get(it, [])
        pre_fps = {f"{t.get('name')}:{t.get('arg_fp')}" for t in pre}
        broke = False
        how = None
        for j in range(it + 1, it + 1 + k):
            calls = by_iter.get(j, [])
            if not calls:
                continue
            for c in calls:
                if str(c.get("name", "")) in _WRITE_TOOLS:
                    broke = True
                    how = "write"
                    break
                fp = f"{c.get('name')}:{c.get('arg_fp')}"
                if fp not in pre_fps:
                    broke = True
                    how = "novel_call"
                    break
            if broke:
                break
        out.append({"iter": it, "kind": nd.get("kind"), "broke": broke, "how": how})
    return out


def _redundant_reads(run: TraceRun) -> List[Dict[str, Any]]:
    """Flag a read of a path that was already read with no intervening write.

    Quantifies the context-gathering stall the recoverage nudge reacts to, and
    lets the dashboard correlate re-reads with compaction events (re-reading
    *caused by* state loss vs aimlessness).
    """
    last_read: Dict[str, int] = {}  # path -> iter of last read
    out = []
    # Walk tools in emit order; emit order is iter-major, input-order within.
    for tr in run.tools:
        name = str(tr.get("name", ""))
        path = str(tr.get("path", "") or "")
        it = _iter_of(tr.get("iter"))
        if name in _WRITE_TOOLS:
            # A write invalidates the "already read" state for that path.
            last_read.pop(path, None)
            continue
        if name in _READ_TOOLS and path:
            prev = last_read.get(path)
            if prev is not None and prev != it:
                out.append(
                    {
                        "iter": it,
                        "path": path,
                        "tool": name,
                        "prev_iter": prev,
                        "gap": it - prev,
                    }
                )
            last_read[path] = it
    return out


# ----------------------------------------------------------- overview summary


def build_summary(run: TraceRun, series: Dict[str, Any]) -> Dict[str, Any]:
    """Overview cards: totals, peaks, counts by type — the at-a-glance read."""
    drift_pts = [d["drift_pct"] for d in series["drift"]]
    peak_ctx = max(
        (c["actual"] for c in series["context"]), default=0.0
    )
    peak_est = max(
        (c["total_est"] for c in series["context"]), default=0.0
    )
    wall_pts = [w["wall_ms"] for w in series["latency"]]
    total_wall = sum(wall_pts) if wall_pts else 0.0
    peak_wall = max(wall_pts, default=0.0)
    mean_wall = total_wall / max(1, len(wall_pts))
    # Median is more robust than mean for drift_pct — the (actual−est)/actual
    # formula blows up when prompt_tokens_actual is small, so the mean is dragged
    # by a few extreme outliers (e.g. −1985%). Median reflects the typical iter.
    sorted_drift = sorted(drift_pts)
    median_drift = (
        sorted_drift[len(sorted_drift) // 2] if sorted_drift else 0.0
    )
    comp_by_kind: Dict[str, int] = {}
    mechanical = 0
    for c in series["compaction_timeline"]:
        comp_by_kind[c["kind"] or "unknown"] = comp_by_kind.get(c["kind"] or "unknown", 0) + 1
        if c.get("summarizer") == "mechanical":
            mechanical += 1
    nudge_by_kind: Dict[str, int] = {}
    for nd in series["nudge_timeline"]:
        nudge_by_kind[nd["kind"] or "unknown"] = nudge_by_kind.get(nd["kind"] or "unknown", 0) + 1
    nudges_broken = sum(1 for e in series["nudge_efficacy"] if e["broke"])

    total_in = 0.0
    total_out = 0.0
    total_cost = 0.0
    if run.turn_end:
        total_in = _num(run.turn_end.get("total_in"))
        total_out = _num(run.turn_end.get("total_out"))
        total_cost = _num(run.turn_end.get("total_cost"))
    else:
        for t in series["tokens"]:
            total_in += t["in"]
            total_out += t["out"]
            total_cost += t["cost_delta"]

    subagent_iters = sum(1 for s in series["subagent_timeline"] if s["active"])

    return {
        "run_id": run.run_id,
        "session": run.header.get("session", ""),
        "model": run.header.get("model", ""),
        "provider": run.header.get("provider", ""),
        "mode": run.header.get("mode", ""),
        "context_limit": int(run.header.get("context_limit", 0) or 0),
        "max_iterations": int(run.header.get("max_iterations", 0) or 0),
        "iters": run.iter_count,
        "total_in": int(total_in),
        "total_out": int(total_out),
        "total_cost": round(total_cost, 6),
        "compaction_count": len(series["compaction_timeline"]),
        "compaction_by_kind": comp_by_kind,
        "mechanical_fallback_count": mechanical,
        "nudge_count": len(series["nudge_timeline"]),
        "nudge_by_kind": nudge_by_kind,
        "nudges_broken": nudges_broken,
        "subagent_iters": subagent_iters,
        "peak_context": int(peak_ctx),
        "peak_estimated": int(peak_est),
        "peak_drift_abs": round(max((abs(d) for d in drift_pts), default=0.0), 2),
        "mean_drift": round(
            sum(drift_pts) / max(1, len(drift_pts)), 2
        ) if drift_pts else 0.0,
        "median_drift": round(median_drift, 2),
        "total_wall_ms": int(total_wall),
        "peak_wall_ms": int(peak_wall),
        "mean_wall_ms": int(mean_wall),
        "tool_calls": len(run.tools),
        "request_count": len(run.requests),
        "context_artifact_counts": series.get("context_artifact_counts", {}),
        "redundant_reads": len(series["redundant_reads"]),
        "status": (run.turn_end or {}).get("status", "running"),
        "bytes": run.bytes,
    }


__all__ = [
    "TraceRun",
    "parse_trace",
    "build_series",
    "build_summary",
    "list_trace_runs",
    "find_trace_path",
    "load_session_runs",
    "combine_runs",
    "build_session_view",
]
