"""The one job transition model (guide §3.1).

Two views of the same lifecycle live here, and only here:

* ``JobState`` / ``CoreState`` — the authoritative pipeline state (plan →
  implement → checks → review → push), with ``TRANSITIONS`` as its legal moves
  and ``transition()`` as the pure transition function.
* the stored ``status`` column (``JobStatus`` values in ``models.py``) — kept
  for compatibility because the DB, the REST/SSE API, the web UI, the terminal
  and the mobile client all read it. ``STATUS_TRANSITIONS`` is its legal-move
  table; ``advance()`` is the explicit, total mapping that derives the next
  ``CoreState`` from the previous one and the status being written. It reads
  no event history, so the derived state is a pure function of its inputs.

``from_legacy`` is migration-only: it maps a row written before the core
columns existed (schema < v3) and old events without a ``core_state`` snapshot.
Live writes never use it.
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
    # PUSHED -> PUSHING: re-push after the host PR was closed unmerged (opens a new PR).
    _S.PUSHED: frozenset({_S.DONE, _S.IMPLEMENTING, _S.PUSHING}),
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


# ------------------------------------------------------- stored status table
# Status values (``JobStatus``) as plain strings so this module stays free of
# model imports. ``timed_out`` and ``budget_exceeded`` are no longer written by
# any code path: a reached cost/token/time/iteration cap pauses the stage with
# ``needs_human`` + attention ``limit_reached`` (core NEEDS_INPUT, paused_from =
# the stage) and ``raiseLimit`` resumes it. Both stay legal stored values (as
# sources only) because old rows and every reader (web, terminal, mobile types)
# still recognise them.
QUEUED, PREPARING, RUNNING, NEEDS_HUMAN, VERIFYING = "queued", "preparing", "running", "needs_human", "verifying"
PLANNING_S, REVIEWING_S, PUBLISHING = "planning", "reviewing", "publishing"
READY_FOR_REVIEW, RECOVERING, CONFLICTED = "ready_for_review", "recovering", "conflicted"
FAILED_S, TIMED_OUT, BUDGET_EXCEEDED, ENVIRONMENT_ERROR = "failed", "timed_out", "budget_exceeded", "environment_error"
CANCELLED_S, MERGED = "cancelled", "merged"
# NEEDS_HUMAN attention reason of a reached cap (``mu/jobs/limits.py``).
LIMIT_REACHED = "limit_reached"

_RETRY_TARGETS = frozenset({QUEUED, PLANNING_S, VERIFYING, REVIEWING_S, PUBLISHING, CANCELLED_S})

STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    QUEUED: frozenset({PREPARING, CANCELLED_S}),
    # Every worker-active status can pause on a human (including a reached cap).
    PREPARING: frozenset({PLANNING_S, RUNNING, NEEDS_HUMAN, RECOVERING, ENVIRONMENT_ERROR, FAILED_S,
                          CANCELLED_S}),
    # RUNNING -> QUEUED is the unattended transient-failure retry (backoff).
    RUNNING: frozenset({NEEDS_HUMAN, VERIFYING, RECOVERING, FAILED_S, ENVIRONMENT_ERROR, CANCELLED_S, QUEUED}),
    NEEDS_HUMAN: frozenset({PLANNING_S, REVIEWING_S, PUBLISHING, VERIFYING, RUNNING, QUEUED, FAILED_S,
                            CANCELLED_S}),
    VERIFYING: frozenset({REVIEWING_S, READY_FOR_REVIEW, QUEUED, RUNNING, NEEDS_HUMAN, RECOVERING,
                          CONFLICTED, FAILED_S, CANCELLED_S}),
    # A pushed job keeps its branch; more agent work requeues it. NEEDS_HUMAN:
    # the host PR was closed without merging (attention ``pr_closed``).
    READY_FOR_REVIEW: frozenset({VERIFYING, QUEUED, RUNNING, CONFLICTED, MERGED, NEEDS_HUMAN, CANCELLED_S}),
    # RECOVERING -> ENVIRONMENT_ERROR: the recovered run could not re-prepare its worktree.
    RECOVERING: frozenset({PLANNING_S, REVIEWING_S, PUBLISHING, RUNNING, NEEDS_HUMAN, FAILED_S,
                           CANCELLED_S, ENVIRONMENT_ERROR}),
    # CONFLICTED -> MERGED: the PR host merged the branch (e.g. squash) despite local drift.
    CONFLICTED: frozenset({QUEUED, RUNNING, NEEDS_HUMAN, MERGED, FAILED_S, CANCELLED_S}),
    # Staged pipeline: PREPARING -> PLANNING -> RUNNING -> VERIFYING -> REVIEWING
    # -> (human) PUBLISHING -> READY_FOR_REVIEW.
    PLANNING_S: frozenset({RUNNING, NEEDS_HUMAN, RECOVERING, QUEUED, FAILED_S, CANCELLED_S, ENVIRONMENT_ERROR}),
    REVIEWING_S: frozenset({RUNNING, PUBLISHING, READY_FOR_REVIEW, NEEDS_HUMAN, RECOVERING, QUEUED,
                            FAILED_S, CANCELLED_S}),
    PUBLISHING: frozenset({READY_FOR_REVIEW, NEEDS_HUMAN, RECOVERING, FAILED_S, CANCELLED_S}),
    FAILED_S: _RETRY_TARGETS,
    # Old rows only (never written now): still retryable by a human.
    TIMED_OUT: _RETRY_TARGETS,
    BUDGET_EXCEEDED: _RETRY_TARGETS,
    ENVIRONMENT_ERROR: _RETRY_TARGETS,
    CANCELLED_S: frozenset(),
    MERGED: frozenset(),
}

TERMINAL_STATUSES = frozenset({CANCELLED_S, MERGED})
FAILURE_STATUSES = frozenset({FAILED_S, TIMED_OUT, BUDGET_EXCEEDED, ENVIRONMENT_ERROR})
# Statuses a human ``retry`` may restart from.
RETRYABLE_STATUSES = FAILURE_STATUSES | {NEEDS_HUMAN}
# Statuses that wait on a human (attention push, boards).
ATTENTION_STATUSES = frozenset({NEEDS_HUMAN, CONFLICTED})
# Worker phase the controller launches for each schedulable status.
STATUS_PHASE: dict[str, str] = {
    QUEUED: "implementation",
    PLANNING_S: "implementation",
    RECOVERING: "implementation",
    VERIFYING: "verification",
    REVIEWING_S: "review",
    PUBLISHING: "publish",
}
SCHEDULABLE_STATUSES = tuple(STATUS_PHASE)
PHASES = ("implementation", "verification", "review", "publish")
# Lease expiry in these statuses re-dispatches the same stage (no status change).
STAGE_RETRY_STATUSES = frozenset({PLANNING_S, VERIFYING, REVIEWING_S, PUBLISHING})


def _value(status: Any) -> str:
    return str(getattr(status, "value", status) or "")


def can_transition_status(current: Any, target: Any) -> bool:
    return _value(target) in STATUS_TRANSITIONS[_value(current)]


def phase_for_status(status: Any) -> str:
    return STATUS_PHASE.get(_value(status), "implementation")


# ------------------------------------------------------- status -> core state
# A write of one of these statuses puts the pipeline in a fixed core state.
STAGE_STATUS_STATE: dict[str, JobState] = {
    PLANNING_S: JobState.PLANNING,
    RUNNING: JobState.IMPLEMENTING,
    VERIFYING: JobState.CHECKING,
    REVIEWING_S: JobState.REVIEWING,
    PUBLISHING: JobState.PUSHING,
    READY_FOR_REVIEW: JobState.PUSHED,
    MERGED: JobState.DONE,
    CANCELLED_S: JobState.CANCELLED,
}
# Scheduling statuses: the worker picks the job up again.
SCHEDULING_STATUSES = frozenset({QUEUED, PREPARING, RECOVERING})
# NEEDS_HUMAN attention reasons that are pipeline gates rather than pauses.
GATE_REASONS: dict[str, JobState] = {
    "plan_review": JobState.PLAN_REVIEW,
    "review_done": JobState.REVIEW_DONE,
    "review_stalled": JobState.REVIEW_DONE,  # old saved jobs only
    # The host PR was closed unmerged: still pushed, waiting on a human
    # decision (re-push opens a new PR, or mark done).
    "pr_closed": JobState.PUSHED,
}
# Where a scheduling round trip lands, by the prior core state. ``None`` = the
# paused/failed origin (resume and retry return to the step they left).
# ``_FRESH`` = new work: PROVISIONING for queued/preparing, IMPLEMENTING for a
# recovered implementation run.
_ORIGIN, _FRESH = None, "fresh"
RESCHEDULE: dict[JobState, Any] = {
    JobState.NEEDS_INPUT: _ORIGIN,
    JobState.FAILED: _ORIGIN,
    # Leaving a human gate (approved plan, re-prompt, more work on a pushed
    # branch) is a fresh implementation run with a fresh fix budget.
    JobState.PLAN_REVIEW: JobState.IMPLEMENTING,
    JobState.REVIEW_DONE: JobState.IMPLEMENTING,
    JobState.PUSHED: JobState.IMPLEMENTING,
    # Fix loop / transient retry / crash recovery of an implementation run.
    JobState.IMPLEMENTING: JobState.IMPLEMENTING,
    JobState.CHECKING: JobState.IMPLEMENTING,
    JobState.SELECTED: _FRESH,
    JobState.PROVISIONING: _FRESH,
    JobState.PLANNING: _FRESH,
    JobState.REVIEWING: _FRESH,
    JobState.PUSHING: _FRESH,
    JobState.DONE: _FRESH,
    JobState.CANCELLED: _FRESH,
}
_RESETS_FIX_BUDGET = frozenset({JobState.PLAN_REVIEW, JobState.REVIEW_DONE, JobState.PUSHED})


def origin_of(core: CoreState) -> JobState:
    """The pipeline step a paused/failed job belongs to (total: falls back to
    IMPLEMENTING / PROVISIONING when an old row lost its origin)."""
    if core.state == JobState.NEEDS_INPUT:
        return core.paused_from or JobState.IMPLEMENTING
    if core.state == JobState.FAILED:
        return core.failed_from or JobState.PROVISIONING
    return core.state


def advance(prior: CoreState, status: Any, attention: Any = "", *, stage: str = "") -> CoreState:
    """Next ``CoreState`` for a write of ``status`` (+ attention reason).

    Total over every (prior state, status, attention) triple and free of event
    history. A ``failed`` write with ``stage == "provisioning"`` marks a failure
    of the sandbox itself, which a retry must provision again. A
    ``needs_human``/``approval_required`` write is always a tool-approval pause
    (the plan gate uses ``plan_review``).
    """
    status, reason = _value(status), _value(attention)
    fixes = prior.fix_attempts
    if status in STAGE_STATUS_STATE:
        return CoreState(STAGE_STATUS_STATE[status], fix_attempts=fixes)
    if status in SCHEDULING_STATUSES:
        landing = RESCHEDULE[prior.state]
        gate = prior.state
        if landing is _ORIGIN:
            origin = origin_of(prior)
            if prior.state != JobState.NEEDS_INPUT or origin not in _RESETS_FIX_BUDGET:
                return CoreState(origin, fix_attempts=fixes)
            # Paused while parked at a human gate (e.g. a pushed branch
            # that went conflicted): scheduling it is new work on that gate,
            # exactly like leaving the gate directly — never back to the gate
            # itself (that would re-fire its writeback while queued).
            gate, landing = origin, RESCHEDULE[origin]
        if landing == _FRESH:
            landing = JobState.IMPLEMENTING if status == RECOVERING else JobState.PROVISIONING
        return CoreState(landing, fix_attempts=0 if gate in _RESETS_FIX_BUDGET else fixes)
    if status == NEEDS_HUMAN and reason in GATE_REASONS:
        return CoreState(GATE_REASONS[reason], fix_attempts=fixes)
    if status in (NEEDS_HUMAN, CONFLICTED):
        # Every other pause — a question, a tool approval and a reached cap
        # (``limit_reached``) — keeps the stage it paused in, so ``resume`` /
        # ``raiseLimit`` return the job to exactly that stage.
        return CoreState(JobState.NEEDS_INPUT, paused_from=origin_of(prior), fix_attempts=fixes)
    if status in FAILURE_STATUSES:
        origin = JobState.PROVISIONING if status == FAILED_S and stage == "provisioning" else origin_of(prior)
        return CoreState(JobState.FAILED, failed_from=origin, fix_attempts=fixes)
    raise ValueError(f"unknown job status {status!r}")


def initial_core() -> CoreState:
    """Core state of a freshly created (QUEUED) job."""
    return CoreState(JobState.PROVISIONING)


def to_legacy_status(core: CoreState) -> str:
    """Status written for a human/command-driven core transition."""
    s = core.state
    return {
        JobState.SELECTED: QUEUED, JobState.PROVISIONING: PREPARING,
        JobState.PLANNING: PLANNING_S, JobState.PLAN_REVIEW: NEEDS_HUMAN,
        JobState.IMPLEMENTING: RUNNING, JobState.CHECKING: VERIFYING,
        JobState.REVIEWING: REVIEWING_S, JobState.REVIEW_DONE: NEEDS_HUMAN,
        JobState.PUSHING: PUBLISHING, JobState.PUSHED: READY_FOR_REVIEW,
        JobState.DONE: MERGED, JobState.NEEDS_INPUT: NEEDS_HUMAN,
        JobState.FAILED: FAILED_S, JobState.CANCELLED: CANCELLED_S,
    }[s]


# ------------------------------------------------------------ migration only
_ACTIVE_LEGACY = {
    QUEUED: JobState.PROVISIONING,
    PREPARING: JobState.PROVISIONING,
    PLANNING_S: JobState.PLANNING,
    RUNNING: JobState.IMPLEMENTING,
    RECOVERING: JobState.IMPLEMENTING,
    VERIFYING: JobState.CHECKING,
    REVIEWING_S: JobState.REVIEWING,
    PUBLISHING: JobState.PUSHING,
}
# Pre-v3 rows: ``approval_required`` was the plan gate's reason.
PLAN_REVIEW_REASONS = frozenset({"approval_required", "plan_review"})
REVIEW_DONE_REASONS = frozenset({"review_stalled", "review_done"})


def _origin(history: Iterable[str]) -> Optional[JobState]:
    """Most recent active state from newest-first legacy status history."""
    for status in history:
        mapped = _ACTIVE_LEGACY.get(str(status or ""))
        if mapped is not None:
            return mapped
    return None


def from_legacy(status: str, attention_reason: str = "", *, history: Iterable[str] = (),
                fix_attempts: int = 0) -> CoreState:
    """Migration-only map of a pre-v3 ``JobStatus`` (+ attention) onto the core model.

    ``history`` is legacy statuses the row passed through, newest first (the
    ``from_status`` of its ``status_changed`` events): the only record of
    ``paused_from`` / ``failed_from`` an old row has. Live transitions use
    ``advance()``.
    """
    status, reason = _value(status), _value(attention_reason)
    if status in _ACTIVE_LEGACY:
        return CoreState(_ACTIVE_LEGACY[status], fix_attempts=fix_attempts)
    if status == NEEDS_HUMAN:
        if reason in PLAN_REVIEW_REASONS:
            return CoreState(JobState.PLAN_REVIEW, fix_attempts=fix_attempts)
        if reason in REVIEW_DONE_REASONS:
            return CoreState(JobState.REVIEW_DONE, fix_attempts=fix_attempts)
        return CoreState(JobState.NEEDS_INPUT, paused_from=_origin(history) or JobState.IMPLEMENTING,
                         fix_attempts=fix_attempts)
    if status == CONFLICTED:
        return CoreState(JobState.NEEDS_INPUT, paused_from=_origin(history) or JobState.IMPLEMENTING,
                         fix_attempts=fix_attempts)
    if status == READY_FOR_REVIEW:
        return CoreState(JobState.PUSHED, fix_attempts=fix_attempts)
    if status == MERGED:
        return CoreState(JobState.DONE, fix_attempts=fix_attempts)
    if status in FAILURE_STATUSES:
        return CoreState(JobState.FAILED, failed_from=_origin(history) or JobState.PROVISIONING,
                         fix_attempts=fix_attempts)
    if status in {CANCELLED_S, "archived"}:
        return CoreState(JobState.CANCELLED, fix_attempts=fix_attempts)
    raise ValueError(f"unknown legacy status {status!r}")
