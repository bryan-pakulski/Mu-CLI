"""Control-plane-neutral service for durable engineering jobs."""

from __future__ import annotations

import threading
from typing import Any, Dict, Iterable, List, Optional

from .models import AttentionReason, Job, JobAttempt, JobEvent, JobSpec, JobStatus, can_transition, coerce_status
from .store import JobStore


class JobStateError(RuntimeError):
    pass


class JobService:
    def __init__(self, store: Optional[JobStore] = None):
        self.store = store or JobStore()

    def create(self, spec: JobSpec, *, job_id: Optional[str] = None) -> Job:
        return self.store.create_job(spec, job_id=job_id)

    def create_from_payload(self, payload: Dict[str, Any]) -> Job:
        return self.create(JobSpec(
            title=str(payload.get("title") or ""),
            description=str(payload.get("description") or ""),
            repository=str(payload.get("repository") or payload.get("repo") or ""),
            base_branch=str(payload.get("base_branch") or "main"),
            base_sha=str(payload.get("base_sha") or ""),
            acceptance_criteria=list(payload.get("acceptance_criteria") or []),
            validation_commands=list(payload.get("validation_commands") or []),
            max_cost_usd=payload.get("max_cost_usd"),
            max_runtime_seconds=payload.get("max_runtime_seconds"),
            max_iterations=payload.get("max_iterations"),
            max_retries=int(payload.get("max_retries", 2)),
            max_subagents=payload.get("max_subagents"),
            environment=dict(payload.get("environment") or {}),
            execution=dict(payload.get("execution") or {}),
            metadata=dict(payload.get("metadata") or {}),
        ))

    def get(self, job_id: str) -> Job:
        return self.store.get_job(job_id)

    def list(self, *, statuses: Optional[Iterable[JobStatus | str]] = None, limit: int = 200) -> List[Job]:
        return self.store.list_jobs(statuses=statuses, limit=limit)

    def events(self, job_id: str, *, after_id: int = 0, limit: int = 500) -> List[JobEvent]:
        self.get(job_id)
        return self.store.list_events(job_id, after_id=after_id, limit=limit)

    def attempts(self, job_id: str) -> List[JobAttempt]:
        self.get(job_id)
        return self.store.list_attempts(job_id)

    def transition(
        self,
        job_id: str,
        target: JobStatus | str,
        *,
        reason: str = "",
        payload: Optional[Dict[str, Any]] = None,
        attention_reason: AttentionReason | str = AttentionReason.NONE,
        attention_detail: str = "",
        expected_version: Optional[int] = None,
    ) -> Job:
        current = self.get(job_id)
        target_status = coerce_status(target)
        if current.status == target_status:
            return current
        if not can_transition(current.status, target_status):
            raise JobStateError(
                f"cannot transition job {job_id} from {current.status.value} to {target_status.value}"
            )
        if target_status == JobStatus.NEEDS_HUMAN:
            attention = attention_reason if isinstance(attention_reason, AttentionReason) else AttentionReason(str(attention_reason or ""))
            if attention == AttentionReason.NONE:
                raise JobStateError("needs_human requires an attention_reason")
        else:
            attention_reason = AttentionReason.NONE
            attention_detail = ""
        return self.store.transition(
            job_id,
            target_status,
            reason=reason,
            payload=payload,
            attention_reason=attention_reason,
            attention_detail=attention_detail,
            expected_version=expected_version,
        )

    def cancel(self, job_id: str, *, reason: str = "cancelled by user") -> Job:
        current = self.get(job_id)
        if current.status == JobStatus.CANCELLED:
            return current
        return self.transition(job_id, JobStatus.CANCELLED, reason=reason)

    def require_human(
        self,
        job_id: str,
        reason: AttentionReason | str,
        detail: str,
        *,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Job:
        return self.transition(
            job_id,
            JobStatus.NEEDS_HUMAN,
            reason="human attention required",
            payload=payload,
            attention_reason=reason,
            attention_detail=detail,
        )

    def resume(self, job_id: str, *, detail: str = "") -> Job:
        current = self.get(job_id)
        if current.status != JobStatus.NEEDS_HUMAN:
            raise JobStateError("only a needs_human job can be resumed")
        self.store.append_event(job_id, "human_response", payload={"detail": str(detail or "")})
        # A response removes the gate, but no worker owns the job yet. Requeue it
        # so the scheduler can create a new attempt against the same durable
        # job session rather than publishing a false RUNNING state.
        return self.transition(job_id, JobStatus.QUEUED, reason="human response received; requeued")

    def retry(self, job_id: str, *, reason: str = "retry requested") -> Job:
        current = self.get(job_id)
        if current.status not in {
            JobStatus.FAILED, JobStatus.TIMED_OUT, JobStatus.BUDGET_EXCEEDED,
            JobStatus.ENVIRONMENT_ERROR, JobStatus.NEEDS_HUMAN,
        }:
            raise JobStateError(f"job {job_id} is not retryable from {current.status.value}")
        return self.transition(job_id, JobStatus.QUEUED, reason=reason)

    def acquire(self, job_id: str, worker_id: str, *, ttl_seconds: int = 60) -> bool:
        if not worker_id:
            raise ValueError("worker_id is required")
        return self.store.acquire_lease(job_id, worker_id, ttl_seconds=ttl_seconds)

    def heartbeat(self, job_id: str, worker_id: str, *, ttl_seconds: int = 60) -> bool:
        return self.store.heartbeat(job_id, worker_id, ttl_seconds=ttl_seconds)

    def release(self, job_id: str, worker_id: str, *, reason: str = "") -> bool:
        return self.store.release_lease(job_id, worker_id, reason=reason)

    def start_attempt(self, job_id: str, *, worker_id: str = "", session_name: str = "", metadata: Optional[Dict[str, Any]] = None) -> JobAttempt:
        return self.store.start_attempt(job_id, worker_id=worker_id, session_name=session_name, metadata=metadata)

    def finish_attempt(self, attempt_id: str, *, status: str, error: str = "", cost_usd: float = 0.0, metadata: Optional[Dict[str, Any]] = None) -> JobAttempt:
        return self.store.finish_attempt(attempt_id, status=status, error=error, cost_usd=cost_usd, metadata=metadata)

    def recover_expired_leases(self) -> List[Job]:
        recovered: List[Job] = []
        for job in self.store.expired_leases():
            worker = job.worker_id
            if job.status in {JobStatus.PREPARING, JobStatus.RUNNING, JobStatus.VERIFYING}:
                try:
                    recovered.append(self.transition(
                        job.id,
                        JobStatus.RECOVERING,
                        reason="worker lease expired",
                        payload={"worker_id": worker},
                    ))
                except JobStateError:
                    pass
            self.store.release_lease(job.id, worker, reason="expired lease recovered")
        return recovered


_default_lock = threading.Lock()
_default_service: Optional[JobService] = None


def get_default_job_service() -> JobService:
    global _default_service
    with _default_lock:
        if _default_service is None:
            _default_service = JobService()
        return _default_service
