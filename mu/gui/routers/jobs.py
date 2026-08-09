"""Durable engineering-job API shared by the browser GUI and mobile client."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request

from mu.jobs import AttentionReason, JobService, JobStateError, JobStatus
from mu.jobs.board import build_job_board
from mu.jobs.diagnostics import build_job_diagnostics
from mu.jobs.receipt import JobReceiptBuilder
from mu.jobs.repository import RepositoryRegistry
from mu.jobs.review import JobReviewError, JobReviewService, build_job_diff
from mu.jobs.verification import VerificationStore


router = APIRouter()


def _service(request: Request) -> JobService:
    service = getattr(request.app.state, "job_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="job service is unavailable")
    return service


def _job_or_404(service: JobService, job_id: str):
    try:
        return service.get(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found") from exc


def _state_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (JobStateError, JobReviewError, ValueError)):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def _with_session_defaults(request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Allow GUI/mobile job creation to inherit the currently loaded session.

    The durable job still stores explicit provider/model/workspace values; this
    is only a control-plane convenience at creation time.
    """
    value = dict(payload or {})
    session_name = str(value.get("session_name") or "").strip()
    if not session_name:
        return value
    resolver = getattr(request.app.state, "session_by_name", None)
    session = resolver(session_name) if callable(resolver) else None
    if session is None:
        raise HTTPException(status_code=409, detail=f"Session '{session_name}' is not loaded")

    folders = list(getattr(getattr(session, "folder_context", None), "folders", []) or [])
    if not str(value.get("repository") or "").strip() and folders:
        value["repository"] = folders[0]
    execution = dict(value.get("execution") or {})
    provider = getattr(getattr(session, "provider", None), "name", "") or ""
    model = getattr(getattr(session, "provider", None), "model_name", "") or ""
    variables = getattr(session, "variables", {}) or {}
    execution.setdefault("provider", str(provider))
    execution.setdefault("model", str(model))
    execution.setdefault("agent_mode", str(variables.get("agent_mode", "default") or "default"))
    execution.setdefault("session_type", str(variables.get("session_type", "workspace") or "workspace"))
    execution.setdefault("auto_approve_writes", bool(variables.get("yolo", False)))
    value["execution"] = execution
    return value


@router.get("")
async def list_jobs(
    request: Request,
    status: Optional[List[str]] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
):
    service = _service(request)
    try:
        jobs = service.list(statuses=status, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"jobs": [job.to_dict() for job in jobs]}


@router.post("")
async def create_job(request: Request, payload: Dict[str, Any]):
    service = _service(request)
    try:
        job = service.create_from_payload(_with_session_defaults(request, payload))
    except HTTPException:
        raise
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"job": job.to_dict()}


@router.get("/board")
async def get_job_board(request: Request):
    return build_job_board(_service(request)).to_dict()


@router.get("/repositories")
async def list_job_repositories(
    request: Request,
    limit: int = Query(default=200, ge=1, le=1000),
):
    registry = RepositoryRegistry(_service(request).store)
    return {"repositories": [item.to_dict() for item in registry.list(limit=limit)]}


@router.get("/repositories/{repository_id}")
async def get_job_repository(repository_id: str, request: Request):
    registry = RepositoryRegistry(_service(request).store)
    try:
        record = registry.get(repository_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Repository '{repository_id}' not found") from exc
    return {"repository": record.to_dict()}


@router.get("/{job_id}")
async def get_job(job_id: str, request: Request):
    job = _job_or_404(_service(request), job_id)
    return {"job": job.to_dict()}


@router.get("/{job_id}/receipt")
async def get_job_receipt(job_id: str, request: Request):
    service = _service(request)
    _job_or_404(service, job_id)
    return {"receipt": JobReceiptBuilder(service).build(job_id)}


@router.get("/{job_id}/diagnostics")
async def get_job_diagnostics(
    job_id: str,
    request: Request,
    event_limit: int = Query(default=200, ge=1, le=1000),
    log_tail_bytes: int = Query(default=65_536, ge=1024, le=524_288),
):
    service = _service(request)
    _job_or_404(service, job_id)
    return {
        "diagnostics": build_job_diagnostics(
            service,
            job_id,
            event_limit=event_limit,
            log_tail_bytes=log_tail_bytes,
        ).to_dict()
    }


@router.get("/{job_id}/diff")
async def get_job_diff(
    job_id: str,
    request: Request,
    max_chars: int = Query(default=500_000, ge=10_000, le=2_000_000),
):
    service = _service(request)
    _job_or_404(service, job_id)
    try:
        return {"diff": build_job_diff(service, job_id, max_chars=max_chars).to_dict()}
    except JobReviewError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{job_id}/events")
async def get_job_events(
    job_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=5000),
):
    service = _service(request)
    _job_or_404(service, job_id)
    return {"events": [event.to_dict() for event in service.events(job_id, after_id=after, limit=limit)]}


@router.get("/{job_id}/attempts")
async def get_job_attempts(job_id: str, request: Request):
    service = _service(request)
    _job_or_404(service, job_id)
    return {"attempts": [attempt.to_dict() for attempt in service.attempts(job_id)]}


