"""planos-style job API (guide §3, Phase 3).

``GET /api/jobs/vm/*`` view models, ``POST /api/jobs/cmd`` single mutation
entry point, ``GET /api/jobs/events`` coalesced change stream (SSE).

Import rule (§3): this module may import only ``core.vm``, ``core.commands``,
``core.changes`` and ``core.actions`` from the job core. A guard test enforces it.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from mu.jobs.core import vm
from mu.jobs.core.changes import default_bus
from mu.jobs.core.commands import CommandError, CommandRejected, dispatch

router = APIRouter()


def _service(request: Request):
    service = getattr(request.app.state, "job_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="job service is unavailable")
    return service


def _bus(request: Request):
    return getattr(request.app.state, "job_change_bus", None) or default_bus()


@router.get("/vm/home")
async def home(request: Request, limit: int = Query(200, ge=1, le=1000)):
    return vm.home_vm(_service(request), limit=limit)


@router.get("/vm/jobs")
async def jobs(request: Request, limit: int = Query(200, ge=1, le=1000)):
    return vm.jobs_vm(_service(request), limit=limit)


@router.get("/vm/job/{job_id}")
async def job(job_id: str, request: Request, parts: Optional[str] = None):
    wanted = [p for p in (parts or "").split(",") if p.strip()] or None
    try:
        return vm.job_vm(_service(request), job_id, parts=wanted)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found") from exc


@router.post("/cmd")
async def command(request: Request, payload: Dict[str, Any]):
    service = _service(request)
    try:
        result = dispatch(service, payload, bus=_bus(request))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Job not found: {exc}") from exc
    except CommandRejected as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CommandError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        # Plan conflicts / validation / legacy state errors surface as 409.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return result.to_dict()


@router.get("/events")
async def events(request: Request, after: int = Query(0, ge=0), once: bool = False):
    """SSE: ``data: {scope, id, parts, seq}``. Clients refetch only ``parts``."""
    bus = _bus(request)

    async def gen():
        seq = after or bus.seq
        yield f"event: hello\ndata: {json.dumps({'seq': seq})}\n\n"
        while True:
            if await request.is_disconnected():
                return
            changes = await asyncio.to_thread(bus.wait, seq, 15.0)
            if not changes:
                yield ": keepalive\n\n"
                if once:
                    return
                continue
            for change in changes:
                seq = max(seq, change.seq)
                yield f"data: {json.dumps(change.to_dict())}\n\n"
            if once:
                return

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
