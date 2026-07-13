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
* **Change frequency (hash-based)** — each layer's text is split into a
  fixed number of *canonical* chunks and SHA-256 hashed. The fingerprint
  remembers each chunk's last hash + a change counter per chunk, keyed by
  the session object. Every snapshot compares the current chunk hashes to
  the stored ones; a chunk whose hash changed increments its counter.
  Cell brightness encodes that counter (sqrt curve, capped), so the parts
  of a layer that change every iteration *glow* and the parts that have
  been stable since first contact stay *dim*. That is the whole point of
  the panel: watching which areas of each layer's memory churn in real
  time, not just that the layer grew.

The grid is resolution-independent: change tracking always runs at the
canonical chunk count, so changing the display resolution does not reset
or distort the heat. Identical content between two snapshots yields an
identical grid (deterministic); only changed chunks brighten.

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

# Canonical chunk count per layer for change tracking. Fixed regardless of
# display resolution so heat is stable and comparable across res changes.
_CANON_CHUNKS = 128

# Change-count at which a cell reaches max brightness (sqrt curve below).
# ~8 observed changes → fully hot; stable content stays dim.
_HEAT_REF = 8.0


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
# Structure: { session(obj): { <layer>: { "hashes": [...], "counts": [...] } } }
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


def build_memory_snapshot(
    session: Any, cols: int = _DEFAULT_RES, rows: int = _DEFAULT_RES
) -> Dict[str, Any]:
    """Build a layer-banded heat-grid snapshot of the active context.

    Returns a JSON-serializable dict with:
      * ``layers`` — per-layer ``{id, name, tokens, max, fill_pct, hue,
        change_count, row_start, row_end}`` for the legend.
      * ``grid`` — ``rows`` × ``cols`` of ints. ``0`` = no content for
        that cell (transparent); ``1..255`` = present, magnitude encodes
        change frequency (1 = stable, 255 = churning).
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

    # --- band sizing: proportional to token share ---
    band_rows: Dict[str, int] = {}
    layer_tokens: Dict[str, int] = {}
    for lid in _LAYER_ORDER:
        tok = int((by_id.get(lid, {}) or {}).get("current") or 0)
        layer_tokens[lid] = tok
        band_rows[lid] = 0

    if total_tokens > 0:
        for lid in _LAYER_ORDER:
            tok = layer_tokens[lid]
            if tok <= 0:
                continue
            r = round(rows * tok / total_tokens)
            band_rows[lid] = max(1, r)

        allocated = sum(band_rows.values())
        while allocated > rows:
            candidates = [lid for lid in _LAYER_ORDER if band_rows[lid] > 1]
            if not candidates:
                break
            big = max(candidates, key=lambda lid: band_rows[lid])
            band_rows[big] -= 1
            allocated -= 1
        if allocated < rows:
            nonzero = [lid for lid in _LAYER_ORDER if band_rows[lid] > 0]
            if nonzero:
                big = max(nonzero, key=lambda lid: layer_tokens[lid])
                band_rows[big] += rows - allocated

    # --- change tracking: canonical chunks per layer ---
    fp = _fingerprint(session)
    layer_heat: Dict[str, List[int]] = {}   # per canonical chunk heat value
    layer_change_count: Dict[str, int] = {}
    for lid in _LAYER_ORDER:
        text = _layer_text(session, lid)
        chunks = _sample_chunks(text, _CANON_CHUNKS)
        hashes = [_chunk_hash(c) for c in chunks]
        present = [1 if c else 0 for c in chunks]

        state = fp.get(lid)
        if state is None:
            counts = [0] * _CANON_CHUNKS
        else:
            prev = state.get("hashes") or []
            counts = list(state.get("counts") or [0] * _CANON_CHUNKS)
            # Pad/truncate to the canonical length defensively.
            if len(counts) < _CANON_CHUNKS:
                counts += [0] * (_CANON_CHUNKS - len(counts))
            counts = counts[:_CANON_CHUNKS]
            for i in range(_CANON_CHUNKS):
                ph = prev[i] if i < len(prev) else ""
                if hashes[i] and ph and hashes[i] != ph:
                    counts[i] += 1
                # If the chunk went from present→absent or vice versa, treat
                # it as a change too (the area's content materially moved).
                if (bool(hashes[i]) != bool(ph)) and (hashes[i] or ph):
                    counts[i] += 1

        fp[lid] = {"hashes": hashes, "counts": counts}
        # Cell value: 0 if the canonical chunk is empty, else 1 + heat.
        layer_heat[lid] = [
            (0 if present[i] == 0 else 1 + _heat_value(counts[i]))
            for i in range(_CANON_CHUNKS)
        ]
        layer_change_count[lid] = sum(counts)

    # --- build the grid row by row, mapping display cells → canonical chunks ---
    grid: List[List[int]] = [[0] * cols for _ in range(rows)]
    layers_out: List[Dict[str, Any]] = []
    row_cursor = 0
    for lid in _LAYER_ORDER:
        meta = by_id.get(lid, {}) or {}
        tokens = int(meta.get("current") or 0)
        maximum = int(meta.get("maximum") or 0)
        r = band_rows.get(lid, 0)
        row_start = row_cursor
        row_end = row_cursor + r

        if r > 0 and row_end <= rows:
            heat = layer_heat.get(lid) or [0] * _CANON_CHUNKS
            total_cells = r * cols
            for ri in range(r):
                row = grid[row_start + ri]
                for ci in range(cols):
                    display_lin = ri * cols + ci
                    # Map this display cell to the nearest canonical chunk.
                    canon_idx = (display_lin * _CANON_CHUNKS) // max(1, total_cells)
                    if canon_idx >= _CANON_CHUNKS:
                        canon_idx = _CANON_CHUNKS - 1
                    row[ci] = heat[canon_idx]

        row_end = min(row_end, rows)
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