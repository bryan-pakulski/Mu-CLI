"""Retrospective engineering-job analysis endpoints.

This router is mounted beneath the existing /api/jobs prefix by
mu.gui.routers.__init__, keeping analysis calculations control-plane neutral.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from mu.jobs.performance import build_job_performance


router = APIRouter()


def _service(request: Request):
    service = getattr(request.app.state, "job_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="job service is unavailable")
    return service


@router.get("/{job_id}/analysis")
async def get_job_analysis(
    job_id: str,
    request: Request,
    timeline_limit: int = Query(default=5000, ge=100, le=20000),
):
    try:
        return {
            "analysis": build_job_performance(
                _service(request),
                job_id,
                timeline_limit=timeline_limit,
            )
        }
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found") from exc
