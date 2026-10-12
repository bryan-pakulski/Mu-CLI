"""Core job state machine (guide §3.1).

``JobState`` is the authoritative lifecycle. The legacy ``JobStatus`` stays
readable during the migration; ``from_legacy`` maps old status/attention pairs
onto the core model so existing rows keep loading.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Iterable, Optional


class JobState(str, Enum):
    SELECTED = "selected"
    PROVISIONING = "provisioning"
    PLANNING = "planning"
    PLAN_REVIEW = "plan_review"
    IMPLEMENTING = "implementing"
    CHECKING = "checking"
    REVIEWING = "reviewing"
    REVIEW_DONE = "review_done"
    PUSHING = "pushing"
    PUSHED = "pushed"
    DONE = "done"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL = frozenset({JobState.DONE, JobState.CANCELLED})
# States in which a stage (agent, checks, git) is actively running.
ACTIVE = frozenset({
    JobState.PROVISIONING, JobState.PLANNING, JobState.IMPLEMENTING,
    JobState.CHECKING, JobState.REVIEWING, JobState.PUSHING,
})
# States waiting on a human.
WAITING = frozenset({JobState.PLAN_REVIEW, JobState.REVIEW_DONE, JobState.NEEDS_INPUT})
AGENT_STAGES = frozenset({JobState.PLANNING, JobState.IMPLEMENTING, JobState.REVIEWING})

_S = JobState
TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    _S.SELECTED: frozenset({_S.PROVISIONING}),
    _S.PROVISIONING: frozenset({_S.PLANNING, _S.IMPLEMENTING}),
    _S.PLANNING: frozenset({_S.PLAN_REVIEW, _S.IMPLEMENTING}),
    _S.PLAN_REVIEW: frozenset({_S.IMPLEMENTING, _S.PLANNING}),
    _S.IMPLEMENTING: frozenset({_S.CHECKING}),
    _S.CHECKING: frozenset({_S.IMPLEMENTING, _S.REVIEWING}),
    _S.REVIEWING: frozenset({_S.REVIEW_DONE}),
    _S.REVIEW_DONE: frozenset({_S.PUSHING, _S.IMPLEMENTING, _S.CHECKING}),
    _S.PUSHING: frozenset({_S.PUSHED}),
    _S.PUSHED: frozenset({_S.DONE, _S.IMPLEMENTING}),
    _S.DONE: frozenset(),
    _S.NEEDS_INPUT: frozenset(),  # resume -> paused_from (handled specially)
    _S.FAILED: frozenset(),       # retry -> failed_from (handled specially)
    _S.CANCELLED: frozenset(),
}


class TransitionError(ValueError):
    pass


def coerce_state(value: "JobState | str | None") -> Optional[JobState]:
    if value in (None, ""):
        return None
    return value if isinstance(value, JobState) else JobState(str(value))


@dataclass(frozen=True)
class CoreState:
    state: JobState
    paused_from: Optional[JobState] = None
    failed_from: Optional[JobState] = None
    fix_attempts: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "paused_from": self.paused_from.value if self.paused_from else None,
            "failed_from": self.failed_from.value if self.failed_from else None,
            "fix_attempts": int(self.fix_attempts),
        }


def can_transition(current: CoreState, target: JobState) -> bool:
    try:
        transition(current, target)
        return True
    except TransitionError:
        return False


def transition(current: CoreState, target: "JobState | str", *, max_fix_attempts: Optional[int] = None) -> CoreState:
    """Pure transition. Returns the new core record or raises TransitionError."""
    target = coerce_state(target)
    src = current.state
    if src in TERMINAL:
        raise TransitionError(f"{src.value} is terminal")
    if target == JobState.CANCELLED:
        return CoreState(JobState.CANCELLED, None, None, current.fix_attempts)
    if target == JobState.NEEDS_INPUT:
        if src not in ACTIVE:
            raise TransitionError(f"cannot pause from {src.value}")
        return replace(current, state=JobState.NEEDS_INPUT, paused_from=src)
    if target == JobState.FAILED:
        if src not in ACTIVE and src not in WAITING:
            raise TransitionError(f"cannot fail from {src.value}")
        origin = current.paused_from if src == JobState.NEEDS_INPUT else src
        return replace(current, state=JobState.FAILED, failed_from=origin, paused_from=None)
    if src == JobState.NEEDS_INPUT:
        if target != current.paused_from:
            raise TransitionError(f"resume must return to {current.paused_from.value if current.paused_from else '?'}")
        return replace(current, state=target, paused_from=None)
    if src == JobState.FAILED:
        if target != current.failed_from:
            raise TransitionError(f"retry must return to {current.failed_from.value if current.failed_from else '?'}")
        return replace(current, state=target, failed_from=None)
    if target not in TRANSITIONS[src]:
        raise TransitionError(f"cannot transition {src.value} -> {target.value}")
    fix_attempts = current.fix_attempts
    if src == JobState.CHECKING and target == JobState.IMPLEMENTING:
        if max_fix_attempts is not None and fix_attempts >= max_fix_attempts:
            raise TransitionError("fix attempt limit reached")
        fix_attempts += 1
    elif target == JobState.IMPLEMENTING and src in {JobState.PLAN_REVIEW, JobState.PROVISIONING,
                                                      JobState.PLANNING, JobState.REVIEW_DONE, JobState.PUSHED}:
        fix_attempts = 0  # a fresh implement run gets a fresh fix budget
    return CoreState(target, None, None, fix_attempts)


def effective_state(core: CoreState) -> JobState:
    """The state whose pipeline step the job is "in" (resolves pause/failure)."""
    if core.state == JobState.NEEDS_INPUT and core.paused_from:
        return core.paused_from
    if core.state == JobState.FAILED and core.failed_from:
        return core.failed_from
    return core.state


# ---------------------------------------------------------------- legacy map
_ACTIVE_LEGACY = {
    "queued": JobState.PROVISIONING,
    "preparing": JobState.PROVISIONING,
    "planning": JobState.PLANNING,
    "running": JobState.IMPLEMENTING,
    "recovering": JobState.IMPLEMENTING,
    "verifying": JobState.CHECKING,
    "reviewing": JobState.REVIEWING,
    "publishing": JobState.PUSHING,
}
_FAILED_LEGACY = {"failed", "timed_out", "budget_exceeded", "environment_error"}
PLAN_REVIEW_REASONS = frozenset({"approval_required", "plan_review"})
REVIEW_DONE_REASONS = frozenset({"review_stalled"})


def legacy_active_state(status: str) -> Optional[JobState]:
    return _ACTIVE_LEGACY.get(str(status or ""))


def _origin(history: Iterable[str]) -> Optional[JobState]:
    """Most recent active state from newest-first legacy status history."""
    for status in history:
        mapped = legacy_active_state(status)
        if mapped is not None:
            return mapped
    return None


def from_legacy(status: str, attention_reason: str = "", *, history: Iterable[str] = (),
                fix_attempts: int = 0) -> CoreState:
    """Map a legacy ``JobStatus`` (+ attention) onto the core model (§3.1 table).

    ``history`` is legacy statuses the job passed through, newest first
    (usually the ``from_status`` of recent ``status_changed`` events); it
    supplies ``paused_from`` / ``failed_from``.
    """
    status = str(getattr(status, "value", status) or "")
    reason = str(getattr(attention_reason, "value", attention_reason) or "")
    if status in _ACTIVE_LEGACY:
        return CoreState(_ACTIVE_LEGACY[status], fix_attempts=fix_attempts)
    if status == "needs_human":
        if reason in PLAN_REVIEW_REASONS:
            return CoreState(JobState.PLAN_REVIEW, fix_attempts=fix_attempts)
        if reason in REVIEW_DONE_REASONS:
            return CoreState(JobState.REVIEW_DONE, fix_attempts=fix_attempts)
        return CoreState(JobState.NEEDS_INPUT, paused_from=_origin(history) or JobState.IMPLEMENTING,
                         fix_attempts=fix_attempts)
    if status == "conflicted":
        return CoreState(JobState.NEEDS_INPUT, paused_from=_origin(history) or JobState.IMPLEMENTING,
                         fix_attempts=fix_attempts)
    if status == "ready_for_review":
        return CoreState(JobState.PUSHED, fix_attempts=fix_attempts)
    if status == "merged":
        return CoreState(JobState.DONE, fix_attempts=fix_attempts)
    if status in _FAILED_LEGACY:
        return CoreState(JobState.FAILED, failed_from=_origin(history) or JobState.PROVISIONING,
                         fix_attempts=fix_attempts)
    if status in {"cancelled", "archived"}:
        return CoreState(JobState.CANCELLED, fix_attempts=fix_attempts)
    raise ValueError(f"unknown legacy status {status!r}")


def to_legacy_status(core: CoreState) -> str:
    """Read-only compatibility projection core -> legacy ``JobStatus`` value."""
    s = core.state
    return {
        JobState.SELECTED: "queued", JobState.PROVISIONING: "preparing",
        JobState.PLANNING: "planning", JobState.PLAN_REVIEW: "needs_human",
        JobState.IMPLEMENTING: "running", JobState.CHECKING: "verifying",
        JobState.REVIEWING: "reviewing", JobState.REVIEW_DONE: "needs_human",
        JobState.PUSHING: "publishing", JobState.PUSHED: "ready_for_review",
        JobState.DONE: "merged", JobState.NEEDS_INPUT: "needs_human",
        JobState.FAILED: "failed", JobState.CANCELLED: "cancelled",
    }[s]
