"""Trace Analyzer router — list/read raw trace runs.

Globs ``$MUCLI_HOME/trace/*.jsonl`` (mirrors the sessions router's
``_session_dirs`` glob) and serves parsed runs + derived series + the
canvas-ready snapshot, plus a streaming raw endpoint for export. All heavy
work is delegated to ``mu/trace/parser.py`` / ``snapshot.py`` so the GUI and
the ``mucli trace`` CLI share one code path.
"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from mu.trace import (
    build_series,
    build_session_view,
    build_summary,
    build_trace_snapshot,
    find_trace_path,
    list_trace_runs,
    load_session_runs,
    parse_trace,
)

router = APIRouter()


def _find_trace(run_id: str) -> str:
    """Resolve a run_id to its file path, or 404. Thin HTTP wrapper over
    :func:`mu.trace.find_trace_path`."""
    path = find_trace_path(run_id)
    if path is None:
        raise HTTPException(status_code=404, detail=f"trace run not found: {run_id}")
    return path


@router.get("")
async def list_traces(session: str | None = None) -> List[Dict[str, Any]]:
    """List trace runs (newest first) with header metadata + iter count.

    Optional ``?session=`` narrows the list to one session's runs — the Trace
    Analyzer is session-scoped, so when opened from the chat it passes the
    current session and ignores every other session's runs.
    """
    runs = list_trace_runs()
    if session:
        runs = [r for r in runs if r.get("session") == session]
    return runs


@router.get("/session/{session_name}")
async def get_session_trace(session_name: str, cols: int = 128) -> Dict[str, Any]:
    """Combined multi-run view for one session — every run in the session,
    chronologically, merged into one series/summary/snapshot with per-run
    bounds. The Trace Analyzer loads this (not a single run) when opened from
    the chat, so everything that happened in the session is visible at once.
    """
    runs = load_session_runs(session_name)
    if not runs:
        raise HTTPException(
            status_code=404, detail=f"no trace runs for session: {session_name}"
        )
    return build_session_view(runs, cols=cols)


@router.get("/{run_id}")
async def get_trace(run_id: str, cols: int = 128) -> Dict[str, Any]:
    """Full parsed run + derived series + snapshot + overview summary."""
    path = _find_trace(run_id)
    run = parse_trace(path)
    series = build_series(run)
    summary = build_summary(run, series)
    snapshot = build_trace_snapshot(run, cols=cols)
    return {
        "run_id": run.run_id,
        "header": run.header,
        "iters": run.iters,
        "tools": run.tools,
        "nudges": run.nudges,
        "compactions": run.compactions,
        "turn_end": run.turn_end,
        "series": series,
        "snapshot": snapshot,
        "summary": summary,
        "path": path,
    }


@router.get("/{run_id}/raw")
async def get_trace_raw(run_id: str):
    """Stream the raw JSONL (for large runs / export)."""
    path = _find_trace(run_id)

    def gen():
        with open(path, encoding="utf-8") as fh:
            while True:
                chunk = fh.read(65536)
                if not chunk:
                    break
                yield chunk

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@router.get("/{run_id}/summary")
async def get_trace_summary(run_id: str) -> Dict[str, Any]:
    """Just the overview cards — cheap for the run picker's hover detail."""
    path = _find_trace(run_id)
    run = parse_trace(path)
    series = build_series(run)
    return build_summary(run, series)