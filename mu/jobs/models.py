"""Durable engineering-job domain model and explicit lifecycle state machine."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional

from .core import states as _states


class JobStatus(str, Enum):
    """The stored ``status`` column. The pipeline state is ``core.states.JobState``,
    derived from status writes by ``core.states.advance``. Every value stays:
    the DB, REST/SSE API, web UI, terminal and mobile client read them, and old
    rows carry them. No code path writes ``TIMED_OUT`` or ``BUDGET_EXCEEDED``
    any more (a reached cap pauses with attention ``limit_reached``); both
    remain readable for old rows."""

    QUEUED = "queued"
    PREPARING = "preparing"
    RUNNING = "running"
    NEEDS_HUMAN = "needs_human"
    VERIFYING = "verifying"
    PLANNING = "planning"
    REVIEWING = "reviewing"
    PUBLISHING = "publishing"
    READY_FOR_REVIEW = "ready_for_review"
    RECOVERING = "recovering"
    CONFLICTED = "conflicted"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    BUDGET_EXCEEDED = "budget_exceeded"
    ENVIRONMENT_ERROR = "environment_error"
    CANCELLED = "cancelled"
    MERGED = "merged"


class AttentionReason(str, Enum):
    NONE = ""
    QUESTION = "question"
    APPROVAL_REQUIRED = "approval_required"
    AMBIGUOUS_REQUIREMENT = "ambiguous_requirement"
    SECRET_REQUIRED = "secret_required"
    MERGE_CONFLICT = "merge_conflict"
    BUDGET_EXCEEDED = "budget_exceeded"
    ENVIRONMENT_FAILURE = "environment_failure"
    VERIFICATION_REQUIRED = "verification_required"
    TEST_FAILURE = "test_failure"
    UNSAFE_ACTION = "unsafe_action"
    WORKER_LOST = "worker_lost"
    REVIEW_STALLED = "review_stalled"  # old saved jobs only
    REVIEW_DONE = "review_done"
    NO_VALIDATION = "no_validation"
    PUBLISH_FAILED = "publish_failed"
    PROVIDER_ERROR = "provider_error"
    MODELS_UNSET = "models_unset"
    PLAN_REVIEW = "plan_review"
    PR_CLOSED = "pr_closed"  # host PR closed without merging (pushed job waits on a human)
    # A cost, token, time or iteration cap was reached: the stage paused resumably and the
    # human approves more (raiseLimit) or cancels. Detail in metadata.limit_reached.
    LIMIT_REACHED = "limit_reached"


# The legal status moves live in mu/jobs/core/states.py (the one transition
# model); these enum-typed views are kept for existing importers.
TERMINAL_STATUSES = frozenset(JobStatus(v) for v in _states.TERMINAL_STATUSES)

ALLOWED_TRANSITIONS = {
    JobStatus(current): {JobStatus(t) for t in targets}
    for current, targets in _states.STATUS_TRANSITIONS.items()
}


def coerce_status(value: JobStatus | str) -> JobStatus:
    return value if isinstance(value, JobStatus) else JobStatus(str(value))


def can_transition(current: JobStatus | str, target: JobStatus | str) -> bool:
    return _states.can_transition_status(coerce_status(current), coerce_status(target))


def normalize_execution(value: Dict[str, Any] | None) -> Dict[str, Any]:
    """Normalize the reproducible agent execution policy stored with a job."""
    raw = dict(value or {})
    execution = {
        "provider": str(raw.get("provider") or "").strip(),
        "model": str(raw.get("model") or "").strip(),
        "agent_mode": str(raw.get("agent_mode") or "default").strip() or "default",
        "session_type": str(raw.get("session_type") or "workspace").strip().lower() or "workspace",
        "auto_approve_writes": bool(raw.get("auto_approve_writes", False)),
    }
    if execution["session_type"] not in {"chat", "workspace", "container"}:
        raise ValueError("execution.session_type must be chat, workspace, or container")
    for key, item in raw.items():
        if key not in execution:
            execution[str(key)] = item
    execution["stages"] = normalize_stages(raw.get("stages"))
    execution.pop("max_review_rounds", None)  # retired automatic review-loop setting
    return execution


STAGES = ("plan", "implement", "review")
DEFAULT_JOB_MAX_ITERATIONS = 1000


def stage_iteration_limit(job: "Job", stage: str) -> int:
    """Job stages get a generous default independent of interactive sessions."""
    limit = job.max_iterations
    if stage in ("plan", "review"):
        limit = (job.execution or {}).get("stage_max_iterations") or limit
    if not limit:
        # Job settings: per-stage limit, then the shared default (both editable in Settings).
        try:
            from .job_settings import load_settings, rule
            settings = load_settings()
            limit = rule(f"limits.stage_max_iterations.{stage}", settings) or rule("limits.max_iterations", settings)
        except (KeyError, OSError, ValueError):
            limit = None
    return int(limit or DEFAULT_JOB_MAX_ITERATIONS)


def normalize_stages(value: Any) -> Dict[str, Dict[str, str]]:
    """Normalize ``execution.stages`` to ``{stage: {provider, model}}``.

    Unknown stage names are rejected so a typo cannot silently fall back to
    the implementation model for an expensive planner/reviewer slot.
    """
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise ValueError("execution.stages must be an object keyed by plan/implement/review")
    stages: Dict[str, Dict[str, str]] = {}
    for name, cfg in value.items():
        key = str(name or "").strip().lower()
        if key not in STAGES:
            raise ValueError(f"unknown execution stage '{name}' (expected plan, implement, review)")
        if isinstance(cfg, str):
            cfg = {"model": cfg}
        if not isinstance(cfg, dict):
            raise ValueError(f"execution.stages.{key} must be an object with provider/model")
        entry = {
            "provider": str(cfg.get("provider") or "").strip(),
            "model": str(cfg.get("model") or "").strip(),
        }
        if entry["provider"] or entry["model"]:
            stages[key] = entry
    return stages


def resolve_stage_model(
    execution: Dict[str, Any] | None,
    stage: str,
    *,
    session_provider: str = "",
    session_model: str = "",
) -> Dict[str, str]:
    """Pick provider/model for a pipeline stage.

    Precedence per field: ``execution.stages[stage]`` -> ``execution`` job
    default -> the submitting session's provider/model.
    """
    key = str(stage or "").strip().lower()
    if key not in STAGES:
        raise ValueError(f"unknown execution stage '{stage}'")
    exe = dict(execution or {})
    cfg = dict((exe.get("stages") or {}).get(key) or {})
    if cfg.get("model") or exe.get("model"):
        provider = cfg.get("provider") or exe.get("provider") or session_provider or ""
        model = cfg.get("model") or exe.get("model") or ""
        source = "stage" if cfg.get("model") else "job"
        return {"stage": key, "provider": str(provider), "model": str(model), "source": source}
    if session_model:
        return {"stage": key, "provider": str(session_provider or ""), "model": str(session_model),
                "source": "session"}
    # Fallback: Job settings ($MUCLI_HOME/jobs/settings.json) per-stage defaults.
    from .job_settings import stage_model

    fallback = stage_model(key)
    if fallback.get("model"):
        return {"stage": key, "provider": str(fallback.get("provider") or ""),
                "model": str(fallback["model"]), "source": "settings"}
    return {"stage": key, "provider": "", "model": "", "source": "unset"}


@dataclass(frozen=True)
class JobSpec:
    title: str
    description: str = ""
    repository: str = ""
    base_branch: str = "main"
    base_sha: str = ""
    acceptance_criteria: List[str] = field(default_factory=list)
    validation_commands: List[str] = field(default_factory=list)
    max_cost_usd: Optional[float] = None
    max_runtime_seconds: Optional[int] = None
    max_iterations: Optional[int] = None
    max_retries: int = 2
    max_subagents: Optional[int] = None
    environment: Dict[str, Any] = field(default_factory=dict)
    execution: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def normalized(self) -> "JobSpec":
        title = str(self.title or "").strip()
        if not title:
            raise ValueError("job title is required")
        # Round-35 F8: finiteness + practical upper bounds. NaN passes
        # `<= 0` comparisons and later defeats every budget check; absurd
        # integers create effectively unbounded runs / resource fan-out.
        max_cost_usd = (
            float(self.max_cost_usd) if self.max_cost_usd is not None else None
        )
        max_runtime_seconds = (
            int(self.max_runtime_seconds)
            if self.max_runtime_seconds is not None
            else None
        )
        max_iterations = (
            int(self.max_iterations) if self.max_iterations is not None else None
        )
        max_retries = int(self.max_retries)
        max_subagents = (
            int(self.max_subagents) if self.max_subagents is not None else None
        )
        if max_cost_usd is not None and not math.isfinite(max_cost_usd):
            raise ValueError("max_cost_usd must be finite")
        if max_cost_usd is not None and max_cost_usd <= 0:
            raise ValueError("max_cost_usd must be positive")
        if max_cost_usd is not None and max_cost_usd > 1_000_000.0:
            raise ValueError("max_cost_usd exceeds the supported maximum (1,000,000)")
        if max_runtime_seconds is not None:
            if max_runtime_seconds <= 0:
                raise ValueError("max_runtime_seconds must be positive")
            if max_runtime_seconds > 7 * 24 * 3600:
                raise ValueError("max_runtime_seconds exceeds the supported maximum (7 days)")
        if max_iterations is not None:
            if max_iterations <= 0:
                raise ValueError("max_iterations must be positive")
            if max_iterations > 100_000:
                raise ValueError("max_iterations exceeds the supported maximum (100,000)")
        if not (0 <= max_retries <= 100):
            raise ValueError("max_retries must be between 0 and 100")
        if max_subagents is not None:
            if max_subagents < 0:
                raise ValueError("max_subagents cannot be negative")
            if max_subagents > 10_000:
                raise ValueError("max_subagents exceeds the supported maximum (10,000)")
        return JobSpec(
            title=title,
            description=str(self.description or "").strip(),
            repository=str(self.repository or "").strip(),
            base_branch=str(self.base_branch or "main").strip() or "main",
            base_sha=str(self.base_sha or "").strip(),
            acceptance_criteria=[str(v).strip() for v in self.acceptance_criteria if str(v).strip()],
            validation_commands=[str(v).strip() for v in self.validation_commands if str(v).strip()],
            max_cost_usd=float(self.max_cost_usd) if self.max_cost_usd is not None else None,
            max_runtime_seconds=int(self.max_runtime_seconds) if self.max_runtime_seconds is not None else None,
            max_iterations=int(self.max_iterations) if self.max_iterations is not None else None,
            max_retries=int(self.max_retries),
            max_subagents=int(self.max_subagents) if self.max_subagents is not None else None,
            environment=dict(self.environment or {}),
            execution=normalize_execution(self.execution),
            metadata=dict(self.metadata or {}),
        )


@dataclass
class Job:
    id: str
    title: str
    description: str
    repository: str
    base_branch: str
    base_sha: str
    acceptance_criteria: List[str]
    validation_commands: List[str]
    status: JobStatus
    attention_reason: AttentionReason = AttentionReason.NONE
    attention_detail: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    max_cost_usd: Optional[float] = None
    max_runtime_seconds: Optional[int] = None
    max_iterations: Optional[int] = None
    max_retries: int = 2
    max_subagents: Optional[int] = None
    cost_usd: float = 0.0
    branch: str = ""
    worktree: str = ""
    environment: Dict[str, Any] = field(default_factory=dict)
    execution: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    session_name: str = ""
    worker_id: str = ""
    lease_expires_at: Optional[float] = None
    heartbeat_at: Optional[float] = None
    version: int = 1

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def needs_attention(self) -> bool:
        return self.status == JobStatus.NEEDS_HUMAN or self.attention_reason != AttentionReason.NONE

    @property
    def review_branch(self) -> str:
        """Normal Git branch that is authoritative once execution is finished."""
        value = str((self.metadata or {}).get("review_branch") or "").strip()
        if value:
            return value
        if self.status == JobStatus.READY_FOR_REVIEW:
            return str(self.branch or "").strip()
        return ""

    @property
    def review_head_sha(self) -> str:
        return str((self.metadata or {}).get("review_head_sha") or "").strip()

    @property
    def review_artifact(self) -> str:
        """Expose review semantics without making callers infer worktree state."""
        if self.review_branch:
            return "branch"
        if self.worktree:
            return "worktree"
        return "none"

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        value["attention_reason"] = self.attention_reason.value
        value["terminal"] = self.terminal
        value["needs_attention"] = self.needs_attention
        value["review_artifact"] = self.review_artifact
        value["review_branch"] = self.review_branch
        value["review_head_sha"] = self.review_head_sha
        return value


@dataclass
class JobEvent:
    id: int
    job_id: str
    event_type: str
    from_status: Optional[JobStatus]
    to_status: Optional[JobStatus]
    reason: str
    payload: Dict[str, Any]
    created_at: float

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["from_status"] = self.from_status.value if self.from_status else None
        value["to_status"] = self.to_status.value if self.to_status else None
        return value


@dataclass
class JobAttempt:
    id: str
    job_id: str
    number: int
    status: str
    session_name: str = ""
    worker_id: str = ""
    started_at: float = 0.0
    finished_at: Optional[float] = None
    error: str = ""
    cost_usd: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def statuses(values: Iterable[JobStatus | str]) -> List[str]:
    return [coerce_status(value).value for value in values]
