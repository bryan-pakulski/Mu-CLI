"""Subprocess entry point for one durable engineering-job attempt.

Each worker owns its process CWD, Session runtime, lease heartbeat and managed
Git worktree. The background controller can therefore run several jobs in
parallel without sharing Session process-global state.
"""

from __future__ import annotations

import argparse
import os
import threading
from types import SimpleNamespace

from .models import AttentionReason, JobStatus
from .runner import JobRunOutcome, SessionJobRunner
from .service import JobService, JobStateError, get_default_job_service
from .worktree import JobWorktreeManager, WorktreeError


DEFAULT_JOB_SYSTEM = (
    "You are an autonomous software engineering agent working on a durable MuCLI job. "
    "Make focused, production-quality changes inside the attached job workspace. "
    "Use tools to inspect and validate the repository. Never claim merge readiness; "
    "the controller owns verification and review state."
)


def _base_args(job):
    execution = dict(job.execution or {})
    return SimpleNamespace(
        session=SessionJobRunner.session_name(job),
        provider=str(execution.get("provider") or ""),
        model=str(execution.get("model") or ""),
        provider_prevalidated=True,
        session_type=str(execution.get("session_type") or "workspace"),
        workspace=[job.worktree] if job.worktree else [],
        yolo=bool(execution.get("auto_approve_writes", False)),
        gui=False,
        trace=False,
        debug=bool(execution.get("debug", False)),
        system=str(execution.get("system_instruction") or DEFAULT_JOB_SYSTEM),
        system_file=None,
        mode_prompt=None,
    )


def _heartbeat(service: JobService, job_id: str, worker_id: str, ttl: int, stop: threading.Event) -> None:
    interval = max(3.0, float(ttl) / 3.0)
    while not stop.wait(interval):
        try:
            if not service.heartbeat(job_id, worker_id, ttl_seconds=ttl):
                return
        except Exception:
            return


def _checkpoint(manager: JobWorktreeManager, service: JobService, job_id: str, label: str) -> str | None:
    try:
        return manager.checkpoint(service.get(job_id), label=label)
    except Exception as exc:
        service.store.append_event(
            job_id,
            "checkpoint_failed",
            reason=str(exc),
            payload={"label": label},
        )
        return None


def _add_cost(service: JobService, job_id: str, amount: float) -> float:
    current = service.get(job_id)
    total = float(current.cost_usd or 0.0) + max(0.0, float(amount or 0.0))
    service.store.update_runtime_fields(job_id, cost_usd=total)
    return total


def _apply_outcome(
    service: JobService,
    manager: JobWorktreeManager,
    job_id: str,
    attempt_id: str,
    attempt_number: int,
    outcome: JobRunOutcome,
) -> int:
    total_cost = _add_cost(service, job_id, outcome.cost_usd)
    current = service.get(job_id)
    if current.status == JobStatus.CANCELLED:
        service.finish_attempt(
            attempt_id,
            status="cancelled",
            cost_usd=outcome.cost_usd,
            metadata={"cancelled_while_running": True},
        )
        _checkpoint(manager, service, job_id, f"attempt-{attempt_number}-cancelled")
        return 0

    if outcome.kind == "needs_human":
        checkpoint = _checkpoint(manager, service, job_id, f"attempt-{attempt_number}-needs-human")
        service.finish_attempt(
            attempt_id,
            status="needs_human",
            cost_usd=outcome.cost_usd,
            metadata={"attention": outcome.attention_payload, "checkpoint": checkpoint},
        )
        service.require_human(
            job_id,
            outcome.attention_reason,
            outcome.attention_detail,
            payload={**outcome.attention_payload, "checkpoint": checkpoint},
        )
        return 20

    if outcome.kind == "completed":
        checkpoint = _checkpoint(manager, service, job_id, f"attempt-{attempt_number}-implementation")
        service.finish_attempt(
            attempt_id,
            status="completed",
            cost_usd=outcome.cost_usd,
            metadata={
                "agent_status": outcome.status,
                "checkpoint": checkpoint,
                "total_job_cost_usd": total_cost,
            },
        )
        service.transition(
            job_id,
            JobStatus.VERIFYING,
            reason="implementation attempt completed",
            payload={"checkpoint": checkpoint, "attempt_id": attempt_id},
        )
        service.store.append_event(
            job_id,
            "verification_pending",
            reason="Milestone 3 verifier not implemented yet",
            payload={"attempt_id": attempt_id, "checkpoint": checkpoint},
        )
        return 0

    checkpoint = _checkpoint(manager, service, job_id, f"attempt-{attempt_number}-failed")
    service.finish_attempt(
        attempt_id,
        status="failed",
        error=outcome.error,
        cost_usd=outcome.cost_usd,
        metadata={
            "agent_status": outcome.status,
            "checkpoint": checkpoint,
            "total_job_cost_usd": total_cost,
        },
    )
    target = JobStatus.ENVIRONMENT_ERROR if outcome.status == "environment_error" else JobStatus.FAILED
    service.transition(
        job_id,
        target,
        reason="job attempt failed",
        payload={"error": outcome.error, "agent_status": outcome.status, "checkpoint": checkpoint},
    )
    return 1


