"""The one worker entry point for durable engineering jobs.

The controller launches ``python -m mu.jobs.worker --phase <phase>`` for each
leased job; ``run_phase`` dispatches to the phase body (implementation here,
checks in ``verify_worker``, review in ``review_worker``, publish in
``publish``). Every phase runs inside ``lease.run_leased``: ownership check,
heartbeat, lease-fenced status writes, release on exit. Each worker owns its
process CWD and managed Git worktree, so several jobs run in parallel without
sharing process-global state.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Optional

from mu.ui.exceptions import InteractionRequired

from .core.states import PHASES
from .lease import run_leased
from .models import AttentionReason, JobStatus
from .receipt import refresh_receipt as _refresh_receipt
from .runner import JobRunOutcome, session_name
from .service import JobService, JobStateError, get_default_job_service
from .worktree import JobWorktreeManager, WorktreeError


def _checkpoint(manager: JobWorktreeManager, service: JobService, job_id: str, label: str,
                blocked: Optional[list] = None) -> str | None:
    """Guard and checkpoint the worktree. A host guard block (``SandboxGuardError``) is appended
    to ``blocked`` when given, so the caller can park the job instead of failing it."""
    try:
        current = service.get(job_id)
        if current.worktree:
            from .sandbox.runner import _guard
            from .sandbox.policy import guard_policy
            _guard(current, guard_policy(current, "implement"))
        checkpoint = manager.checkpoint(current, label=label)
        if checkpoint and current.base_sha:
            from .core.artifacts import artifacts_for
            patch = manager._run(current.worktree, "diff", "--no-ext-diff", f"{current.base_sha}..{checkpoint}")
            artifacts_for(current).ensure().write_text("diff.patch", patch.stdout or "")
        return checkpoint
    except Exception as exc:
        from .sandbox.policy import SandboxGuardError
        if blocked is not None and isinstance(exc, SandboxGuardError):
            blocked.append(exc)
        payload = {"label": label}
        if isinstance(exc, WorktreeError):
            payload.update(exc.to_dict())
        service.store.append_event(
            job_id,
            "checkpoint_failed",
            reason=str(exc),
            payload=payload,
        )
        return None


def _apply_outcome(
    service: JobService,
    manager: JobWorktreeManager,
    job_id: str,
    attempt_id: str,
    attempt_number: int,
    outcome: JobRunOutcome,
    *,
    worker_id: str = "",
) -> int:
    """Apply a run outcome under the worker's lease.

    Round-39 F1: every branch finishes the attempt AND transitions the
    job through store.finish_attempt_owned() — a single lease-conditioned
    transaction. Round-40 F1: job-cost accumulation happens INSIDE that
    transaction (cost_add) — a superseded worker can no longer add its
    cost to the replacement's job before failing the ownership check.
    Round-40 F4: on lease loss every branch returns 5 IMMEDIATELY — no
    receipt refresh (the job may have been deleted by the new owner).
    """
    current = service.get(job_id)
    common_metadata = {
        "agent_status": outcome.status,
        "agent_result": dict(outcome.result or {}),
    }
    # Checkpoints are git operations OUTSIDE the lease transaction; if
    # ownership is lost below, the checkpoint remains as harmless evidence
    # on the managed branch (it never mutates the job row).
    if current.status == JobStatus.CANCELLED:
        checkpoint = _checkpoint(manager, service, job_id, f"attempt-{attempt_number}-cancelled")
        owned = service.store.finish_attempt_owned(
            attempt_id,
            worker_id=worker_id,
            status="cancelled",
            cost_usd=outcome.cost_usd,
            cost_add=outcome.cost_usd,
            metadata={**common_metadata, "cancelled_while_running": True, "checkpoint": checkpoint},
        )
        if not owned:
            return 5
        _refresh_receipt(service, job_id)
        return 0

    if outcome.kind == "needs_human":
        checkpoint = _checkpoint(manager, service, job_id, f"attempt-{attempt_number}-needs-human")
        attention_meta = {
            **common_metadata,
            "attention": outcome.attention_payload,
            # Round-16 F20: persist the reason so crash recovery can
            # idempotently re-enter NEEDS_HUMAN without re-running the
            # attempt (the lease-recovery path replays from this row).
            "attention_reason": str(
                getattr(outcome.attention_reason, "value", None)
                or outcome.attention_reason or ""
            ),
            "attention_detail": outcome.attention_detail,
            "checkpoint": checkpoint,
        }
        owned = service.store.finish_attempt_owned(
            attempt_id,
            worker_id=worker_id,
            status="needs_human",
            cost_usd=outcome.cost_usd,
            cost_add=outcome.cost_usd,
            metadata=attention_meta,
            target_status=JobStatus.NEEDS_HUMAN,
            transition_reason="human attention required",
            transition_payload={**outcome.attention_payload, "checkpoint": checkpoint},
            attention_reason=outcome.attention_reason,
            attention_detail=outcome.attention_detail,
        )
        if not owned:
            return 5
        _refresh_receipt(service, job_id)
        return 20

    if outcome.kind == "completed":
        blocked: list = []
        checkpoint = _checkpoint(manager, service, job_id, f"attempt-{attempt_number}-implementation", blocked)
        if not checkpoint and blocked:
            # A security control stopped the change: park for a human, never fail the job.
            from .sandbox.runner import guard_blocked
            detail = guard_blocked(service, current, "implement", blocked[0])
            owned = service.store.finish_attempt_owned(
                attempt_id, worker_id=worker_id, status="needs_human", cost_usd=outcome.cost_usd,
                cost_add=outcome.cost_usd,
                metadata={**common_metadata, "attention_reason": AttentionReason.UNSAFE_ACTION.value,
                          "attention_detail": detail, "attention": {"stage": "implement", "guard": str(blocked[0])}},
                target_status=JobStatus.NEEDS_HUMAN, transition_reason="human attention required",
                transition_payload={"stage": "implement", "guard": str(blocked[0])},
                attention_reason=AttentionReason.UNSAFE_ACTION, attention_detail=detail)
            if not owned:
                return 5
            _refresh_receipt(service, job_id)
            return 20
        if not checkpoint:
            owned = service.store.finish_attempt_owned(
                attempt_id, worker_id=worker_id, status="failed", error="host checkpoint or path guard failed",
                cost_usd=outcome.cost_usd, cost_add=outcome.cost_usd, target_status=JobStatus.FAILED,
                transition_reason="host checkpoint or path guard failed")
            return 1 if owned else 5
        owned = service.store.finish_attempt_owned(
            attempt_id,
            worker_id=worker_id,
            status="completed",
            cost_usd=outcome.cost_usd,
            cost_add=outcome.cost_usd,
            metadata={**common_metadata, "checkpoint": checkpoint},
            target_status=JobStatus.VERIFYING,
            transition_reason="implementation attempt completed",
            transition_payload={"checkpoint": checkpoint, "attempt_id": attempt_id},
        )
        if not owned:
            return 5
        service.store.append_event(
            job_id,
            "verification_pending",
            reason="deterministic verifier scheduled",
            payload={"attempt_id": attempt_id, "checkpoint": checkpoint},
        )
        _refresh_receipt(service, job_id)
        return 0

    checkpoint = _checkpoint(manager, service, job_id, f"attempt-{attempt_number}-failed")
    if outcome.status == "environment_error":
        target = JobStatus.ENVIRONMENT_ERROR
    else:
        target = JobStatus.FAILED

    # Unattended retry: a transient provider/network failure is requeued
    # with exponential backoff while the attempt count is within
    # max_retries (same budget the verifier uses). Non-transient failures
    # and exhausted budgets stay terminal FAILED so a human sees the truth.
    retry_budget = int(getattr(current, "max_retries", 0) or 0)
    # Only earlier automatic transient retries consume the budget: fix
    # attempts (verification/review rework) and human retries also create
    # attempt rows, so the raw attempt number would starve a job that
    # needed a couple of fix rounds of its transient-retry budget.
    retries_used = _transient_retries_used(service, job_id, attempt_id)
    error_class = str(getattr(outcome, "error_class", "") or "")
    retryable = (
        target == JobStatus.FAILED
        and bool(getattr(outcome, "transient", False))
        and retries_used < retry_budget
    )
    if retryable:
        delay = _retry_backoff_seconds(retries_used + 1)
        next_run_at = float(service.store._clock()) + delay
        owned = service.store.finish_attempt_owned(
            attempt_id,
            worker_id=worker_id,
            status="failed",
            error=outcome.error,
            cost_usd=outcome.cost_usd,
            cost_add=outcome.cost_usd,
            metadata={
                **common_metadata,
                "checkpoint": checkpoint,
                "transient": True,
                "retry_scheduled": True,
                "retry_number": retries_used + 1,
                "next_run_at": next_run_at,
                **({"error_class": error_class} if error_class else {}),
            },
            target_status=JobStatus.QUEUED,
            transition_reason="transient provider failure; requeued with backoff",
            transition_payload={
                "error": outcome.error,
                "agent_status": outcome.status,
                "checkpoint": checkpoint,
                "attempt": int(attempt_number),
                "retry_number": retries_used + 1,
                "max_retries": retry_budget,
                "backoff_seconds": delay,
                **({"error_class": error_class} if error_class else {}),
                "next_run_at": next_run_at,
            },
        )
        if not owned:
            return 5
        _schedule_next_run(service, job_id, next_run_at)
        _refresh_receipt(service, job_id)
        return 10

    owned = service.store.finish_attempt_owned(
        attempt_id,
        worker_id=worker_id,
        status="failed",
        error=outcome.error,
        cost_usd=outcome.cost_usd,
        cost_add=outcome.cost_usd,
        metadata={
            **common_metadata,
            "checkpoint": checkpoint,
            "transient": bool(getattr(outcome, "transient", False)),
            "retry_budget_exhausted": bool(getattr(outcome, "transient", False)),
            **({"error_class": error_class} if error_class else {}),
        },
        target_status=target,
        transition_reason=(
            "transient failure; automatic retry budget exhausted"
            if getattr(outcome, "transient", False) and target == JobStatus.FAILED
            else "job attempt failed"
        ),
        transition_payload={
            "error": outcome.error,
            "agent_status": outcome.status,
            "checkpoint": checkpoint,
            "attempt": int(attempt_number),
            "max_retries": retry_budget,
        },
    )
    if not owned:
        return 5
    _refresh_receipt(service, job_id)
    return 1


def _transient_retries_used(service, job_id: str, current_attempt_id: str) -> int:
    """Automatic transient-failure requeues already spent by this job."""
    try:
        attempts = service.store.list_attempts(job_id)
    except Exception:  # noqa: BLE001
        return 0
    return sum(1 for a in attempts
               if a.id != current_attempt_id and (a.metadata or {}).get("retry_scheduled"))


# Backoff schedule for automatic transient-failure requeues: 30s, 60s,
# 120s, ... capped at 10 minutes. Attempt numbers start at 1.
RETRY_BACKOFF_BASE_SECONDS = 30.0
RETRY_BACKOFF_MAX_SECONDS = 600.0


def _retry_backoff_seconds(attempt_number: int) -> float:
    from .job_settings import rule_or
    base = float(rule_or("retry.backoff_base_s", RETRY_BACKOFF_BASE_SECONDS))
    cap = float(rule_or("retry.backoff_max_s", RETRY_BACKOFF_MAX_SECONDS))
    exponent = max(0, int(attempt_number) - 1)
    return float(min(cap, base * (2 ** exponent)))


def _schedule_next_run(service, job_id: str, next_run_at: float) -> None:
    """Persist the earliest time the controller may lease this job again.
    Stored in job metadata (no schema migration); the controller's tick
    skips QUEUED jobs whose ``next_run_at`` is still in the future."""
    try:
        service.store.patch_metadata(job_id, {"next_run_at": float(next_run_at)})
    except Exception:  # noqa: BLE001 — scheduling hint is best-effort
        logging.getLogger("mucli").debug("next_run_at update failed", exc_info=True)


def _maybe_run_plan_stage(service, job_id: str, generate_fn=None):
    """Durable job v2: PREPARING -> PLANNING -> (caller moves to RUNNING).

    Runs once per job; the versioned plan artifact is reused on rework. Returns an exit code when the job was parked, else None.
    """
    from . import stages
    from .job_settings import missing_stages, plan_approval_required

    job = service.get(job_id)
    missing = missing_stages(job.execution)
    if not stages.is_staged(job):
        missing = [s for s in missing if s == "implement"]
    if missing:
        service.require_human(
            job_id,
            AttentionReason.MODELS_UNSET,
            "No model is set for the "
            + ", ".join(missing)
            + " stage(s). Open Work → Job settings → Stage models, pick a provider and model "
            "for each stage, then resume this job.",
            payload={"missing_stages": missing},
        )
        return 27
    from .core.plan_ops import plan_store
    if not stages.is_staged(job) or (plan_store(job).latest_version() and job.status != JobStatus.PLANNING):
        return None
    service.transition(job_id, JobStatus.PLANNING, reason="planning stage started")
    try:
        stages.run_plan_stage(
            service, job_id, generate_fn=generate_fn or stages.default_generate(service)
        )
    except Exception as exc:  # noqa: BLE001 - planner failure parks on a human
        from .sandbox.runner import SandboxUnavailable
        from .sandbox.environments import EnvironmentBlocked
        if isinstance(exc, SandboxUnavailable):
            service.transition(job_id, JobStatus.FAILED, reason=str(exc),
                               payload={"stage": "provisioning", "exception_type": "SandboxUnavailable"})
            return 3
        if isinstance(exc, EnvironmentBlocked):
            service.require_human(job_id, AttentionReason.ENVIRONMENT_FAILURE, str(exc)[:2000], payload={"stage": "plan"})
            return 26
        from .sandbox.runner import StageLimitReached
        if isinstance(exc, StageLimitReached):
            # A reached cap pauses the planner resumably; the human approves more or cancels.
            service.require_human(job_id, AttentionReason.LIMIT_REACHED, str(exc)[:2000],
                                  payload={"stage": "plan", "limit": exc.detail})
            return 26
        service.require_human(
            job_id,
            AttentionReason.PROVIDER_ERROR,
            f"Planning stage failed: {str(exc)[:400]}",
            payload={"stage": "plan"},
        )
        return 26
    current = service.get(job_id)
    if current.status != JobStatus.PLANNING:
        return 0  # parked by the plan contract (e.g. NO_VALIDATION)
    if plan_approval_required(current.execution):
        doc = plan_store(current).load()
        plan = doc.to_dict() if doc else {}
        service.require_human(
            job_id,
            AttentionReason.PLAN_REVIEW,
            "Review the generated plan, acceptance criteria and validation commands, "
            "then approve to start implementation (or request a re-plan).",
            payload={
                "stage": "plan",
                "plan": plan,
                "acceptance_criteria": list(current.acceptance_criteria or []),
                "validation_commands": list(current.validation_commands or []),
            },
        )
        return 0
    from .core.plan_ops import plan_store
    from .stages import merge_metadata
    version = plan_store(current).latest_version()
    if version:
        merge_metadata(service, job_id, plan_approved_version=version)
        service.store.append_event(job_id, "plan_version_approved", reason="automatic approval configured",
                                   payload={"version": version, "automatic": True})
    return None


def _require_approved_plan(service, job) -> Optional[int]:
    """A staged job implements only an approved plan version.

    Lease recovery used to route a job that died in PLANNING through
    RECOVERING straight into implementation with no plan at all. Whatever
    path reaches this point, a staged job without ``plan_approved_version``
    parks on a human instead of implementing. Returns an exit code when the
    job was parked, else None."""
    from . import stages

    if not stages.is_staged(job) or (job.metadata or {}).get("plan_approved_version"):
        return None
    try:
        from .core.plan_ops import plan_store
        latest = plan_store(job).latest_version()
    except Exception:  # noqa: BLE001 - unreadable artifacts count as no plan
        latest = None
    if latest:
        reason = AttentionReason.PLAN_REVIEW
        detail = (f"Implementation needs an approved plan, but plan v{latest} has not been approved. "
                  "Review and approve the plan (or request a re-plan) to continue.")
    else:
        reason = AttentionReason.WORKER_LOST
        detail = ("Implementation needs an approved plan, but this job has no plan. "
                  "Resume or retry the job to run the planning stage again.")
    service.require_human(job.id, reason, detail,
                          payload={"stage": "implement", "missing": "plan_approved_version", "plan_version": latest or 0})
    return 28


def run_job(job_id: str, worker_id: str, *, lease_ttl_seconds: int = 45) -> int:
    """Implementation phase: prepare the worktree, plan if needed, run one implement attempt."""
    return run_leased(
        get_default_job_service(), job_id, worker_id, phase="implementation",
        lease_ttl_seconds=lease_ttl_seconds, body=_implementation,
        statuses=(JobStatus.QUEUED, JobStatus.RECOVERING, JobStatus.PLANNING),
        reject_event="worker_rejected", release_reason="isolated worker exited",
    )


def _implementation(service: JobService, job, lease) -> int:
    job_id, worker_id = job.id, lease.worker_id
    manager = JobWorktreeManager(service)
    attempt = None
    try:
        if job.status == JobStatus.QUEUED:
            # Consume any automatic-retry backoff hint: this attempt IS the
            # scheduled retry, so a stale next_run_at must not linger.
            service._clear_retry_backoff(job_id)
            job = service.transition(job_id, JobStatus.PREPARING, reason="worker preparing isolated workspace")

        execution = dict(job.execution or {})
        if str(execution.get("session_type") or "workspace") == "workspace" and not str(job.repository or "").strip():
            # An empty workspace used to resolve against the worker's cwd,
            # silently running the job in the GUI's own repository.
            service.transition(
                job_id,
                JobStatus.ENVIRONMENT_ERROR,
                reason="job has no workspace",
                payload={"error": "Job was created without a workspace; recreate it from a session with an attached workspace."},
            )
            _refresh_receipt(service, job_id)
            return 3
        if str(execution.get("session_type") or "workspace") == "workspace":
            try:
                manager.prepare(job)
            except WorktreeError as exc:
                diagnostics = exc.to_dict()
                diagnostics.update(
                    {
                        "repository": job.repository,
                        "requested_base_branch": job.base_branch,
                        "requested_base_sha": job.base_sha,
                        "worker_process_id": os.getpid(),
                    }
                )
                service.transition(
                    job_id,
                    JobStatus.ENVIRONMENT_ERROR,
                    reason="could not prepare isolated Git worktree",
                    payload=diagnostics,
                )
                _refresh_receipt(service, job_id)
                return 3
            job = service.get(job_id)

        if job.status in (JobStatus.PREPARING, JobStatus.PLANNING):
            plan_rc = _maybe_run_plan_stage(service, job_id)
            if plan_rc is not None:
                _refresh_receipt(service, job_id)
                return plan_rc
            job = service.get(job_id)

        blocked = _require_approved_plan(service, job)
        if blocked is not None:
            _refresh_receipt(service, job_id)
            return blocked

        attempt = service.start_attempt(
            job_id,
            worker_id=worker_id,
            session_name=session_name(job),
            metadata={
                "process_id": os.getpid(),
                "branch": job.branch,
                "worktree": job.worktree,
                "base_sha": job.base_sha,
            },
        )
        job = service.transition(job_id, JobStatus.RUNNING, reason="isolated worker attempt started")

        from .sandbox.runner import SandboxJobRunner

        runner = SandboxJobRunner(service)
        outcome = runner.run(job, attempt)
        if lease.lost.is_set():
            # Heartbeat failed mid-run: a replacement worker may own the
            # job. Do NOT checkpoint/finish/transition — that would race
            # the replacement and corrupt its state.
            service.store.append_event(
                job_id,
                "worker_lease_lost_midrun",
                reason="heartbeat lost; worker abandoning outcome",
                payload={"worker_id": worker_id, "attempt_id": attempt.id},
            )
            return 5
        if not service.store.assert_lease(job_id, worker_id):
            # Final transactional gate: the heartbeat check above can race
            # an expiry + takeover between the check and _apply_outcome.
            service.store.append_event(
                job_id,
                "worker_lease_lost_midrun",
                reason="lease lost before outcome application",
                payload={"worker_id": worker_id, "attempt_id": attempt.id},
            )
            return 5
        # Round-39 F1: worker_id threaded through — _apply_outcome now
        # enforces ownership transactionally; the assert_lease gate above
        # remains as a fast pre-check.
        return _apply_outcome(
            service, manager, job_id, attempt.id, attempt.number, outcome,
            worker_id=worker_id,
        )

    except JobStateError:
        raise
    except BaseException as exc:
        # Round-36 F3: the crash path is fenced exactly like the success
        # path — a worker that has lost its lease (heartbeat reported loss,
        # takeover acquired it, or it lapsed) must NOT mark the attempt
        # failed or transition a job that now belongs to a replacement.
        # Without this, a shutdown-time exception after lease loss flipped
        # the replacement worker's job to FAILED. Detached diagnostics
        # (events only) are still allowed.
        still_owned = False
        try:
            still_owned = service.store.assert_lease(job_id, worker_id)
        except Exception:
            still_owned = False
        # Round-40 F2: the ONLY attempt write is the atomic
        # finish_attempt_owned(target_status=FAILED) below. The previous
        # duplicate unconditioned finish already committed 'failed' before
        # the atomic call — a later status change could no longer
        # roll it back, defeating the guarantee, and every ordinary crash
        # emitted a duplicate attempt_finished event.
        if attempt is not None and still_owned:
            try:
                # Round-39/40 F2: finish the attempt AND transition to
                # FAILED in the same lease-conditioned transaction. If the
                # job already left RUNNING (e.g. cancelled), the
                # JobStateError rolls the whole transaction back, so it
                # never gets a misleading 'failed' attempt. (Reached caps
                # never land here: they pause with ``limit_reached``.)
                service.store.finish_attempt_owned(
                    attempt.id,
                    worker_id=worker_id,
                    status="failed",
                    error=str(exc),
                    target_status=JobStatus.FAILED,
                    transition_reason="isolated worker crashed",
                    transition_payload={
                        "error": str(exc),
                        "process_id": os.getpid(),
                        "exception_type": type(exc).__name__,
                    },
                )
            except Exception:
                pass
        elif attempt is None and still_owned:
            # Crash before any attempt existed (preparing/planning). Without
            # this branch nothing was recorded and the job sat in PLANNING
            # forever after the worker exited.
            try:
                if not isinstance(exc, InteractionRequired):
                    service.transition(job_id, JobStatus.FAILED, reason="worker crashed before an attempt started",
                                       payload={"error": str(exc)[:500], "exception_type": type(exc).__name__})
                else:
                    service.require_human(
                        job_id,
                        AttentionReason.PROVIDER_ERROR,
                        f"Worker stopped before implementation: {type(exc).__name__}: {str(exc)[:400]}",
                        payload={
                            "stage": "pre_attempt",
                            "exception_type": type(exc).__name__,
                            "process_id": os.getpid(),
                        },
                    )
            except Exception:
                try:
                    service.store.append_event(
                        job_id,
                        "worker_crashed",
                        reason="worker crashed before an attempt started",
                        payload={"error": str(exc)[:500], "exception_type": type(exc).__name__},
                    )
                except Exception:
                    pass
        elif attempt is not None:
            try:
                service.store.append_event(
                    job_id,
                    "worker_crash_result_discarded",
                    reason="lease lost; crash outcome not applied to superseded job",
                    payload={"attempt_id": attempt.id, "error": str(exc)[:500]},
                )
            except Exception:
                pass
        # Round-40 F4: receipt refresh is best-effort in both branches —
        # a job deleted by the new owner must not turn the clean lease-loss
        # exit into a worker crash.
        try:
            _refresh_receipt(service, job_id)
        except Exception:
            pass
        return 4


WORKER_PHASES = {
    "implementation": "mu.jobs.worker:run_job",
    "verification": "mu.jobs.verify_worker:run_verification",
    "review": "mu.jobs.review_worker:run_review",
    "publish": "mu.jobs.publish:run_publish",
}


def run_phase(phase: str, job_id: str, worker_id: str, *, lease_ttl_seconds: int = 45) -> int:
    """The single worker entry point: run one leased phase of one job."""
    import importlib

    module, _, name = WORKER_PHASES[phase].partition(":")
    return getattr(importlib.import_module(module), name)(job_id, worker_id, lease_ttl_seconds=lease_ttl_seconds)


def main(argv: Optional[list] = None, *, phase: Optional[str] = None) -> int:
    parser = argparse.ArgumentParser(description="Run one phase of a MuCLI durable engineering job")
    parser.add_argument("--phase", choices=PHASES, default=phase or "implementation")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--lease-ttl", type=int, default=45)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    return run_phase(args.phase, args.job_id, args.worker_id, lease_ttl_seconds=args.lease_ttl)


if __name__ == "__main__":
    raise SystemExit(main())
