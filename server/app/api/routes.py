import asyncio

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.app.persistence.db import get_db
from server.app.persistence.models import JobModel, JobState, SessionModel
from server.app.providers.registry import provider_registry
from server.app.runtime.event_bus import event_bus
from server.app.runtime.orchestrator import emit_event, update_job_state
from server.app.schemas import JobCreate, JobRead, ProviderRead, SessionCreate, SessionRead

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


async def _run_job(job_id: str) -> None:
    from server.app.persistence.db import SessionLocal

    async with SessionLocal() as db:
        job = await update_job_state(db, job_id, JobState.running)
        await emit_event(db, job.session_id, "log", {"message": "job started"}, job_id=job.id)
        await asyncio.sleep(0.05)
        job.result_artifacts = {"summary": "Initial Phase 1 runtime execution completed."}
        await db.commit()
        await update_job_state(db, job_id, JobState.completed)


@router.post("/sessions/{session_id}/jobs", response_model=JobRead)
async def create_job(
    session_id: str,
    payload: JobCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> JobModel:
    session = await db.scalar(select(SessionModel).where(SessionModel.id == session_id))
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

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
    background_tasks.add_task(_run_job, job.id)
    return job


@router.get("/jobs/{job_id}", response_model=JobRead)
async def get_job(job_id: str, db: AsyncSession = Depends(get_db)) -> JobModel:
    job = await db.scalar(select(JobModel).where(JobModel.id == job_id))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
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
