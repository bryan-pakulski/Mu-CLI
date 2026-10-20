"""planos-style job API (guide §3, Phase 3).

``GET /api/jobs/vm/*`` view models, ``POST /api/jobs/cmd`` single mutation
entry point, ``GET /api/jobs/events`` coalesced change stream (SSE).

Import rule (§3): this module may import only ``core.vm``, ``core.commands``,
``core.changes`` and ``core.actions`` from the job core. A guard test enforces it.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from mu.jobs.core import vm
from mu.jobs.core.changes import collect_durable_changes, default_bus
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
def home(request: Request, limit: int = Query(200, ge=1, le=1000)):
    return vm.home_vm(_service(request), limit=limit)


@router.get("/vm/jobs")
def jobs(request: Request, limit: int = Query(200, ge=1, le=1000)):
    return vm.jobs_vm(_service(request), limit=limit)


@router.get("/vm/job/{job_id}")
def job(job_id: str, request: Request, parts: Optional[str] = None, plan_version: Optional[int] = None):
    wanted = [p for p in (parts or "").split(",") if p.strip()] or None
    try:
        return vm.job_vm(_service(request), job_id, parts=wanted, plan_version=plan_version)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found") from exc


@router.get("/vm/job/{job_id}/push-preview")
def push_preview(job_id: str, request: Request):
    try:
        return vm.push_preview_vm(_service(request), job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found") from exc


@router.get("/vm/environments")
def environments(request: Request, repository: str = "", session: str = "", images: bool = True):
    return vm.environments_vm(_service(request), repository=repository, session=session, include_images=images)


@router.get("/vm/job/{job_id}/environments")
def job_environments(job_id: str, request: Request):
    try:
        return vm.job_environments_vm(_service(request), job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found") from exc


@router.get("/vm/activity")
def activity(request: Request):
    return vm.activity_vm(_service(request))


@router.get("/vm/activity/logs/{job_id}")
def activity_logs(job_id: str, request: Request, tail_bytes: int = Query(65536, ge=1024, le=512 * 1024)):
    try:
        return vm.activity_logs_vm(_service(request), job_id, tail_bytes=tail_bytes)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found") from exc


@router.get("/vm/status")
def status(request: Request):
    return vm.activity_status_vm(_service(request))


@router.post("/activity/containers/{name}/remove")
def remove_container(name: str, request: Request):
    try:
        return vm.remove_container(_service(request), name)
    except CommandRejected as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"container {name!r} not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/cmd")
def command(request: Request, payload: Dict[str, Any]):
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
        service = _service(request)
        cursor = await asyncio.to_thread(service.store.event_cursor)
        seq = min(after or bus.seq, bus.seq)
        yield f"event: hello\ndata: {json.dumps({'seq': seq})}\n\n"
        heartbeat = time.monotonic()
        from mu.gui.devices import still_paired
        while True:
            if await request.is_disconnected() or not still_paired(request):
                return
            cursor = await asyncio.to_thread(collect_durable_changes, service, bus, cursor)
            changes = await asyncio.to_thread(bus.wait, seq, .25)
            if not changes:
                if time.monotonic() - heartbeat >= 15:
                    yield ": keepalive\n\n"
                    heartbeat = time.monotonic()
                if once:
                    return
                continue
            for change in changes:
                seq = max(seq, change.seq)
                yield f"id: {seq}\ndata: {json.dumps(change.to_dict())}\n\n"
            if once:
                return

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
