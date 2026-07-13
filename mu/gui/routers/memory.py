"""Memory Map panel — live context-window fingerprint.

Surfaces ``build_memory_snapshot`` over the active session as JSON so the
GUI "Memory Map" view can render a layer-banded color grid and refresh it
each turn (and per iteration, via the ``context_snapshot`` SSE event the
``pre_provider_call`` hook publishes). Mirrors the shape of the other
mode-panel routers (e.g. ``routers/debug.py``).
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Request

from ..memory_snapshot import LAYER_HUES, _LAYER_ORDER, build_memory_snapshot

router = APIRouter()


@router.get("/state")
async def get_memory_state(
    request: Request, cols: int = 128, rows: int = 128
) -> Dict[str, Any]:
    """Current context-window snapshot for the focused session.

    ``cols``/``rows`` are clamped to [16, 256] inside the builder. Returns
    an int ``grid`` (``0`` = empty cell — the frontend renders it as dim
    "empty space"; ``1..255`` = present where the magnitude encodes change
    frequency — 1 = stable since first seen, 255 = churning) plus a
    per-layer legend (``hue`` + ``change_count``) the panel renders as a
    hue-per-layer, per-cell change-frequency heatmap alongside the canvas.
    """
    session = request.app.state.session_by_name()
    return build_memory_snapshot(session, cols=cols, rows=rows)


@router.get("/content")
async def get_memory_layer_content(
    request: Request, layer: str = ""
) -> Dict[str, Any]:
    """The actual text body the harness injects for one context layer.

    Clicking a layer in the Memory Map legend opens a modal with this
    content so you can inspect exactly what the model sees at that layer
    (the assembled system prompt for L0, the rendered conversation for L5,
    workspace files for L1, etc.). Returns plain text — the frontend shows
    it as selectable, copyable preformatted text.
    """
    from mu.commands.memory import _layer_content
    from utils.runtime_metrics import collect_context_layers

    lid = (layer or "").strip().upper()
    session = request.app.state.session_by_name()

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
    meta = (by_id.get(lid) or {})
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