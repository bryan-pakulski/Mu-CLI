"""Trace Analyzer router — list/read raw trace runs.

Globs ``$MUCLI_HOME/trace/*.jsonl`` (mirrors the sessions router's
``_session_dirs`` glob) and serves parsed runs + derived series + the
canvas-ready snapshot, plus a streaming raw endpoint for export. All heavy
work is delegated to ``mu/trace/parser.py`` / ``snapshot.py`` so the GUI and
the ``mucli trace`` CLI share one code path.
"""

from __future__ import annotations

import asyncio
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


# The trace parser + snapshot builder are CPU-bound and can take seconds on a
# huge run (a 1.1M-token trace has been seen in the wild). These handlers are
# ``async def``, so running that work inline would block the single event loop
# for the whole parse — freezing SSE, /api/sessions, and prompt-answer routes
# (the "GUI freezes, can't load traces / navigate" symptom). Every heavy call
# is offloaded to the default thread executor via ``asyncio.to_thread`` so the
# loop stays free to serve everything else while a big trace parses on a
# worker thread. The ``/raw`` streaming endpoint already runs its sync
# generator in a threadpool (Starlette), so it needs no change.


@router.get("")
async def list_traces(session: str | None = None) -> List[Dict[str, Any]]:
    """List trace runs (newest first) with header metadata + iter count.

    Optional ``?session=`` narrows the list to one session's runs — the Trace
    Analyzer is session-scoped, so when opened from the chat it passes the
    current session and ignores every other session's runs.
    """
    runs = await asyncio.to_thread(list_trace_runs)
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
    runs = await asyncio.to_thread(load_session_runs, session_name)
    if not runs:
        raise HTTPException(
            status_code=404, detail=f"no trace runs for session: {session_name}"
        )
    return await asyncio.to_thread(build_session_view, runs, cols)


@router.get("/{run_id}")
async def get_trace(run_id: str, cols: int = 128) -> Dict[str, Any]:
    """Full parsed run + derived series + snapshot + overview summary."""
    path = _find_trace(run_id)
    run = await asyncio.to_thread(parse_trace, path)
    series = await asyncio.to_thread(build_series, run)
    snapshot = await asyncio.to_thread(build_trace_snapshot, run, cols)
    summary = await asyncio.to_thread(build_summary, run, series)
    return {
        "run_id": run.run_id,
        "header": run.header,
        "iters": run.iters,
        "tools": run.tools,
        "nudges": run.nudges,
        "compactions": run.compactions,
        "requests": run.requests,
        "context_artifacts": run.context_artifacts,
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
    run = await asyncio.to_thread(parse_trace, path)
    series = await asyncio.to_thread(build_series, run)
    return await asyncio.to_thread(build_summary, run, series)