def run_job(job_id: str, worker_id: str, *, lease_ttl_seconds: int = 45) -> int:
    service = get_default_job_service()
    manager = JobWorktreeManager(service)
    heartbeat_stop = threading.Event()
    heartbeat_thread = threading.Thread(
        target=_heartbeat,
        args=(service, job_id, worker_id, lease_ttl_seconds, heartbeat_stop),
        name=f"mucli-job-worker-heartbeat-{job_id[:8]}",
        daemon=True,
    )
    attempt = None
    try:
        job = service.get(job_id)
        if job.worker_id != worker_id:
            service.store.append_event(
                job_id,
                "worker_rejected",
                reason="worker does not own job lease",
                payload={"expected": job.worker_id, "received": worker_id},
            )
            return 2
        heartbeat_thread.start()

        if job.status == JobStatus.QUEUED:
            job = service.transition(job_id, JobStatus.PREPARING, reason="worker preparing isolated workspace")
        elif job.status != JobStatus.RECOVERING:
            return 0

        execution = dict(job.execution or {})
        if str(execution.get("session_type") or "workspace") == "workspace":
            try:
                manager.prepare(job)
            except WorktreeError as exc:
                service.transition(
                    job_id,
                    JobStatus.ENVIRONMENT_ERROR,
                    reason="could not prepare isolated Git worktree",
                    payload={"error": str(exc)},
                )
                return 3
            job = service.get(job_id)

        attempt = service.start_attempt(
            job_id,
            worker_id=worker_id,
            session_name=SessionJobRunner.session_name(job),
            metadata={
                "process_id": os.getpid(),
                "branch": job.branch,
                "worktree": job.worktree,
                "base_sha": job.base_sha,
            },
        )
        job = service.transition(job_id, JobStatus.RUNNING, reason="isolated worker attempt started")

        import mucli as mucli_entry

        runner = SessionJobRunner(
            service,
            build_session_fn=mucli_entry.build_session,
            base_args=_base_args(job),
        )
        outcome = runner.run(job, attempt)
        return _apply_outcome(service, manager, job_id, attempt.id, attempt.number, outcome)

    except JobStateError:
        return 0
    except BaseException as exc:
        if attempt is not None:
            try:
                service.finish_attempt(attempt.id, status="failed", error=str(exc))
            except Exception:
                pass
        try:
            current = service.get(job_id)
            if current.status not in {JobStatus.CANCELLED, JobStatus.MERGED, JobStatus.FAILED}:
                service.transition(
                    job_id,
                    JobStatus.FAILED,
                    reason="isolated worker crashed",
                    payload={"error": str(exc), "process_id": os.getpid()},
                )
        except Exception:
            pass
        return 4
    finally:
        heartbeat_stop.set()
        if heartbeat_thread.is_alive():
            heartbeat_thread.join(timeout=1.0)
        try:
            service.release(job_id, worker_id, reason="isolated worker exited")
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one MuCLI durable engineering job")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--lease-ttl", type=int, default=45)
    args = parser.parse_args()
    return run_job(args.job_id, args.worker_id, lease_ttl_seconds=args.lease_ttl)


if __name__ == "__main__":
    raise SystemExit(main())
