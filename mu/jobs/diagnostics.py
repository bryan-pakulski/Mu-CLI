"""Shared diagnostics read-model for durable engineering jobs.

The three control planes consume this snapshot rather than learning how the
controller/worktree implementation stores logs or which lifecycle events are
useful for troubleshooting. Paths are never accepted from the client: the
worker log is derived from the durable job id inside the job-store directory.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from .models import JobEvent
from .service import JobService


DIAGNOSTIC_EVENT_TYPES = frozenset(
    {
        "worker_lease_acquired",
        "worker_lease_released",
        "worker_process_started",
        "worker_process_exited",
        "worker_process_terminated",
        "worker_spawn_failed",
        "worker_rejected",
        "worktree_preflight_started",
        "repository_inspected",
        "job_base_resolved",
        "worktree_inventory",
        "worktree_add_started",
        "worktree_prepare_failed",
        "worktree_ready",
        "checkpoint_failed",
        "runtime_error",
        "verification_worker_error",
        "verification_lease_expired",
    }
)


@dataclass(frozen=True)
class JobDiagnosticsSnapshot:
    job_id: str
    status: str
    attention_reason: str
    repository: str
    base_branch: str
    base_sha: str
    branch: str
    worktree: str
    worker_id: str
    heartbeat_at: Optional[float]
    lease_expires_at: Optional[float]
    diagnostics: List[Dict[str, Any]]
    latest_failure: Optional[Dict[str, Any]]
    worker_log_path: str
    worker_log_exists: bool
    worker_log_size: int
    worker_log_tail: str
    worker_log_truncated: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _is_diagnostic_event(event: JobEvent) -> bool:
    return bool(
        event.event_type in DIAGNOSTIC_EVENT_TYPES
        or (
            event.event_type == "status_changed"
            and event.to_status is not None
            and event.to_status.value in {
                "environment_error",
                "failed",
                "timed_out",
                "budget_exceeded",
                "recovering",
            }
        )
    )


def _event_failure(event: JobEvent) -> bool:
    return bool(
        event.event_type
        in {
            "worktree_prepare_failed",
            "worker_spawn_failed",
            "runtime_error",
            "verification_worker_error",
            "checkpoint_failed",
        }
        or (
            event.event_type == "status_changed"
            and event.to_status is not None
            and event.to_status.value
            in {"environment_error", "failed", "timed_out", "budget_exceeded"}
        )
    )


def _read_tail(path: str, *, max_bytes: int) -> tuple[str, int, bool]:
    try:
        size = int(os.path.getsize(path))
    except OSError:
        return "", 0, False
    take = max(1024, min(int(max_bytes), 512 * 1024))
    try:
        with open(path, "rb") as handle:
            if size > take:
                handle.seek(-take, os.SEEK_END)
            raw = handle.read(take)
    except OSError:
        return "", size, size > take
    return raw.decode("utf-8", errors="replace"), size, size > take


def build_job_diagnostics(
    service: JobService,
    job_id: str,
    *,
    event_limit: int = 200,
    log_tail_bytes: int = 64 * 1024,
) -> JobDiagnosticsSnapshot:
    job = service.get(job_id)
    events = [
        event
        for event in service.events(
            job_id,
            limit=max(1, min(int(event_limit), 1000)),
        )
        if _is_diagnostic_event(event)
    ]
    latest_failure = next(
        (event.to_dict() for event in reversed(events) if _event_failure(event)),
        None,
    )

    # Controller logs are always written alongside the active JobStore DB:
    #   <jobs-dir>/jobs.sqlite3
    #   <jobs-dir>/logs/<job-id>.log
    # Deriving this server-side prevents a client from asking the API to read
    # arbitrary filesystem paths.
    log_path = os.path.join(os.path.dirname(service.store.path), "logs", f"{job.id}.log")
    log_tail, log_size, truncated = _read_tail(
        log_path,
        max_bytes=log_tail_bytes,
    )

    return JobDiagnosticsSnapshot(
        job_id=job.id,
        status=job.status.value,
        attention_reason=job.attention_reason.value,
        repository=job.repository,
        base_branch=job.base_branch,
        base_sha=job.base_sha,
        branch=job.branch,
        worktree=job.worktree,
        worker_id=job.worker_id,
        heartbeat_at=job.heartbeat_at,
        lease_expires_at=job.lease_expires_at,
        diagnostics=[event.to_dict() for event in events],
        latest_failure=latest_failure,
        worker_log_path=log_path,
        worker_log_exists=bool(log_size or os.path.exists(log_path)),
        worker_log_size=log_size,
        worker_log_tail=log_tail,
        worker_log_truncated=truncated,
    )
