"""Canvas-ready grid for the Trace Analyzer context-growth heat strip.

Analogous to ``mu/gui/memory_snapshot.build_memory_snapshot``: the server
turns the parsed trace into a small structured grid the frontend renders with
vanilla ``<canvas>`` (no charting library). The grid is iters (columns) ×
context layers (rows), with each cell carrying the layer's estimated token
count at that iteration as a 0..255 heat value — so the dashboard's
context-growth panel can render either a stacked-area line chart or a
layer-banded heat strip from the same payload.

Also produces the drift heat strip (one row, signed drift_pct mapped to a
diverging 0..255 scale) and the per-iter compaction markers.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .parser import TraceRun, _num

# Layer order top-to-bottom as assembled by the harness.
LAYER_ROWS: Tuple[str, ...] = ("l0", "l1a", "l1b", "l2", "l3", "l4b", "l5")
LAYER_LABELS: Dict[str, str] = {
    "l0": "L0 system",
    "l1b": "L1B skills",
    "l2": "L2 summary", "l3": "L3 memory", "l4b": "L4B compressed", "l5": "L5 history",
}


def _heat(value: float, vmax: float) -> int:
    """Map a non-negative value onto 0..255 relative to vmax."""
    if vmax <= 0:
        return 0
    v = value / vmax
    if v <= 0:
        return 0
    if v >= 1:
        return 255
    return int(round(v * 255))


def build_trace_snapshot(run: TraceRun, cols: int = 128) -> Dict[str, Any]:
    """Build the canvas-ready grid + drift strip + markers for one run.

    ``cols`` caps the column count (one per iteration, or downsampled when a
    run exceeds ``cols`` iterations by striding). Returns:

      * ``grid``: list of rows (one per layer), each a list of 0..255 heats,
        length == rendered column count.
      * ``drift_strip``: one row of 0..255 (diverging: <128 negative drift,
        >128 positive).
      * ``xs``: the actual iteration numbers for each column.
      * ``compaction_cols``: column indices where a compaction fired.
      * ``context_actual`` / ``context_est``: per-column totals for the line
        overlay.
      * ``meta``: layer labels + the vmax used for normalization.
    """
    iters = run.iters
    n = len(iters)
    if n == 0:
        return {
            "grid": [[] for _ in LAYER_ROWS],
            "drift_strip": [],
            "xs": [],
            "compaction_cols": [],
            "context_actual": [],
            "context_est": [],
            "meta": {"layers": list(LAYER_ROWS), "labels": LAYER_LABELS,
                     "layer_vmax": {}, "drift_abs_max": 0, "n": 0},
        }

    # Downsample by stride if the run is longer than cols.
    if n > cols:
        stride = n / cols
        idxs = [int(i * stride) for i in range(cols)]
        # Ensure the last iteration is always represented.
        if idxs[-1] != n - 1:
            idxs[-1] = n - 1
    else:
        idxs = list(range(n))

    # Per-layer max across rendered columns for normalization.
    per_layer_vals: Dict[str, List[float]] = {k: [] for k in LAYER_ROWS}
    drift_vals: List[float] = []
    actual_vals: List[float] = []
    est_vals: List[float] = []
    real_est_vals: List[float] = []
    for i in idxs:
        ctx = iters[i].get("context", {}) or {}
        for k in LAYER_ROWS:
            per_layer_vals[k].append(_num(ctx.get(k)))
        drift_vals.append(_num(ctx.get("drift_pct")))
        actual_vals.append(_num(ctx.get("prompt_tokens_actual")))
        est_vals.append(_num(ctx.get("total_est")))
        real_est_vals.append(_num(ctx.get("prompt_tokens_real_est")))

    layer_vmax = {k: max(vals, default=0.0) for k, vals in per_layer_vals.items()}
    drift_abs_max = max((abs(d) for d in drift_vals), default=0.0) or 1.0

    grid: List[List[int]] = []
    for k in LAYER_ROWS:
        vmax = layer_vmax[k]
        grid.append([_heat(v, vmax) for v in per_layer_vals[k]])

    # Diverging drift strip: 128 = 0%, <128 negative, >128 positive.
    drift_strip = []
    for d in drift_vals:
        if d == 0:
            drift_strip.append(128)
        elif d > 0:
            drift_strip.append(128 + _heat(d, drift_abs_max))
        else:
            drift_strip.append(128 - _heat(-d, drift_abs_max))

    # Compaction markers — column index of each compaction's iteration.
    compaction_cols = []
    comp_iters = {int(c.get("iter", -1) or -1) for c in run.compactions}
    # Map each rendered column to its iteration number for the marker lookup.
    col_iters = [int(iters[i].get("iter", i)) for i in idxs]
    for ci, itnum in enumerate(col_iters):
        if itnum in comp_iters:
            compaction_cols.append(ci)

    return {
        "grid": grid,
        "drift_strip": drift_strip,
        "xs": col_iters,
        "compaction_cols": compaction_cols,
        "context_actual": [int(v) for v in actual_vals],
        "context_est": [int(v) for v in est_vals],
        "context_real_est": [int(v) for v in real_est_vals],
        "meta": {
            "layers": list(LAYER_ROWS),
            "labels": LAYER_LABELS,
            "layer_vmax": {k: int(v) for k, v in layer_vmax.items()},
            "drift_abs_max": round(drift_abs_max, 2),
            "n": n,
            "rendered_cols": len(idxs),
        },
    }


__all__ = ["build_trace_snapshot", "LAYER_ROWS", "LAYER_LABELS"]