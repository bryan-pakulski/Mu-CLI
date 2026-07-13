"""Context-window fingerprint for the GUI "Memory Map" panel.

``build_memory_snapshot`` turns the layered system prompt the harness
assembles each turn into a 2-D grid: one horizontal band per layer
(L0..L5), band height proportional to the layer's token share. Each
**cell carries a heat value** (0..255) rather than a raw color, so the
frontend can render either a change-frequency heatmap or a solid
per-layer color-coding from the same payload.

Two signals, one grid:

* **Per-layer hue** — every layer gets a fixed hue (L0 blue, L1 green,
  …, L5 red). The band's color identifies *which* layer it is at a
  glance.
* **Change frequency (hash-based)** — each layer's text is split into one
  chunk **per grid cell in its band** (``r * cols`` row-major slices, so
  the chunk count equals the band's cell count — one hash per displayed
  cell) and SHA-256 hashed. The fingerprint remembers each cell's last
  hash + a per-cell change counter, keyed by the session object and the
  ``(cols, rows)`` resolution. Every snapshot compares the current cell
  hashes to the stored ones; a cell whose hash changed increments its
  counter. Cell brightness encodes that counter (sqrt curve, capped), so
  the regions of a layer that change every iteration *glow* and the
  regions stable since first contact stay *dim*. That is the whole point
  of the panel: watching **which regions of each layer's memory churn**
  in real time, and how often — not just that the layer grew.

  Because chunking tracks the band's actual cell count, a layer whose
  band height changes (its token share drifted) is re-chunked. To keep
  the change signal across that resize, cells are compared by
  *fractional position* against the previous layout (the cell at "30%
  through the layer" still corresponds to "30% through"), so the regions
  that shifted still light up — at the cost of some boundary noise from
  the moved slice edges on that one snapshot. While the band height is
  stable (the common case within a turn), cells compare directly and the
  per-cell heat accumulates cleanly.

Each resolution keeps its own change history (keyed by ``(cols, rows)``),
so switching resolution starts a fresh heat for that view. Identical
content between two snapshots at the same resolution yields an identical
grid (deterministic); only changed cells brighten. Empty space is still
space: every layer always gets at least one band row, and a cell whose
slice has no text renders as a dim layer-colored cell rather than
vanishing.

The same builder feeds the REST endpoint (``/api/memory/state``) and the
``pre_provider_call`` hook that pushes a live snapshot per iteration, so
the layout is identical between the live and final frames.
"""

from __future__ import annotations

import hashlib
import logging
import math
import weakref
from typing import Any, Dict, List, Optional, Tuple

from utils.runtime_metrics import collect_context_layers

_logger = logging.getLogger(__name__)

# Canonical layer order — matches the assembly in
# mu/session/context.py:inject_hierarchical_context.
_LAYER_ORDER: Tuple[str, ...] = ("L0", "L1", "L1B", "L2", "L3", "L4B", "L5")

# A distinct hue per layer (HSL degrees) so bands are visually identifiable.
# Spread around the wheel; L5 (the volatile history) lands on red.
LAYER_HUES: Dict[str, int] = {
    "L0": 210,   # blue  — system prompt (stable)
    "L1": 135,   # green — workspace files
    "L1B": 168,  # teal  — installed skills
    "L2": 280,   # purple — conversation summary
    "L3": 25,    # orange — active goal
    "L4B": 50,   # yellow — retrieved snippets
    "L5": 358,   # red   — conversation history (churns most)
}

# Resolution bounds the router enforces. 256×256 is the documented max
# (65,536 cells); the live SSE push uses a smaller grid to stay light.
_MIN_RES = 16
_MAX_RES = 256
_DEFAULT_RES = 128

# Live-push resolution — capped so a per-iteration SSE event stays well
# under ~100 KB even at 256-wide display. The canvas scales to the same
# on-screen size as the full-res frame, so the handoff at turn end is
# visually seamless.
_LIVE_RES = 96

# Change-count at which a cell reaches max brightness (sqrt curve below).
# ~8 observed changes → fully hot; stable content stays dim.
_HEAT_REF = 8.0

# Minimum band height: every layer gets at least this many rows so an empty
# layer still renders as a visible (dim) band rather than disappearing.
_MIN_BAND = 1


