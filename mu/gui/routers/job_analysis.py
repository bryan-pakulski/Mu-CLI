"""Retrospective engineering-job analysis endpoints.

This router is mounted beneath the existing /api/jobs prefix by
mu.gui.routers.__init__, keeping analysis calculations control-plane neutral.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from mu.jobs.analysis import build_job_analysis, compare_job_analyses
from mu.jobs.management import JobManagementService


router = APIRouter()


def _service(request: Request):
    service = getattr(request.app.state, "job_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="job service is unavailable")
    return service


def _decorate_analysis(service, analysis):
    """Attach management truth and normalize first-pass verification semantics.

    Archival deliberately lives outside the execution state machine, so it must
    come from JobManagementService rather than job.metadata. Verification rows
    are emitted chronologically by the analysis core; the first row therefore
    represents the job's original deterministic verification attempt even when
    later reviewer-requested changes produce additional verification runs.
    """
    job = analysis.get("job") or {}
    job_id = str(job.get("id") or "")
    if job_id:
        management = JobManagementService(service).state(job_id)
        job["archived"] = bool(management.get("archived"))
        job["archived_at"] = management.get("archived_at")
        job["archived_reason"] = management.get("archived_reason") or ""
    verifications = analysis.get("verifications") or []
    summary = analysis.get("summary") or {}
    summary["first_pass_verification"] = (
        bool(verifications[0].get("passed")) if verifications else None
    )
    return analysis


@router.get("/analysis/compare")
async def compare_jobs(
    request: Request,
    job_id: str = Query(..., min_length=1),
    compare_id: str = Query(..., min_length=1),
):
    service = _service(request)
    try:
        primary = _decorate_analysis(
            service,
            build_job_analysis(service, job_id, timeline_limit=1000),
        )
        comparison = _decorate_analysis(
            service,
            build_job_analysis(service, compare_id, timeline_limit=1000),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Job '{exc.args[0]}' not found") from exc
    return {
        "comparison": compare_job_analyses(primary, comparison),
        "primary": primary,
        "reference": comparison,
    }


@router.get("/{job_id}/analysis")
async def get_job_analysis(
    job_id: str,
    request: Request,
    timeline_limit: int = Query(default=5000, ge=100, le=20000),
):
    service = _service(request)
    try:
        return {
            "analysis": _decorate_analysis(
                service,
                build_job_analysis(
                    service,
                    job_id,
                    timeline_limit=timeline_limit,
                ),
            )
        }
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found") from exc
