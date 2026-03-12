from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.app.core.config import settings
from server.app.persistence.db import get_db
from server.app.persistence.models import (
    ApprovalModel,
    ApprovalState,
    EventModel,
    JobModel,
    JobState,
    SessionModel,
    SessionStatus,
)
from server.app.policies.engine import policy_engine
from server.app.providers.registry import provider_registry
from server.app.runtime.event_bus import event_bus
from server.app.runtime.job_runner import job_runner
from server.app.runtime.orchestrator import emit_event
from server.app.schemas import (
    ApprovalDecisionWrite,
    ApprovalRead,
    EventRead,
    JobCreate,
    JobRead,
    PolicyDecisionRead,
    ProviderRead,
    SessionCreate,
    SessionRead,
    SessionUpdate,
    SkillRead,
    ToolRead,
    WorkspaceIndexBuildResponse,
    WorkspaceIndexRead,
    WorkspaceIndexRefreshResponse,
)
from server.app.skills.registry import skill_registry
from server.app.tools.registry import tool_registry
from server.app.workspace.discovery import index_workspace, list_index, refresh_workspace_index

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




@router.get("/sessions", response_model=list[SessionRead])
async def list_sessions(db: AsyncSession = Depends(get_db)) -> list[SessionModel]:
    sessions = (
        await db.scalars(select(SessionModel).order_by(SessionModel.created_at.desc()))
    ).all()
    return list(sessions)


@router.get("/sessions/{session_id}/jobs", response_model=list[JobRead])
async def list_session_jobs(session_id: str, db: AsyncSession = Depends(get_db)) -> list[JobModel]:
    session = await db.scalar(select(SessionModel).where(SessionModel.id == session_id))
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    jobs = (
        await db.scalars(
            select(JobModel)
            .where(JobModel.session_id == session_id)
            .order_by(JobModel.created_at.desc())
        )
    ).all()
    return list(jobs)


@router.patch("/sessions/{session_id}", response_model=SessionRead)
async def update_session(
    session_id: str,
    payload: SessionUpdate,
    db: AsyncSession = Depends(get_db),
) -> SessionModel:
    session = await db.scalar(select(SessionModel).where(SessionModel.id == session_id))
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if payload.mode is not None:
        session.mode = payload.mode
    if payload.provider_preferences is not None:
        session.provider_preferences = payload.provider_preferences
    if payload.policy_profile is not None:
        session.policy_profile = payload.policy_profile

    await db.commit()
    await db.refresh(session)
    await emit_event(
        db,
        session.id,
        "session_config",
        {
            "mode": session.mode,
            "provider_preferences": session.provider_preferences,
            "policy_profile": session.policy_profile,
        },
    )
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


@router.post("/sessions/{session_id}/clear", response_model=SessionRead)
async def clear_session_context(session_id: str, db: AsyncSession = Depends(get_db)) -> SessionModel:
    session = await db.scalar(select(SessionModel).where(SessionModel.id == session_id))
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    session.context_state = {"messages": [], "summary": None, "memory_refs": []}
    await db.commit()
    await db.refresh(session)
    await emit_event(db, session.id, "session_context_cleared", {"status": "ok"})
    return session


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, db: AsyncSession = Depends(get_db)) -> dict:
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

    await db.delete(session)
    await db.commit()
    return {"deleted": True, "session_id": session_id}


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


@router.get("/jobs/{job_id}/approvals", response_model=list[ApprovalRead])
async def get_job_approvals(job_id: str, db: AsyncSession = Depends(get_db)) -> list[ApprovalModel]:
    job = await db.scalar(select(JobModel).where(JobModel.id == job_id))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    approvals = (
        await db.scalars(
            select(ApprovalModel)
            .where(ApprovalModel.job_id == job_id)
            .order_by(ApprovalModel.created_at)
        )
    ).all()
    return list(approvals)