@router.get("/{job_id}/verifications")
async def get_job_verifications(
    job_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
):
    service = _service(request)
    _job_or_404(service, job_id)
    values = VerificationStore(service.store).list(job_id, limit=limit)
    return {"verifications": [value.to_dict() for value in values]}


@router.get("/{job_id}/verifications/{verification_id}")
async def get_job_verification(job_id: str, verification_id: str, request: Request):
    service = _service(request)
    _job_or_404(service, job_id)
    try:
        value = VerificationStore(service.store).get(verification_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Verification '{verification_id}' not found") from exc
    if value.job_id != job_id:
        raise HTTPException(status_code=404, detail=f"Verification '{verification_id}' not found for job")
    return {"verification": value.to_dict()}


@router.post("/{job_id}/respond")
async def respond_to_job(job_id: str, request: Request, payload: Dict[str, Any]):
    service = _service(request)
    _job_or_404(service, job_id)
    try:
        job = JobReviewService(service).respond(
            job_id,
            detail=str(payload.get("detail") or ""),
            decision=str(payload.get("decision") or ""),
            value=payload.get("value"),
            selected=list(payload.get("selected") or []),
        )
    except (JobReviewError, JobStateError, ValueError) as exc:
        raise _state_error(exc) from exc
    return {"job": job.to_dict()}


@router.post("/{job_id}/request-changes")
async def request_job_changes(job_id: str, request: Request, payload: Dict[str, Any]):
    service = _service(request)
    _job_or_404(service, job_id)
    try:
        job = JobReviewService(service).request_changes(job_id, str(payload.get("feedback") or ""))
    except (JobReviewError, JobStateError) as exc:
        raise _state_error(exc) from exc
    return {"job": job.to_dict()}


@router.post("/{job_id}/continue")
async def continue_job(job_id: str, request: Request, payload: Dict[str, Any] | None = None):
    service = _service(request)
    _job_or_404(service, job_id)
    try:
        job = JobReviewService(service).continue_job(job_id, str((payload or {}).get("detail") or ""))
    except (JobReviewError, JobStateError) as exc:
        raise _state_error(exc) from exc
    return {"job": job.to_dict()}


@router.post("/{job_id}/discard")
async def discard_job(job_id: str, request: Request, payload: Dict[str, Any] | None = None):
    service = _service(request)
    _job_or_404(service, job_id)
    try:
        job = JobReviewService(service).discard(job_id, str((payload or {}).get("reason") or ""))
    except (JobReviewError, JobStateError) as exc:
        raise _state_error(exc) from exc
    return {"job": job.to_dict()}


@router.post("/{job_id}/transition")
async def transition_job(job_id: str, request: Request, payload: Dict[str, Any]):
    service = _service(request)
    _job_or_404(service, job_id)
    target = str(payload.get("status") or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="status is required")
    try:
        job = service.transition(
            job_id,
            JobStatus(target),
            reason=str(payload.get("reason") or ""),
            payload=dict(payload.get("payload") or {}),
            attention_reason=str(payload.get("attention_reason") or ""),
            attention_detail=str(payload.get("attention_detail") or ""),
            expected_version=payload.get("expected_version"),
        )
    except (JobStateError, RuntimeError, ValueError) as exc:
        raise _state_error(exc) from exc
    return {"job": job.to_dict()}


# Compatibility controls retained while clients migrate to the review actions.
@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request, payload: Dict[str, Any] | None = None):
    service = _service(request)
    _job_or_404(service, job_id)
    try:
        job = service.cancel(job_id, reason=str((payload or {}).get("reason") or "cancelled by user"))
    except JobStateError as exc:
        raise _state_error(exc) from exc
    return {"job": job.to_dict()}


@router.post("/{job_id}/attention")
async def require_attention(job_id: str, request: Request, payload: Dict[str, Any]):
    service = _service(request)
    _job_or_404(service, job_id)
    reason = str(payload.get("reason") or "").strip()
    detail = str(payload.get("detail") or "").strip()
    if not reason or not detail:
        raise HTTPException(status_code=400, detail="reason and detail are required")
    try:
        job = service.require_human(
            job_id,
            AttentionReason(reason),
            detail,
            payload=dict(payload.get("payload") or {}),
        )
    except (JobStateError, ValueError) as exc:
        raise _state_error(exc) from exc
    return {"job": job.to_dict()}


@router.post("/{job_id}/resume")
async def resume_job(job_id: str, request: Request, payload: Dict[str, Any] | None = None):
    service = _service(request)
    _job_or_404(service, job_id)
    try:
        job = service.resume(job_id, detail=str((payload or {}).get("detail") or ""))
    except JobStateError as exc:
        raise _state_error(exc) from exc
    return {"job": job.to_dict()}


@router.post("/{job_id}/retry")
async def retry_job(job_id: str, request: Request, payload: Dict[str, Any] | None = None):
    service = _service(request)
    _job_or_404(service, job_id)
    try:
        job = service.retry(job_id, reason=str((payload or {}).get("reason") or "retry requested"))
    except JobStateError as exc:
        raise _state_error(exc) from exc
    return {"job": job.to_dict()}
