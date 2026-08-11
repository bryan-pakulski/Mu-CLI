"""Durable engineering-job orchestration core.

The job layer is intentionally control-plane neutral. GUI, TUI and mobile
consume the same JobService API and never own job lifecycle state themselves.
"""

from .models import (
    AttentionReason,
    Job,
    JobAttempt,
    JobEvent,
    JobSpec,
    JobStatus,
    TERMINAL_STATUSES,
    can_transition,
)
from .service import JobService, JobStateError, get_default_job_service
from .store import JobStore

__all__ = [
    "AttentionReason",
    "Job",
    "JobAttempt",
    "JobEvent",
    "JobService",
    "JobSpec",
    "JobStateError",
    "JobStatus",
    "JobStore",
    "TERMINAL_STATUSES",
    "can_transition",
    "get_default_job_service",
]
