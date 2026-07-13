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

from ..memory_snapshot import build_memory_snapshot

router = APIRouter()


@router.get("/state")
async def get_memory_state(
    request: Request, cols: int = 128, rows: int = 128
) -> Dict[str, Any]:
    """Current context-window snapshot for the focused session.

    ``cols``/``rows`` are clamped to [16, 256] inside the builder. Returns
    a grid of ``#rrggbb`` colors (or ``null`` for empty cells) plus a
    per-layer legend the panel renders alongside the canvas.
    """
    session = request.app.state.session_by_name()
    return build_memory_snapshot(session, cols=cols, rows=rows)