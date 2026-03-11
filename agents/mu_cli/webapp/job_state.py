from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class JobStatus(str, Enum):
    QUEUED = "queued"
    PLANNING = "planning"
    AWAITING_PLAN_APPROVAL = "awaiting_plan_approval"
    RUNNING = "running"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    KILLED = "killed"


TERMINAL_STATUSES = {JobStatus.COMPLETED.value, JobStatus.FAILED.value, JobStatus.TIMED_OUT.value, JobStatus.KILLED.value}

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    JobStatus.QUEUED.value: {JobStatus.PLANNING.value, JobStatus.KILLED.value},
    JobStatus.PLANNING.value: {JobStatus.AWAITING_PLAN_APPROVAL.value, JobStatus.RUNNING.value, JobStatus.FAILED.value, JobStatus.KILLED.value},
    JobStatus.AWAITING_PLAN_APPROVAL.value: {JobStatus.RUNNING.value, JobStatus.FAILED.value, JobStatus.KILLED.value, JobStatus.TIMED_OUT.value},
    JobStatus.RUNNING.value: {JobStatus.VERIFYING.value, JobStatus.COMPLETED.value, JobStatus.FAILED.value, JobStatus.TIMED_OUT.value, JobStatus.KILLED.value},
    JobStatus.VERIFYING.value: {JobStatus.COMPLETED.value, JobStatus.FAILED.value, JobStatus.TIMED_OUT.value, JobStatus.KILLED.value},
    JobStatus.COMPLETED.value: set(),
    JobStatus.FAILED.value: set(),
    JobStatus.TIMED_OUT.value: set(),
    JobStatus.KILLED.value: set(),
}


@dataclass(slots=True)
class TransitionResult:
    ok: bool
    previous: str
    new: str
    reason: str | None = None


def transition_job_status(job: dict[str, Any], new_status: str, *, reason: str | None = None) -> TransitionResult:
    previous = str(job.get("status") or JobStatus.QUEUED.value)
    if previous == new_status:
        return TransitionResult(ok=True, previous=previous, new=new_status, reason=reason)
    if previous in TERMINAL_STATUSES:
        return TransitionResult(ok=False, previous=previous, new=new_status, reason="cannot transition from terminal status")
    allowed = ALLOWED_TRANSITIONS.get(previous, set())
    if new_status not in allowed:
        return TransitionResult(ok=False, previous=previous, new=new_status, reason=f"invalid transition {previous}->{new_status}")
    job["status"] = new_status
    transitions = job.setdefault("status_transitions", [])
    if isinstance(transitions, list):
        transitions.append({"from": previous, "to": new_status, "reason": reason})
    return TransitionResult(ok=True, previous=previous, new=new_status, reason=reason)
