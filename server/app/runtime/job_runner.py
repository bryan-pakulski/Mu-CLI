import asyncio

from sqlalchemy import select

from server.app.core.config import settings
from server.app.persistence.db import SessionLocal
from server.app.persistence.models import (
    ApprovalModel,
    ApprovalState,
    JobModel,
    JobState,
    SessionModel,
)
from server.app.policies.engine import policy_engine
from server.app.providers.router import provider_router
from server.app.runtime.agent_loop import LoopStep, run_agent_loop
from server.app.runtime.orchestrator import emit_event, update_job_state
from server.app.tools.registry import tool_registry


class JobRunner:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._cancel_flags: dict[str, asyncio.Event] = {}

    def _cancelled(self, job_id: str) -> bool:
        event = self._cancel_flags.get(job_id)
        return event.is_set() if event else False

    def _cleanup(self, job_id: str) -> None:
        self._cancel_flags.pop(job_id, None)
        self._tasks.pop(job_id, None)

    async def _handle_tool_policy(
        self,
        db,
        session: SessionModel,
        job: JobModel,
    ) -> bool:
        requested_tool = (job.constraints or {}).get("tool_name")
        if not requested_tool:
            return True

        tool = tool_registry.get(requested_tool)
        if not tool:
            await emit_event(
                db,
                job.session_id,
                "policy",
                {"tool_name": requested_tool, "decision": "deny", "reason": "unknown tool"},
                job_id=job.id,
            )
            await update_job_state(db, job.id, JobState.failed)
            return False

        decision = policy_engine.evaluate(session.mode, tool)
        await emit_event(
            db,
            job.session_id,
            "policy",
            {
                "tool_name": tool.name,
                "decision": decision.decision,
                "reason": decision.reason,
            },
            job_id=job.id,
        )

        if decision.decision == "allow":
            return True

        if decision.decision == "deny":
            await update_job_state(db, job.id, JobState.blocked)
            return False

        if decision.decision not in {"ask", "escalate"}:
            await update_job_state(db, job.id, JobState.blocked)
            return False

        approval = ApprovalModel(
            session_id=job.session_id,
            job_id=job.id,
            tool_name=tool.name,
            reason=decision.reason,
            state=ApprovalState.pending,
        )
        db.add(approval)
        await db.commit()
        await db.refresh(approval)

        await update_job_state(db, job.id, JobState.awaiting_approval)
        await emit_event(
            db,
            job.session_id,
            "approval_requested",
            {
                "approval_id": approval.id,
                "tool_name": tool.name,
                "reason": approval.reason,
                "decision": decision.decision,
            },
            job_id=job.id,
        )

        for _ in range(300):
            if self._cancelled(job.id):
                return False
            latest_approval = await db.scalar(
                select(ApprovalModel).where(ApprovalModel.id == approval.id)
            )
            if not latest_approval:
                await update_job_state(db, job.id, JobState.blocked)
                return False
            if latest_approval.state == ApprovalState.approved:
                await update_job_state(db, job.id, JobState.queued)
                return True
            if latest_approval.state == ApprovalState.denied:
                await update_job_state(db, job.id, JobState.blocked)
                return False
            await asyncio.sleep(0.1)

        await update_job_state(db, job.id, JobState.blocked)
        return False

    async def _run(self, job_id: str) -> None:
        async with SessionLocal() as db:
            job = await db.scalar(select(JobModel).where(JobModel.id == job_id))
            if not job:
                return

            session = await db.scalar(select(SessionModel).where(SessionModel.id == job.session_id))
            if not session:
                await update_job_state(db, job_id, JobState.failed)
                return

            checkpoints = job.checkpoints or {}
            attempts = int(checkpoints.get("attempts", 0)) + 1
            checkpoints["attempts"] = attempts
            job.checkpoints = checkpoints
            await db.commit()

            if not await self._handle_tool_policy(db, session, job):
                return

            await update_job_state(db, job_id, JobState.running)
            await emit_event(
                db,
                job.session_id,
                "log",
                {"message": "job started", "attempt": attempts},
                job_id=job.id,
            )

            ordered_providers = list(
                (session.provider_preferences or {}).get("ordered", ["ollama"])
            )

            async def emit_step(step: LoopStep) -> None:
                prompt = f"goal={job.goal}\nmode={session.mode}\nstep={step.label}"
                result = await provider_router.generate_with_fallback(
                    prompt=prompt,
                    ordered_providers=ordered_providers,
                    model=None,
                    max_retries=settings.provider_max_retries,
                )

                last_provider = result.provider_name
                job.checkpoints = {
                    "last_completed_step": step.index,
                    "mode": session.mode,
                    "attempts": attempts,
                    "provider": last_provider,
                }
                await db.commit()
                await emit_event(
                    db,
                    job.session_id,
                    "loop_step",
                    {
                        "index": step.index,
                        "label": step.label,
                        "mode": session.mode,
                        "provider": last_provider,
                        "output_preview": result.output[:180],
                    },
                    job_id=job.id,
                )

            try:
                result = await run_agent_loop(
                    session=session,
                    job=job,
                    emit_step=emit_step,
                    is_cancelled=lambda: self._cancelled(job_id),
                )
            except Exception as exc:  # noqa: BLE001
                await emit_event(
                    db,
                    job.session_id,
                    "log",
                    {"message": "job failed", "error": str(exc), "attempt": attempts},
                    job_id=job.id,
                )
                await update_job_state(db, job_id, JobState.failed)
                return

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
                    "attempts": attempts,
                    "provider": (job.checkpoints or {}).get("provider"),
                }
                await db.commit()
                await update_job_state(db, job_id, JobState.completed)
                await emit_event(
                    db,
                    job.session_id,
                    "log",
                    {
                        "message": "job completed",
                        "attempt": attempts,
                        "provider": (job.checkpoints or {}).get("provider"),
                    },
                    job_id=job.id,
                )

    def start(self, job_id: str) -> None:
        if job_id in self._tasks and not self._tasks[job_id].done():
            return
        self._cancel_flags[job_id] = asyncio.Event()
        task = asyncio.create_task(self._run(job_id))
        self._tasks[job_id] = task
        task.add_done_callback(lambda _: self._cleanup(job_id))

    async def cancel(self, job_id: str) -> None:
        if job_id not in self._cancel_flags:
            self._cancel_flags[job_id] = asyncio.Event()
        self._cancel_flags[job_id].set()

    async def resume(self, job_id: str) -> None:
        self.start(job_id)


job_runner = JobRunner()
