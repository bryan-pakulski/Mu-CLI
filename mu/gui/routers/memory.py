"""Context Observatory — current fingerprint and provider-call timeline.

The current-state endpoints expose the assembled prompt layers and detailed
slice grid. The timeline endpoint exposes content-free measurements captured
only at real provider calls. All routes use the same explicit session
dependency as web and mobile mutations.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, Query

from ..deps import require_session
from ..memory_snapshot import (
    LAYER_HUES,
    _LAYER_ORDER,
    _layer_text,
    _sample_chunks,
    build_memory_snapshot,
    get_context_timeline,
)

router = APIRouter()


@router.get("/state")
async def get_memory_state(
    cols: int = 128,
    rows: int = 128,
    session: Any = Depends(require_session),
) -> Dict[str, Any]:
    """Current context-window snapshot for the focused session.

    ``cols``/``rows`` are clamped to [16, 256] inside the builder. Returns
    an int ``grid`` (``0`` = empty cell — the frontend renders it as dim
    "empty space"; ``1..255`` = present where the magnitude encodes change
    frequency — 1 = stable since first seen, 255 = churning) plus a
    per-layer legend (``hue`` + ``change_count``) the panel renders as a
    hue-per-layer, per-cell change-frequency heatmap alongside the canvas.
    """
    return build_memory_snapshot(session, cols=cols, rows=rows)


@router.get("/timeline")
async def get_memory_timeline(
    limit: int = Query(default=240, ge=1, le=360),
    session: Any = Depends(require_session),
) -> Dict[str, Any]:
    """Provider-call history for the session's Context Observatory.

    Points contain layer token counts, deltas and fixed-slice churn metrics;
    they deliberately contain no raw prompt or conversation content.
    """
    return get_context_timeline(session, limit=limit)


@router.get("/content")
async def get_memory_layer_content(
    layer: str = "",
    session: Any = Depends(require_session),
) -> Dict[str, Any]:
    """The actual text body the harness injects for one context layer.

    Clicking a layer in the Context Observatory legend opens a modal with this
    content so you can inspect exactly what the model sees at that layer
    (the assembled system prompt for L0, the rendered conversation for L5,
    workspace files for L1, etc.). Returns plain text — the frontend shows
    it as selectable, copyable preformatted text.
    """
    from mu.commands.memory import _layer_content
    from utils.runtime_metrics import collect_context_layers

    lid = (layer or "").strip().upper()
    if lid not in _LAYER_ORDER:
        return {
            "layer": lid,
            "name": lid,
            "hue": 0,
            "content": "",
            "tokens": 0,
            "chars": 0,
            "error": "unknown layer",
        }

    # Name + token count come from the same layer stats the snapshot uses,
    # so the modal header matches the legend exactly.
    try:
        token_layers = collect_context_layers(session) if session is not None else []
    except Exception:
        token_layers = []
    by_id = {e["layer"]: e for e in token_layers}
    meta = by_id.get(lid) or {}
    name = meta.get("name", lid)
    tokens = int(meta.get("current") or 0)

    try:
        content = _layer_content(session, lid) if session is not None else ""
    except Exception as exc:  # never let a layer-builder bug break the modal
        return {
            "layer": lid,
            "name": name,
            "hue": LAYER_HUES.get(lid, 0),
            "content": "",
            "tokens": tokens,
            "chars": 0,
            "error": f"could not read layer: {exc}",
        }

    return {
        "layer": lid,
        "name": name,
        "hue": LAYER_HUES.get(lid, 0),
        "content": content or "",
        "tokens": tokens,
        "chars": len(content or ""),
        "error": "",
    }


@router.get("/cell")
async def get_memory_cell(
    layer: str = "",
    cols: int = 128,
    rows: int = 128,
    row: int = -1,
    col: int = -1,
    session: Any = Depends(require_session),
) -> Dict[str, Any]:
    """Return the metadata and exact text slice represented by one grid cell."""
    lid = (layer or "").strip().upper()
    snapshot = build_memory_snapshot(session, cols=cols, rows=rows)
    region = next(
        (item for item in snapshot.get("regions", []) if item["id"] == lid), None
    )
    if (
        region is None
        or not (region["row_start"] <= row < region["row_end"])
        or not (0 <= col < snapshot["cols"])
    ):
        return {"error": "cell is outside the current memory map", "content": ""}
    if lid == "FREE":
        return {"error": "", "content": "", "free": True, "tokens": 0, "chars": 0}

    cell_count = (region["row_end"] - region["row_start"]) * snapshot["cols"]
    cell_index = (row - region["row_start"]) * snapshot["cols"] + col
    text = _layer_text(session, lid)
    content = _sample_chunks(text, cell_count)[cell_index] if cell_count else ""
    return {
        "error": "",
        "content": content,
        "chars": len(content),
        "tokens": int(region.get("tokens", 0) or 0),
        "cell_index": cell_index,
        "cell_count": cell_count,
    }
