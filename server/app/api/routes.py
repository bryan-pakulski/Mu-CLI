from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.app.persistence.db import get_db
from server.app.persistence.models import (
    EventModel,
    JobModel,
    JobState,
    SessionModel,
    SessionStatus,
)
from server.app.providers.registry import provider_registry
from server.app.runtime.event_bus import event_bus
from server.app.runtime.job_runner import job_runner
from server.app.runtime.orchestrator import emit_event
from server.app.schemas import (
    EventRead,
    JobCreate,
    JobRead,
    ProviderRead,
    SessionCreate,
    SessionRead,
)

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"ok": True}


@router.post("/sessions", response_model=SessionRead)
async def create_session(
    payload: SessionCreate,
    db: AsyncSession = Depends(get_db),
) -> SessionModel:
    session = SessionModel(
        workspace_path=payload.workspace_path,
        mode=payload.mode,
        provider_preferences=payload.provider_preferences,
        policy_profile=payload.policy_profile,
        context_state={"messages": [], "summary": None, "memory_refs": []},
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


@router.get("/sessions/{session_id}", response_model=SessionRead)
async def get_session(session_id: str, db: AsyncSession = Depends(get_db)) -> SessionModel:
    session = await db.scalar(select(SessionModel).where(SessionModel.id == session_id))
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.post("/sessions/{session_id}/pause", response_model=SessionRead)
async def pause_session(session_id: str, db: AsyncSession = Depends(get_db)) -> SessionModel:
    session = await db.scalar(select(SessionModel).where(SessionModel.id == session_id))
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    session.status = SessionStatus.paused
    await db.commit()
    await db.refresh(session)
    await emit_event(db, session.id, "session_state", {"status": session.status.value})
    return session


@router.post("/sessions/{session_id}/resume", response_model=SessionRead)
async def resume_session(session_id: str, db: AsyncSession = Depends(get_db)) -> SessionModel:
    session = await db.scalar(select(SessionModel).where(SessionModel.id == session_id))
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    session.status = SessionStatus.active
    await db.commit()
    await db.refresh(session)
    await emit_event(db, session.id, "session_state", {"status": session.status.value})
    return session


@router.post("/sessions/{session_id}/terminate", response_model=SessionRead)
async def terminate_session(session_id: str, db: AsyncSession = Depends(get_db)) -> SessionModel:
    session = await db.scalar(select(SessionModel).where(SessionModel.id == session_id))
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    jobs = (
        await db.scalars(
            select(JobModel).where(
                JobModel.session_id == session.id,
                JobModel.state.in_([JobState.queued, JobState.running]),
            )
        )
    ).all()
    for job in jobs:
        await job_runner.cancel(job.id)

    session.status = SessionStatus.completed
    await db.commit()
    await db.refresh(session)
    await emit_event(db, session.id, "session_state", {"status": session.status.value})
    return session


@router.post("/sessions/{session_id}/jobs", response_model=JobRead)
async def create_job(
    session_id: str,
    payload: JobCreate,
    db: AsyncSession = Depends(get_db),
) -> JobModel:
    session = await db.scalar(select(SessionModel).where(SessionModel.id == session_id))
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.status != SessionStatus.active:
        raise HTTPException(status_code=400, detail="Session must be active to create jobs")

    job = JobModel(
        session_id=session_id,
        goal=payload.goal,
        constraints=payload.constraints,
        acceptance_criteria=payload.acceptance_criteria,
        checkpoints={},
        result_artifacts={},
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    await emit_event(db, session_id, "job_state", {"state": job.state.value}, job_id=job.id)
    job_runner.start(job.id)
    return job


@router.get("/jobs/{job_id}", response_model=JobRead)
async def get_job(job_id: str, db: AsyncSession = Depends(get_db)) -> JobModel:
    job = await db.scalar(select(JobModel).where(JobModel.id == job_id))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/jobs/{job_id}/events", response_model=list[EventRead])
async def get_job_events(job_id: str, db: AsyncSession = Depends(get_db)) -> list[EventModel]:
    job = await db.scalar(select(JobModel).where(JobModel.id == job_id))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    events = (
        await db.scalars(
            select(EventModel)
            .where(EventModel.job_id == job_id)
            .order_by(EventModel.created_at)
        )
    ).all()
    return list(events)


@router.post("/jobs/{job_id}/run", response_model=JobRead)
async def run_job(job_id: str, db: AsyncSession = Depends(get_db)) -> JobModel:
    job = await db.scalar(select(JobModel).where(JobModel.id == job_id))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.state == JobState.running:
        return job
    job.state = JobState.queued
    job.checkpoints = {}
    job.result_artifacts = {}
    await db.commit()
    await db.refresh(job)
    await emit_event(db, job.session_id, "job_state", {"state": job.state.value}, job_id=job.id)
    job_runner.start(job.id)
    return job


@router.post("/jobs/{job_id}/cancel", response_model=JobRead)
async def cancel_job(job_id: str, db: AsyncSession = Depends(get_db)) -> JobModel:
    job = await db.scalar(select(JobModel).where(JobModel.id == job_id))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    await job_runner.cancel(job.id)
    await emit_event(db, job.session_id, "log", {"message": "cancel requested"}, job_id=job.id)
    await db.refresh(job)
    return job


@router.post("/jobs/{job_id}/resume", response_model=JobRead)
async def resume_job(job_id: str, db: AsyncSession = Depends(get_db)) -> JobModel:
    job = await db.scalar(select(JobModel).where(JobModel.id == job_id))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.state == JobState.running:
        return job
    if job.state not in {JobState.cancelled, JobState.failed, JobState.queued}:
        raise HTTPException(status_code=400, detail="Only cancelled/failed/queued jobs can resume")

    await job_runner.resume(job.id)
    await emit_event(db, job.session_id, "log", {"message": "resume requested"}, job_id=job.id)
    await db.refresh(job)
    return job


@router.get("/providers", response_model=list[ProviderRead])
async def list_providers() -> list[ProviderRead]:
    return [
        ProviderRead(
            name=p.name,
            supports_streaming=p.supports_streaming,
            supports_tools=p.supports_tools,
            supports_thinking=p.supports_thinking,
        )
        for p in provider_registry.list_providers()
    ]


@router.websocket("/stream/sessions/{session_id}")
async def stream_session(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    queue = event_bus.subscribe(session_id)
    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        event_bus.unsubscribe(session_id, queue)
