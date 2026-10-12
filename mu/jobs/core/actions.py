"""Action model (guide §3.3).

``available_actions(facts)`` returns every command with ``enabled`` and, when
disabled, a human ``reason``. Web, mobile and TUI render buttons from this list
so availability logic exists exactly once. ``commands.dispatch`` re-checks the
same list and refuses disabled commands (HTTP 409).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .states import ACTIVE, AGENT_STAGES, TERMINAL, CoreState, JobState

# Order is the display order.
COMMANDS: Sequence[tuple[str, str]] = (
    ("approvePlan", "Approve plan"),
    ("rejectPlan", "Request re-plan"),
    ("savePlanDraft", "Save draft"),
    ("message", "Message"),
    ("resume", "Resume"),
    ("restartStage", "Restart stage"),
    ("retry", "Retry"),
    ("recheck", "Re-run checks"),
    ("reprompt", "Re-prompt with findings"),
    ("markFindings", "Update findings"),
    ("push", "Push / open PR"),
    ("markMerged", "Mark merged"),
    ("discard", "Discard"),
    ("delete", "Delete"),
)
COMMAND_IDS = tuple(c[0] for c in COMMANDS)

# Keyboard shortcuts surfaced by the web palette (§8.3).
SHORTCUTS = {"approvePlan": "a", "reprompt": "r", "push": "p", "retry": "t", "resume": "u"}


@dataclass
class JobFacts:
    """Everything availability depends on. Built by ``vm`` from store rows."""

    core: CoreState
    archived: bool = False
    has_plan: bool = False
    plan_version: int = 0
    has_review: bool = False
    verdict: str = ""  # ready | ready_with_nits | needs_work | do_not_merge
    open_findings: int = 0
    has_branch: bool = False
    has_worktree: bool = False
    live_stage: bool = False  # a stage is attached and accepting messages
    git_workspace: bool = True


@dataclass
class ActionVM:
    id: str
    label: str
    enabled: bool
    reason: str = ""
    shortcut: str = ""
    primary: bool = False
    danger: bool = False
    args: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _rule(cmd: str, f: JobFacts) -> Optional[str]:
    """Return ``None`` when enabled, else the disabled reason."""
    s = f.core.state
    terminal = s in TERMINAL
    if cmd == "delete":
        return None  # always allowed; running work is cancelled first
    if terminal:
        return f"job is {s.value}"
    if cmd in ("approvePlan", "rejectPlan", "savePlanDraft"):
        if s != JobState.PLAN_REVIEW:
            return "plan is not awaiting approval"
        if cmd == "approvePlan" and not f.has_plan:
            return "no plan to approve"
        return None
    if cmd == "message":
        if s in AGENT_STAGES and f.live_stage:
            return None
        if s == JobState.NEEDS_INPUT:
            return None
        return "no live stage to message"
    if cmd == "resume":
        return None if s == JobState.NEEDS_INPUT else "job is not paused"
    if cmd == "restartStage":
        if s in AGENT_STAGES or s == JobState.CHECKING:
            return None
        if s == JobState.NEEDS_INPUT and f.core.paused_from in AGENT_STAGES:
            return None
        return "no stage to restart"
    if cmd == "retry":
        return None if s == JobState.FAILED else "only failed jobs can be retried"
    if cmd == "recheck":
        if s in (JobState.REVIEW_DONE, JobState.PUSHED) and (f.has_worktree or f.has_branch):
            return None
        return "checks re-run only after review"
    if cmd == "reprompt":
        if s not in (JobState.REVIEW_DONE, JobState.PUSHED):
            return "re-prompt is available after review"
        if not f.has_review:
            return "no review report yet"
        return None
    if cmd == "markFindings":
        return None if f.has_review and s in (JobState.REVIEW_DONE, JobState.PUSHED) else "no review findings"
    if cmd == "push":
        if s != JobState.REVIEW_DONE:
            return "push is available after review"
        if not f.git_workspace:
            return "workspace is not a git repository"
        if f.verdict == "do_not_merge":
            # Still enabled with an explicit override + recorded reason (§4.4);
            # the UI asks for the reason, dispatch enforces it.
            return None
        return None
    if cmd == "markMerged":
        return None if s == JobState.PUSHED else "job has not been pushed"
    if cmd == "discard":
        return None  # any non-terminal job can be cancelled
    return "unknown command"


def available_actions(facts: JobFacts) -> List[ActionVM]:
    out: List[ActionVM] = []
    s = facts.core.state
    primary = {
        JobState.PLAN_REVIEW: "approvePlan",
        JobState.REVIEW_DONE: "push" if facts.verdict != "do_not_merge" else "reprompt",
        JobState.NEEDS_INPUT: "resume",
        JobState.FAILED: "retry",
        JobState.PUSHED: "markMerged",
    }.get(s, "")
    for cid, label in COMMANDS:
        reason = _rule(cid, facts)
        args: Dict[str, Any] = {}
        if cid == "approvePlan":
            args["version"] = facts.plan_version
        if cid == "push" and facts.verdict == "do_not_merge":
            args["requires_override"] = True
        out.append(ActionVM(
            id=cid, label=label, enabled=reason is None, reason=reason or "",
            shortcut=SHORTCUTS.get(cid, ""), primary=(cid == primary),
            danger=cid in ("discard", "delete"), args=args,
        ))
    return out


def action_map(facts: JobFacts) -> Dict[str, ActionVM]:
    return {a.id: a for a in available_actions(facts)}


def check_allowed(facts: JobFacts, command: str) -> Optional[str]:
    """``None`` when ``command`` may run, else the reason (used by dispatch)."""
    if command not in COMMAND_IDS:
        return f"unknown command {command!r}"
    return _rule(command, facts)


__all__ = [
    "ACTIVE", "COMMANDS", "COMMAND_IDS", "ActionVM", "JobFacts",
    "action_map", "available_actions", "check_allowed",
]