def _clamp_res(value: int, default: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    if v < _MIN_RES:
        return _MIN_RES
    if v > _MAX_RES:
        return _MAX_RES
    return v


def _hash_color(chunk: str) -> str:
    """Deterministic chunk → ``#rrggbb`` (kept for the debug/identity view
    and for tests; the live grid uses heat ints, not this).

    SHA-256 of the UTF-8 bytes, take the first three bytes as RGB, then
    lift each channel into [40, 255] so colors are never near-invisible
    on a dark theme. Purely a function of the chunk text.
    """
    digest = hashlib.sha256(chunk.encode("utf-8", "replace")).digest()
    r, g, b = digest[0], digest[1], digest[2]

    def lift(byte: int) -> int:
        return 40 + (byte * 215 // 255)

    return f"#{lift(r):02x}{lift(g):02x}{lift(b):02x}"


def _chunk_hash(chunk: str) -> str:
    """Stable hash of one canonical chunk (hex digest prefix)."""
    return hashlib.sha256(chunk.encode("utf-8", "replace")).hexdigest()[:16]


def _sample_chunks(text: str, n: int) -> List[str]:
    """Split ``text`` into ``n`` evenly-spaced slices.

    Even sampling (rather than a prefix split) means a band represents its
    whole layer, not just its opening words — important for L5, where a
    long conversation's recent tail is usually what changed. Empty slices
    (when ``n`` exceeds the text length, or the layer is empty) yield
    ``""``, which the caller maps to a 0 (absent/transparent) cell.
    """
    if n <= 0:
        return []
    if not text:
        return ["" for _ in range(n)]
    tlen = len(text)
    out: List[str] = []
    for i in range(n):
        start = i * tlen // n
        end = (i + 1) * tlen // n
        out.append(text[start:end])
    return out


def _layer_text(session: Any, layer_id: str) -> str:
    """Best-effort text body for one layer.

    Imports ``_layer_content`` lazily so importing this module never pulls
    the commands package (and its CLI deps). Swallows builder failures —
    a layer that can't be materialized just contributes an empty band.
    """
    try:
        from mu.commands.memory import _layer_content

        return str(_layer_content(session, layer_id) or "")
    except Exception as exc:  # defensive — runs on the FastAPI thread too
        _logger.debug("memory_snapshot: layer %s read failed: %s", layer_id, exc)
        return ""


# ---------------------------------------------------------------- change tracking
#
# Per-session fingerprint: for each layer, the last hash of every canonical
# chunk plus a per-chunk change counter. Keyed by the session *object* via a
# WeakKeyDictionary, so the entry auto-evicts the moment the session is
# garbage-collected — change frequency is a runtime observation tied to one
# live session, not persisted history, and weak-keying means a recycled
# id() can never inherit a dead session's counts (which would otherwise
# make a fresh session's first snapshot look "already churning").
#
# Structure: { session(obj): { (cols, rows): { <layer>: { "hashes": [...], "counts": [...] } } } }
_FINGERPRINTS: "weakref.WeakKeyDictionary[Any, Dict[str, Dict[str, List[Any]]]]" = weakref.WeakKeyDictionary()


def _fingerprint(session: Any) -> Dict[str, Dict[str, List[Any]]]:
    fp = _FINGERPRINTS.get(session)
    if fp is None:
        fp = {}
        _FINGERPRINTS[session] = fp
    return fp


def _heat_value(count: int) -> int:
    """Map a per-chunk change count to a 0..254 heat magnitude.

    sqrt curve so the first few changes spread out perceptually; capped at
    ``_HEAT_REF`` changes → max. A stable (count 0) chunk that has content
    still renders (value 1 = dim presence) so the band is visible — only
    truly empty chunks map to 0 (absent/transparent).
    """
    if count <= 0:
        return 0
    t = math.sqrt(min(count, _HEAT_REF) / _HEAT_REF)
    return max(1, round(t * 254))


def _changed(new_hash: str, prev_hash: str) -> bool:
    """Did this chunk's content change between snapshots?

    A hash diff is a change; a chunk going present→absent or absent→present
    (one side empty, the other not) is also a change — the region's content
    materially moved in or out. Both-empty is no change.
    """
    if new_hash and prev_hash:
        return new_hash != prev_hash
    return bool(new_hash) != bool(prev_hash)


def _prop_index(j: int, new_n: int, old_n: int) -> int:
    """Index in a length-``old_n`` array for fractional position ``j`` of a
    length-``new_n`` array — used to correspond cells across a band resize,
    so "30% through the layer" still maps to "30% through" after re-chunking.
    """
    if old_n <= 0:
        return 0
    idx = int((j + 0.5) * old_n / new_n)
    return 0 if idx < 0 else (old_n - 1 if idx >= old_n else idx)


def _empty_state(cols: int, rows: int) -> Dict[str, Any]:
    grid: List[List[int]] = [[0] * cols for _ in range(rows)]
    return {
        "active": False,
        "cols": cols,
        "rows": rows,
        "layers": [],
        "grid": grid,
        "total_tokens": 0,
        "context_limit": 0,
        "fill_pct": 0.0,
        "updated_at": None,
    }


def _adjust_band_rows(
    band_rows: Dict[str, int], layer_tokens: Dict[str, int], rows: int
) -> None:
    """Fix up band row counts so they sum to exactly ``rows`` without
    dropping any layer below ``_MIN_BAND`` — every layer stays visible.

    Shrinks the largest shrinkable band when over-allocated, grows the
    largest token-bearing band (else the largest band) when under-allocated.
    """
    total = sum(band_rows.values())
    while total > rows:
        cand = [lid for lid in _LAYER_ORDER if band_rows[lid] > _MIN_BAND]
        if not cand:
            break
        big = max(cand, key=lambda lid: band_rows[lid])
        band_rows[big] -= 1
        total -= 1
    while total < rows:
        cand = [lid for lid in _LAYER_ORDER if layer_tokens[lid] > 0] or list(_LAYER_ORDER)
        big = max(cand, key=lambda lid: band_rows[lid])
        band_rows[big] += 1
        total += 1


def build_memory_snapshot(
    session: Any, cols: int = _DEFAULT_RES, rows: int = _DEFAULT_RES
) -> Dict[str, Any]:
    """Build a layer-banded heat-grid snapshot of the active context.

    Returns a JSON-serializable dict with:
      * ``layers`` — per-layer ``{id, name, tokens, max, fill_pct, hue,
        change_count, row_start, row_end}`` for the legend.
      * ``grid`` — ``rows`` × ``cols`` of ints. ``0`` = no content for
        that cell (the frontend renders it as a dim "empty space"); ``1..255``
        = present, magnitude encodes change frequency (1 = stable, 255 =
        churning).
      * ``total_tokens`` / ``context_limit`` / ``fill_pct`` for the header.

    The frontend derives each cell's color from the layer hue + the heat
    value + the active view mode (heatmap vs. solid layer color), so this
    single payload supports both render modes without a re-fetch.
    """
    cols = _clamp_res(cols, _DEFAULT_RES)
    rows = _clamp_res(rows, _DEFAULT_RES)

    if session is None:
        return _empty_state(cols, rows)

    try:
        token_layers = collect_context_layers(session)
    except Exception as exc:
        _logger.warning("memory_snapshot: collect_context_layers failed: %s", exc)
        return _empty_state(cols, rows)

    by_id = {entry["layer"]: entry for entry in token_layers}
    total_tokens = sum(int((by_id.get(lid, {}) or {}).get("current") or 0) for lid in _LAYER_ORDER)

    context_limit = 0
    try:
        context_limit = int(by_id.get("L5", {}).get("maximum") or 0)
    except Exception:
        context_limit = 0
    if context_limit <= 0:
        context_limit = sum(int((by_id.get(lid, {}) or {}).get("maximum") or 0) for lid in _LAYER_ORDER)

    fill_pct = round(100.0 * total_tokens / context_limit, 1) if context_limit > 0 else 0.0

    # --- band sizing: proportional to token share, but every layer gets at
    # least _MIN_BAND rows so empty layers still render ("empty space is
    # still space"). Bands tile the grid exactly (sum == rows).
    band_rows: Dict[str, int] = {}
    layer_tokens: Dict[str, int] = {}
    for lid in _LAYER_ORDER:
        layer_tokens[lid] = int((by_id.get(lid, {}) or {}).get("current") or 0)
        band_rows[lid] = 0

    if total_tokens > 0:
        for lid in _LAYER_ORDER:
            tok = layer_tokens[lid]
            if tok <= 0:
                band_rows[lid] = _MIN_BAND
            else:
                band_rows[lid] = max(_MIN_BAND, round(rows * tok / total_tokens))
        _adjust_band_rows(band_rows, layer_tokens, rows)
    else:
        # No content anywhere: split the grid evenly so all layers show as
        # visible (empty) bands rather than a blank canvas.
        base = rows // len(_LAYER_ORDER)
        rem = rows - base * len(_LAYER_ORDER)
        for i, lid in enumerate(_LAYER_ORDER):
            band_rows[lid] = base + (1 if i < rem else 0)

    # --- change tracking: one hash per GRID CELL in the band (r*cols
    # row-major slices — one chunk per displayed cell), so changes light up
    # at cell granularity and you can see *which regions* of a layer churn.
    # Keyed by (cols, rows) so each resolution keeps its own change history.
    fp = _fingerprint(session)
    res_fp = fp.setdefault((cols, rows), {})
    layer_heat: Dict[str, List[int]] = {}   # per-cell heat (len == r*cols)
    layer_change_count: Dict[str, int] = {}
    for lid in _LAYER_ORDER:
        r = band_rows.get(lid, 0)
        n = r * cols                          # one chunk per band cell (row-major)
        text = _layer_text(session, lid)
        chunks = _sample_chunks(text, n) if n > 0 else []
        hashes = [_chunk_hash(c) for c in chunks]
        present = [bool(c) for c in chunks]

        state = res_fp.get(lid)
        prev_hashes = (state or {}).get("hashes") or []
        prev_counts = (state or {}).get("counts") or []
        prev_r = (state or {}).get("band_rows")

        counts = [0] * n
        if state is not None and prev_hashes:
            if prev_r == r and len(prev_hashes) == n:
                # Band height unchanged → direct cell-by-cell correspondence;
                # carry each cell's accumulated count forward.
                for i in range(n):
                    pc = prev_counts[i] if i < len(prev_counts) else 0
                    counts[i] = pc + (1 if _changed(hashes[i], prev_hashes[i]) else 0)
            else:
                # Band resized → re-chunked. Correspond by fractional position
                # so we still detect *which regions* shifted (with some boundary
                # noise from the moved slice edges on this one snapshot).
                # Counts start fresh for the new layout.
                old_n = len(prev_hashes)
                for i in range(n):
                    if _changed(hashes[i], prev_hashes[_prop_index(i, n, old_n)]):
                        counts[i] = 1
        # else: first snapshot for this (resolution, layer) → counts stay 0.

        res_fp[lid] = {"band_rows": r, "hashes": hashes, "counts": counts}
        # 0 = empty cell (no text in that slice); 1..255 = present, where the
        # magnitude encodes change frequency (1 = stable since first seen,
        # 255 = churning).
        layer_heat[lid] = [
            (0 if not present[i] else 1 + _heat_value(counts[i]))
            for i in range(n)
        ]
        layer_change_count[lid] = sum(counts)

    # --- build the grid: each band cell carries its own per-cell heat
    # (row-major within the band), so changes show up as spatial regions,
    # not uniform stripes. Empty cells stay 0 so the frontend renders them
    # as dim "empty space" rather than transparent — the band's full extent
    # is always visible.
    grid: List[List[int]] = [[0] * cols for _ in range(rows)]
    layers_out: List[Dict[str, Any]] = []
    row_cursor = 0
    for lid in _LAYER_ORDER:
        meta = by_id.get(lid, {}) or {}
        tokens = int(meta.get("current") or 0)
        maximum = int(meta.get("maximum") or 0)
        r = band_rows.get(lid, 0)
        row_start = row_cursor
        row_end = min(row_cursor + r, rows)
        heat = layer_heat.get(lid) or []
        for ri in range(row_start, row_end):
            row = grid[ri]
            base = (ri - row_start) * cols
            for ci in range(cols):
                idx = base + ci
                row[ci] = heat[idx] if idx < len(heat) else 0
        row_cursor = row_end

        layers_out.append(
            {
                "id": lid,
                "name": meta.get("name", lid),
                "tokens": tokens,
                "max": maximum,
                "fill_pct": round(100.0 * tokens / maximum, 1) if maximum > 0 else 0.0,
                "hue": LAYER_HUES.get(lid, 0),
                "change_count": layer_change_count.get(lid, 0),
                "row_start": row_start,
                "row_end": row_end,
            }
        )

    return {
        "active": True,
        "cols": cols,
        "rows": rows,
        "layers": layers_out,
        "grid": grid,
        "total_tokens": total_tokens,
        "context_limit": context_limit,
        "fill_pct": fill_pct,
        "updated_at": None,
    }


__all__ = ["build_memory_snapshot", "LIVE_RESOLUTION", "LAYER_HUES"]
# Constant the hook/router import for the live push resolution.
LIVE_RESOLUTION = _LIVE_RES