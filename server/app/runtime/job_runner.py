import asyncio

from sqlalchemy import select

from server.app.persistence.db import SessionLocal
from server.app.persistence.models import JobModel, JobState, SessionModel
from server.app.runtime.agent_loop import LoopStep, run_agent_loop
from server.app.runtime.orchestrator import emit_event, update_job_state


class JobRunner:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._cancel_flags: dict[str, asyncio.Event] = {}

    def _cancelled(self, job_id: str) -> bool:
        event = self._cancel_flags.get(job_id)
        return event.is_set() if event else False

    async def _run(self, job_id: str) -> None:
        async with SessionLocal() as db:
            job = await db.scalar(select(JobModel).where(JobModel.id == job_id))
            if not job:
                return

            session = await db.scalar(select(SessionModel).where(SessionModel.id == job.session_id))
            if not session:
                await update_job_state(db, job_id, JobState.failed)
                return

            await update_job_state(db, job_id, JobState.running)
            await emit_event(db, job.session_id, "log", {"message": "job started"}, job_id=job.id)

            async def emit_step(step: LoopStep) -> None:
                job.checkpoints = {"last_completed_step": step.index, "mode": session.mode}
                await db.commit()
                await emit_event(
                    db,
                    job.session_id,
                    "loop_step",
                    {"index": step.index, "label": step.label, "mode": session.mode},
                    job_id=job.id,
                )

            result = await run_agent_loop(
                session=session,
                job=job,
                emit_step=emit_step,
                is_cancelled=lambda: self._cancelled(job_id),
            )

            if result["status"] == "cancelled":
                job.result_artifacts = {
                    "summary": "Job cancelled by user",
                    "mode": result["mode"],
                }
                await db.commit()
                await update_job_state(db, job_id, JobState.cancelled)
                await emit_event(
                    db,
                    job.session_id,
                    "log",
                    {"message": "job cancelled"},
                    job_id=job.id,
                )
            else:
                job.result_artifacts = {
                    "summary": "Job completed successfully",
                    "mode": result["mode"],
                    "steps": result["steps_executed"],
                }
                await db.commit()
                await update_job_state(db, job_id, JobState.completed)
                await emit_event(
                    db,
                    job.session_id,
                    "log",
                    {"message": "job completed"},
                    job_id=job.id,
                )

    def start(self, job_id: str) -> None:
        if job_id in self._tasks and not self._tasks[job_id].done():
            return
        self._cancel_flags[job_id] = asyncio.Event()
        self._tasks[job_id] = asyncio.create_task(self._run(job_id))

    async def cancel(self, job_id: str) -> None:
        if job_id not in self._cancel_flags:
            self._cancel_flags[job_id] = asyncio.Event()
        self._cancel_flags[job_id].set()

    async def resume(self, job_id: str) -> None:
        self.start(job_id)


job_runner = JobRunner()
