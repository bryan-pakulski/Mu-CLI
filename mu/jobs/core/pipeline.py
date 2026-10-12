"""Derived six-step pipeline (guide §3.2).

Step status, timing, cost and run count are computed from the core state plus
state history. Nothing here is stored.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .states import CoreState, JobState, coerce_state

# Status glyph vocabulary shared by web, mobile and TUI (§8.1).
DONE, ACTIVE, WAITING, FAILED, SKIPPED, PENDING = (
    "done", "active", "waiting", "failed", "skipped", "pending",
)
GLYPHS = {DONE: "✓", ACTIVE: "●", WAITING: "◆", FAILED: "✗", SKIPPED: "–", PENDING: "○"}

STEPS: Sequence[tuple[str, str, frozenset]] = (
    ("plan", "Plan", frozenset({JobState.PROVISIONING, JobState.PLANNING})),
    ("approve", "Approve", frozenset({JobState.PLAN_REVIEW})),
    ("implement", "Implement", frozenset({JobState.IMPLEMENTING})),
    ("checks", "Checks", frozenset({JobState.CHECKING})),
    ("review", "Review", frozenset({JobState.REVIEWING, JobState.REVIEW_DONE})),
    ("push", "Push", frozenset({JobState.PUSHING, JobState.PUSHED, JobState.DONE})),
)
STEP_IDS = tuple(s[0] for s in STEPS)


def step_for_state(state: JobState | str | None) -> Optional[str]:
    st = coerce_state(state)
    for sid, _label, states in STEPS:
        if st in states:
            return sid
    return None


@dataclass
class HistoryEntry:
    """One core-state entry: the job entered ``state`` at ``at``."""

    state: JobState
    at: float

    @classmethod
    def coerce(cls, value: Any) -> "HistoryEntry":
        if isinstance(value, HistoryEntry):
            return value
        if isinstance(value, Mapping):
            return cls(coerce_state(value.get("state")), float(value.get("at") or 0.0))
        state, at = value
        return cls(coerce_state(state), float(at or 0.0))


@dataclass
class Step:
    id: str
    label: str
    status: str = PENDING
    glyph: str = GLYPHS[PENDING]
    started_at: Optional[float] = None
    elapsed: float = 0.0
    cost_usd: float = 0.0
    runs: int = 0
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def derive_pipeline(
    core: CoreState,
    history: Iterable[Any] = (),
    *,
    costs: Optional[Mapping[str, float]] = None,
    now: float = 0.0,
    plan_approval: bool = True,
    details: Optional[Mapping[str, str]] = None,
) -> List[Step]:
    """Compute the six steps from ``core`` and chronological ``history``.

    ``history`` entries are ``(state, entered_at)`` oldest first. ``costs`` is
    keyed by step id. ``details`` lets callers attach per-step text (for
    example "3 blocking findings") without the pipeline reading artifacts.
    """
    entries = [HistoryEntry.coerce(h) for h in history]
    steps = {sid: Step(sid, label) for sid, label, _ in STEPS}
    # Time + run accounting: a run starts every time a step is entered from a
    # different step. Pause/failed intervals are not billed to any step.
    prev_step: Optional[str] = None
    for i, entry in enumerate(entries):
        sid = step_for_state(entry.state)
        end = entries[i + 1].at if i + 1 < len(entries) else (now or entry.at)
        if sid is None:
            prev_step = None
            continue
        step = steps[sid]
        if sid != prev_step:
            step.runs += 1
            if step.started_at is None:
                step.started_at = entry.at
        step.elapsed += max(0.0, end - entry.at)
        prev_step = sid
    for sid, value in (costs or {}).items():
        if sid in steps:
            steps[sid].cost_usd = round(float(value or 0.0), 6)

    current_state = core.state
    effective = core.paused_from if current_state == JobState.NEEDS_INPUT else (
        core.failed_from if current_state == JobState.FAILED else current_state)
    current_step = step_for_state(effective)
    order = list(STEP_IDS)

    if current_state == JobState.CANCELLED:
        visited = {sid for sid, s in steps.items() if s.runs}
        for sid in order:
            steps[sid].status = DONE if sid in visited else SKIPPED
    elif current_state == JobState.DONE:
        for sid in order:
            steps[sid].status = DONE
    else:
        idx = order.index(current_step) if current_step else -1
        for i, sid in enumerate(order):
            if i < idx:
                steps[sid].status = DONE
            elif i == idx:
                steps[sid].status = _current_status(current_state, effective)
            else:
                steps[sid].status = PENDING
        if current_step == "push" and current_state == JobState.PUSHED:
            steps["push"].status = DONE
    if not plan_approval and steps["approve"].runs == 0 and steps["approve"].status == DONE:
        steps["approve"].status = SKIPPED

    if core.fix_attempts:
        steps["implement"].detail = f"{core.fix_attempts} fix attempt{'s' if core.fix_attempts != 1 else ''}"
    for sid, text in (details or {}).items():
        if sid in steps and text:
            steps[sid].detail = str(text)
    for step in steps.values():
        step.glyph = GLYPHS[step.status]
        step.elapsed = round(step.elapsed, 3)
    return [steps[sid] for sid in order]


def _current_status(state: JobState, effective: Optional[JobState]) -> str:
    if state == JobState.FAILED:
        return FAILED
    if state == JobState.NEEDS_INPUT:
        return WAITING
    if state in (JobState.PLAN_REVIEW, JobState.REVIEW_DONE):
        return WAITING
    return ACTIVE


def mini_indicator(steps: Sequence[Step]) -> str:
    """Compact ``✓─✓─●─○─○─○`` string for tables and the TUI."""
    return "─".join(s.glyph for s in steps)
