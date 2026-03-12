from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.app.persistence.models import EventModel, JobModel, JobState
from server.app.runtime.event_bus import event_bus


async def emit_event(
    db: AsyncSession, session_id: str, event_type: str, payload: dict, job_id: str | None = None
) -> None:
    event = EventModel(session_id=session_id, job_id=job_id, event_type=event_type, payload=payload)
    db.add(event)
    await db.commit()
    await event_bus.publish(
        session_id,
        {
            "event_type": event_type,
            "session_id": session_id,
            "job_id": job_id,
            "payload": payload,
        },
    )


async def update_job_state(db: AsyncSession, job_id: str, state: JobState) -> JobModel:
    job = await db.scalar(select(JobModel).where(JobModel.id == job_id))
    if not job:
        raise ValueError("Job not found")
    job.state = state
    await db.commit()
    await db.refresh(job)
    await emit_event(
        db,
        job.session_id,
        "job_state",
        {"state": job.state.value},
        job_id=job.id,
    )
    return job
