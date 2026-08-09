"""Read-only durable work-board API shared by GUI and mobile."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from mu.jobs.board import build_job_board
from mu.jobs.service import JobService


router = APIRouter()


def _service(request: Request) -> JobService:
    service = getattr(request.app.state, "job_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="job service is unavailable")
    return service


@router.get("/board")
async def job_board(request: Request):
    return build_job_board(_service(request)).to_dict()