@router.get("/sessions/{session_id}/approvals/pending", response_model=list[ApprovalRead])
async def get_pending_approvals(
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> list[ApprovalModel]:
    session = await db.scalar(select(SessionModel).where(SessionModel.id == session_id))
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    approvals = (
        await db.scalars(
            select(ApprovalModel)
            .where(
                ApprovalModel.session_id == session_id,
                ApprovalModel.state == ApprovalState.pending,
            )
            .order_by(ApprovalModel.created_at)
        )
    ).all()
    return list(approvals)


@router.post("/jobs/{job_id}/approvals/{approval_id}", response_model=ApprovalRead)
async def decide_approval(
    job_id: str,
    approval_id: str,
    payload: ApprovalDecisionWrite,
    db: AsyncSession = Depends(get_db),
) -> ApprovalModel:
    approval = await db.scalar(
        select(ApprovalModel).where(ApprovalModel.id == approval_id, ApprovalModel.job_id == job_id)
    )
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found")
    if approval.state != ApprovalState.pending:
        return approval
    if payload.decision not in {ApprovalState.approved, ApprovalState.denied}:
        raise HTTPException(status_code=400, detail="Only approved/denied are valid decisions")

    approval.state = payload.decision
    await db.commit()
    await db.refresh(approval)
    await emit_event(
        db,
        approval.session_id,
        "approval_decision",
        {"approval_id": approval.id, "state": approval.state.value},
        job_id=approval.job_id,
    )
    return approval




@router.post("/jobs/{job_id}/input", response_model=JobRead)
async def add_job_input(
    job_id: str,
    payload: dict,
    db: AsyncSession = Depends(get_db),
) -> JobModel:
    job = await db.scalar(select(JobModel).where(JobModel.id == job_id))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    session = await db.scalar(select(SessionModel).where(SessionModel.id == job.session_id))
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    context_state = session.context_state or {"messages": [], "summary": None, "memory_refs": []}
    context_state.setdefault("messages", []).append(
        {"role": "user", "content": payload.get("message", "")}
    )
    session.context_state = context_state
    await db.commit()

    await emit_event(
        db,
        job.session_id,
        "user_input",
        {"message": payload.get("message", "")},
        job_id=job.id,
    )
    await db.refresh(job)
    return job

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
    if job.state not in {JobState.cancelled, JobState.failed, JobState.queued, JobState.blocked}:
        raise HTTPException(
            status_code=400,
            detail="Only cancelled/failed/queued/blocked jobs can resume",
        )

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


@router.get("/tools", response_model=list[ToolRead])
async def list_tools() -> list[ToolRead]:
    return [
        ToolRead(
            name=t.name,
            description=t.description,
            risk_level=t.risk_level,
            requires_approval=t.requires_approval,
        )
        for t in tool_registry.list_tools()
    ]


@router.get("/policies/evaluate/{tool_name}", response_model=PolicyDecisionRead)
async def evaluate_policy(tool_name: str, session_mode: str = "interactive") -> PolicyDecisionRead:
    tool = tool_registry.get(tool_name)
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")
    decision = policy_engine.evaluate(session_mode, tool)
    return PolicyDecisionRead(
        tool_name=tool.name,
        decision=decision.decision,
        reason=decision.reason,
    )


@router.get("/skills", response_model=list[SkillRead])
async def list_skills(
    session_id: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[SkillRead]:
    if not session_id:
        return []

    session = await db.scalar(select(SessionModel).where(SessionModel.id == session_id))
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return [
        SkillRead(name=s.name, description=s.description, file_path=s.file_path)
        for s in skill_registry.discover(session.workspace_path)
    ]


@router.post("/sessions/{session_id}/workspace/index", response_model=WorkspaceIndexBuildResponse)
async def build_workspace_index(
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> WorkspaceIndexBuildResponse:
    session = await db.scalar(select(SessionModel).where(SessionModel.id == session_id))
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    count = await index_workspace(
        session_id=session.id,
        workspace_path=session.workspace_path,
        db=db,
    )
    await emit_event(
        db,
        session.id,
        "workspace_indexed",
        {"indexed_files": count},
    )
    return WorkspaceIndexBuildResponse(session_id=session.id, indexed_files=count)


@router.post(
    "/sessions/{session_id}/workspace/index/refresh",
    response_model=WorkspaceIndexRefreshResponse,
)
async def refresh_index(
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> WorkspaceIndexRefreshResponse:
    session = await db.scalar(select(SessionModel).where(SessionModel.id == session_id))
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    stats = await refresh_workspace_index(
        session_id=session.id,
        workspace_path=session.workspace_path,
        db=db,
    )
    await emit_event(
        db,
        session.id,
        "workspace_index_refreshed",
        stats,
    )
    return WorkspaceIndexRefreshResponse(
        session_id=session.id,
        next_refresh_after_s=settings.workspace_index_refresh_interval_s,
        **stats,
    )


@router.get("/sessions/{session_id}/workspace/index", response_model=list[WorkspaceIndexRead])
async def get_workspace_index(
    session_id: str,
    file_type: str | None = None,
    tag: str | None = None,
    limit: int | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[WorkspaceIndexRead]:
    session = await db.scalar(select(SessionModel).where(SessionModel.id == session_id))
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    rows = await list_index(session_id=session.id, db=db, file_type=file_type, tag=tag, limit=limit)
    return [
        WorkspaceIndexRead(
            path=r.path,
            file_type=r.file_type,
            language=r.language,
            last_modified=r.last_modified,
            content_hash=r.content_hash,
            description=r.description,
            key_symbols=r.key_symbols,
            tags=r.tags,
            priority_score=r.priority_score,
        )
        for r in rows
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
