"""Context-window fingerprint for the GUI "Memory Map" panel.

``build_memory_snapshot`` turns the layered system prompt the harness
assembles each turn into a 2-D grid of colors: one horizontal band per
layer (L0..L5), band height proportional to the layer's token share, and
within a band each cell's color is derived from the SHA-256 hash of a slice
of that layer's text. Identical content → identical colors, so unchanged
layers stay visually stable across turns and the moment a layer's body
changes (a new tool result lands in L5, a summary gets written into L2,
…) the band visibly shifts. That is the whole point of the panel: watching
the context evolve in real time.

The same builder is used by the REST endpoint (``/api/memory/state``) and
by the ``pre_provider_call`` hook that pushes a live snapshot per
iteration, so the layout is identical between the live and final frames.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List, Optional, Tuple

from utils.runtime_metrics import collect_context_layers

_logger = logging.getLogger(__name__)

# Canonical layer order — matches the assembly in
# mu/session/context.py:inject_hierarchical_context.
_LAYER_ORDER: Tuple[str, ...] = ("L0", "L1", "L1B", "L2", "L3", "L4B", "L5")

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
    """Deterministic chunk → ``#rrggbb``.

    SHA-256 of the UTF-8 bytes, take the first three bytes as RGB, then
    lift each channel into [40, 255] so colors are never near-invisible
    on a dark theme. The mapping is purely a function of the chunk text,
    so the same content always renders the same color.
    """
    digest = hashlib.sha256(chunk.encode("utf-8", "replace")).digest()
    r, g, b = digest[0], digest[1], digest[2]

    def lift(byte: int) -> int:
        # Map 0..255 into 40..255 so even low-byte channels stay legible.
        return 40 + (byte * 215 // 255)

    return f"#{lift(r):02x}{lift(g):02x}{lift(b):02x}"


def _sample_chunks(text: str, n: int) -> List[str]:
    """Split ``text`` into ``n`` evenly-spaced slices.

    Even sampling (rather than a prefix split) means a band represents
    its whole layer, not just its opening words — important for L5, where
    a long conversation's recent tail is usually what changed. Empty
    slices (when ``n`` exceeds the text length) yield ``""``, which the
    caller maps to a null/transparent cell so a tiny layer honestly
    renders as a few colored cells rather than a solid block of the
    empty-string hash color.
    """
    if n <= 0:
        return []
    if not text:
        return ["" for _ in range(n)]
    t = text
    tlen = len(t)
    out: List[str] = []
    for i in range(n):
        start = i * tlen // n
        end = (i + 1) * tlen // n
        out.append(t[start:end])
    return out


def _layer_text(session: Any, layer_id: str) -> str:
    """Best-effort text body for one layer.

    Imports ``_layer_content`` lazily so importing this module never
    pulls the commands package (and its CLI deps). Swallows builder
    failures — a layer that can't be materialized just contributes an
    empty band rather than breaking the snapshot.
    """
    try:
        from mu.commands.memory import _layer_content

        return str(_layer_content(session, layer_id) or "")
    except Exception as exc:  # defensive — runs on the FastAPI thread too
        _logger.debug("memory_snapshot: layer %s read failed: %s", layer_id, exc)
        return ""


def _empty_state(cols: int, rows: int) -> Dict[str, Any]:
    grid: List[List[Optional[str]]] = [[None] * cols for _ in range(rows)]
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
    """Build a layer-banded color-grid snapshot of the active context.

    Returns a JSON-serializable dict with:
      * ``layers`` — per-layer ``{id, name, tokens, max, fill_pct,
        row_start, row_end}`` for the legend.
      * ``grid`` — ``rows`` × ``cols`` of ``#rrggbb`` strings or ``None``
        (``None`` = no content for that cell → panel background).
      * ``total_tokens`` / ``context_limit`` / ``fill_pct`` for the header.
    """
    cols = _clamp_res(cols, _DEFAULT_RES)
    rows = _clamp_res(rows, _DEFAULT_RES)

    if session is None:
        return _empty_state(cols, rows)

    # Per-layer token breakdown. Each entry: {layer, name, current, maximum,
    # description}. ``current`` is in tokens, mirroring the real prompt cost.
    try:
        token_layers = collect_context_layers(session)
    except Exception as exc:
        _logger.warning("memory_snapshot: collect_context_layers failed: %s", exc)
        return _empty_state(cols, rows)

    by_id = {entry["layer"]: entry for entry in token_layers}
    total_tokens = sum(int((by_id.get(lid, {}) or {}).get("current") or 0) for lid in _LAYER_ORDER)

    # Context limit = the global token cap (the L5 layer's ``maximum`` is
    # the configured ``context_token_limit``). Matches what the splash
    # banner and /memory "Total" row report.
    context_limit = 0
    try:
        context_limit = int(by_id.get("L5", {}).get("maximum") or 0)
    except Exception:
        context_limit = 0
    if context_limit <= 0:
        # Fall back to summing per-layer caps — still a usable denominator.
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
            # Any layer with real content gets at least one row so it stays
            # visible (e.g. a tiny L5 history beside a large L0 system
            # prompt). Rounding can then over-allocate; fix that below.
            r = round(rows * tok / total_tokens)
            band_rows[lid] = max(1, r)

        allocated = sum(band_rows.values())
        # Over-allocation (from the min-1-row rule + rounding): trim from
        # the largest bands — never the small ones — so a tiny but nonzero
        # layer keeps its visible row. Trimming the largest band is also
        # visually the least disruptive (±1 row on a big band is invisible).
        while allocated > rows:
            candidates = [lid for lid in _LAYER_ORDER if band_rows[lid] > 1]
            if not candidates:
                break
            big = max(candidates, key=lambda lid: band_rows[lid])
            band_rows[big] -= 1
            allocated -= 1
        # Under-allocation (rounding left rows on the table): give the
        # remainder to the largest-token layer.
        if allocated < rows:
            nonzero = [lid for lid in _LAYER_ORDER if band_rows[lid] > 0]
            if nonzero:
                big = max(nonzero, key=lambda lid: layer_tokens[lid])
                band_rows[big] += rows - allocated

    # --- build the grid row by row ---
    grid: List[List[Optional[str]]] = [[None] * cols for _ in range(rows)]
    layers_out: List[Dict[str, Any]] = []
    row_cursor = 0
    for lid in _LAYER_ORDER:
        meta = by_id.get(lid, {}) or {}
        tokens = int(meta.get("current") or 0)
        maximum = int(meta.get("maximum") or 0)
        r = band_rows.get(lid, 0)
        row_start = row_cursor
        row_end = row_cursor + r  # exclusive

        if r > 0 and row_end <= rows:
            text = _layer_text(session, lid)
            chunks = _sample_chunks(text, r * cols)
            for ri in range(r):
                row = grid[row_start + ri]
                for ci in range(cols):
                    idx = ri * cols + ci
                    chunk = chunks[idx] if idx < len(chunks) else ""
                    if chunk:
                        row[ci] = _hash_color(chunk)
        # Clamp to the grid in case drift pushed L5 past `rows`.
        row_end = min(row_end, rows)
        row_cursor = row_end

        layers_out.append(
            {
                "id": lid,
                "name": meta.get("name", lid),
                "tokens": tokens,
                "max": maximum,
                "fill_pct": round(100.0 * tokens / maximum, 1) if maximum > 0 else 0.0,
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


__all__ = ["build_memory_snapshot", "LIVE_RESOLUTION"]
# Constant the hook/router import for the live push resolution.
LIVE_RESOLUTION = _LIVE_RES