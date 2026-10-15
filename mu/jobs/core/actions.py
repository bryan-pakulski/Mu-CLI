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
    ("approveTool", "Approve tool"),
    ("denyTool", "Deny tool"),
    ("message", "Message"),
    ("raiseLimit", "Approve more & continue"),
    ("resume", "Resume"),
    ("restartStage", "Restart stage"),
    ("retry", "Retry"),
    ("skipSetup", "Continue without setup"),
    ("continueUnverified", "Continue unverified"),
    ("recheck", "Re-run checks"),
    ("reprompt", "Re-prompt with findings"),
    ("markFindings", "Update findings"),
    ("markViewed", "Mark file viewed"),
    ("push", "Push / open PR"),
    ("repush", "Re-push"),
    ("markMerged", "Mark merged"),
    ("approveEnvironment", "Approve environment"),
    ("rejectEnvironment", "Reject environment"),
    ("setEnvironments", "Change build environments"),
    ("refreshEnvironments", "Refresh environments"),
    ("keepEnvironments", "Keep environments"),
    ("discard", "Discard"),
    ("delete", "Delete"),
)
COMMAND_IDS = tuple(c[0] for c in COMMANDS)

# Keyboard shortcuts surfaced by the web palette (§8.3). Same keys as the planos
# task view: a approve · x reject + re-plan · e edit plan · d deny · m message ·
# R restart stage · y retry · c re-run checks · F fix findings · p push · D mark done.
# Build environments: E refresh (re-snapshot) · K keep/unkeep.
SHORTCUTS = {"raiseLimit": "L", "approvePlan": "a", "rejectPlan": "x", "savePlanDraft": "e", "approveTool": "a", "denyTool": "d",
             "message": "m", "resume": "u", "restartStage": "R", "retry": "y", "recheck": "c", "reprompt": "F",
             "push": "p", "markMerged": "D", "refreshEnvironments": "E", "keepEnvironments": "K"}


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
    push_check_reason: str = "push requires a successful check run"
    pending_approval: Optional[Dict[str, Any]] = None
    has_diff: bool = False  # a diff.patch artifact exists (markViewed)
    has_worker: bool = False  # a worker process holds the job lease (mid-stage)
    environments: int = 0  # prepared/declared build-environment bindings
    environments_kept: bool = False
    # Repository-declared image/devcontainer awaiting the user's approval (job parked on it):
    # {repository, fingerprint, kind, ref, label, origin, message, event_id}.
    environment_approval: Optional[Dict[str, Any]] = None
    # The pushed job's host PR was closed without merging (metadata.pr.state).
    pr_closed: bool = False
    # The cap the job is paused on (``mu.jobs.limits.limit_detail``): {kind, scope, stage, used,
    # limit, unit, message, summary, suggested, suggested_text}; None when not paused on a limit.
    limit_reached: Optional[Dict[str, Any]] = None
    # Honest gate (job_templates.md §5): the latest check run could not run some checks.
    checks_unverified: bool = False
    # Parked on unverified checks (``continueUnverified`` reviews anyway, verdict capped).
    unverified_pending: bool = False
    # Parked on a failed template job setup: {binding, command, index, error, template}.
    setup_failed: Optional[Dict[str, Any]] = None


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
    # Shared flow hints (contract §7) so every UI renders the same dialogs.
    tone: str = ""                            # ok | warn | danger | ""
    confirm: Optional[str] = None             # confirm-dialog text
    input: Optional[Dict[str, Any]] = None    # {label, optional, multiline}
    dialog: Optional[str] = None              # "push" opens the push dialog
    hidden: bool = False                      # keep out of action bars

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


TONES = {
    "approvePlan": "ok", "rejectPlan": "danger", "approveTool": "warn", "denyTool": "danger",
    "retry": "warn", "restartStage": "warn", "resume": "warn", "push": "ok", "repush": "ok",
    "refreshEnvironments": "warn", "discard": "danger", "delete": "danger",
    "approveEnvironment": "warn", "rejectEnvironment": "danger", "raiseLimit": "ok",
    "skipSetup": "warn", "continueUnverified": "warn",
}
CONFIRMS = {
    "repush": "Push the branch again and open a new PR? The closed PR is left as it is.",
    "discard": "Discard this job? Running work is cancelled and the job cannot be resumed.",
    "delete": "Delete this job and its artifacts? This cannot be undone.",
    "restartStage": "Restart the current stage? Its in-progress work is discarded.",
    "refreshEnvironments": "Re-snapshot the build environments for this job? Stages started after this use the new images.",
    "approveEnvironment": "Approve this repository-declared build environment? mucli will use (or build) it for "
                          "jobs on this repository (a job still waiting on it is requeued; a job that ran without it keeps its "
                          "current environment).",
    "skipSetup": "Continue without the template's job setup? Checks that need the missing dependencies will be "
                 "reported as unrunnable.",
    "continueUnverified": "Review this job although some checks could not run? The review verdict is capped at "
                          "needs work.",
}
INPUTS: Dict[str, Dict[str, Any]] = {
    "rejectPlan": {"label": "What should the planner change?", "optional": False, "multiline": True},
    "denyTool": {"label": "Reason for the agent (optional)", "optional": True, "multiline": True},
    "resume": {"label": "Guidance for the agent (optional)", "optional": True, "multiline": True},
    "reprompt": {"label": "Guidance for the fix (optional)", "optional": True, "multiline": True},
    "message": {"label": "Message to the agent", "optional": False, "multiline": True},
    "rejectEnvironment": {"label": "Reason (optional)", "optional": True, "multiline": False},
}
DIALOGS = {"push": "push", "setEnvironments": "environments"}
# Build-environment commands live in the job's environments panel, not the action bar.
HIDDEN = frozenset({"markFindings", "markViewed", "setEnvironments", "refreshEnvironments", "keepEnvironments"})


def _rule(cmd: str, f: JobFacts) -> Optional[str]:
    """Return ``None`` when enabled, else the disabled reason."""
    s = f.core.state
    terminal = s in TERMINAL
    if cmd == "delete":
        return None  # always allowed; running work is cancelled first
    if cmd == "markViewed":
        # Review bookkeeping only: allowed in every state, terminal included.
        if f.archived:
            return "job is archived"
        return None if f.has_diff else "no diff to review"
    if terminal:
        return f"job is {s.value}"
    if cmd in ("approveEnvironment", "rejectEnvironment"):
        if f.archived:
            return "job is archived"
        return None if f.environment_approval else "no environment approval pending"
    if cmd in ("approveTool", "denyTool"):
        if s != JobState.NEEDS_INPUT or not f.pending_approval:
            return "no tool approval pending"
        if cmd == "approveTool" and f.pending_approval.get("can_approve") is False:
            return "tool preview failed; this request cannot be approved"
        return None
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
    if cmd == "raiseLimit":
        # Paused on a cap: approve more and continue. Otherwise: raise a cap ahead of time
        # (terminal ``/job raise-limit``; kept out of action bars).
        return "job is archived" if f.archived else None
    if cmd == "resume":
        # A pending tool approval (parked before jobs ran unattended) is resolved automatically.
        if f.limit_reached:
            return "the job reached a limit: approve more to continue, or cancel"
        return None if s == JobState.NEEDS_INPUT else "job is not paused"
    if cmd == "restartStage":
        if f.pending_approval:
            return "approve or deny the pending tool request first"
        if s in AGENT_STAGES or s == JobState.CHECKING:
            return None
        if s == JobState.NEEDS_INPUT and f.core.paused_from in AGENT_STAGES:
            return None
        return "no stage to restart"
    if cmd == "retry":
        return None if s == JobState.FAILED else "only failed jobs can be retried"
    if cmd == "skipSetup":
        return None if s == JobState.NEEDS_INPUT and f.setup_failed else "no failed job setup"
    if cmd == "continueUnverified":
        return None if s == JobState.NEEDS_INPUT and f.unverified_pending else "checks are not waiting on unrunnable results"
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
        if f.push_check_reason:
            return f.push_check_reason
        if f.verdict == "do_not_merge":
            # Still enabled with an explicit override + recorded reason (§4.4);
            # the UI asks for the reason, dispatch enforces it.
            return None
        return None
    if cmd == "repush":
        if s != JobState.PUSHED or not f.pr_closed:
            return "re-push is available when the job's PR was closed without merging"
        if not f.git_workspace:
            return "workspace is not a git repository"
        return None
    if cmd == "markMerged":
        return None if s == JobState.PUSHED else "job has not been pushed"
    if cmd in ("setEnvironments", "refreshEnvironments", "keepEnvironments"):
        if f.archived:
            return "job is archived"
        if cmd == "keepEnvironments":
            return None
        if f.has_worker:
            return "a stage is running; wait for it to finish or pause the job"
        if cmd == "refreshEnvironments" and not f.environments:
            return "this job uses the shared runtime image"
        return None
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
        JobState.PUSHED: "repush" if facts.pr_closed else "markMerged",
    }.get(s, "")
    if s == JobState.NEEDS_INPUT and facts.environment_approval and facts.environment_approval.get("blocking", True):
        primary = "approveEnvironment"  # non-blocking (egress) approvals are a banner, not the gate
    if s == JobState.NEEDS_INPUT and facts.pending_approval:
        primary = "denyTool" if facts.pending_approval.get("can_approve") is False else "approveTool"
    limit = facts.limit_reached if s == JobState.NEEDS_INPUT else None
    if limit:
        primary = "raiseLimit"
    for cid, label in COMMANDS:
        reason = _rule(cid, facts)
        args: Dict[str, Any] = {}
        if cid == "approvePlan":
            args["version"] = facts.plan_version
        if cid in ("approveTool", "denyTool") and facts.pending_approval:
            args["event_id"] = facts.pending_approval["event_id"]
        if cid in ("approveEnvironment", "rejectEnvironment") and facts.environment_approval:
            args["fingerprint"] = facts.environment_approval.get("fingerprint", "")
        if cid == "push" and facts.verdict == "do_not_merge":
            args["requires_override"] = True
        if cid == "push" and facts.checks_unverified:
            args["unverified"] = True  # the push dialog warns; no override needed
        if cid == "keepEnvironments" and facts.environments_kept:
            label = "Unkeep environments"
        if cid == "markMerged" and facts.pr_closed:
            label = "Mark done"  # nothing was merged; the human closes the job out
        input_spec = dict(INPUTS[cid]) if cid in INPUTS else None
        hidden = cid in HIDDEN
        if cid == "raiseLimit":
            input_spec, args = limit_input(limit)
            hidden = not limit
            if not limit:
                label = "Raise limit"
        if cid == "discard" and limit:
            label = "Cancel job"
        out.append(ActionVM(
            id=cid, label=label, enabled=reason is None, reason=reason or "",
            shortcut=SHORTCUTS.get(cid, ""), primary=(cid == primary),
            danger=cid in ("discard", "delete"), args=args,
            tone=TONES.get(cid, ""), confirm=CONFIRMS.get(cid),
            input=input_spec, dialog=DIALOGS.get(cid), hidden=hidden,
        ))
    return out


_AMOUNT_LABELS = {
    "cost": "Additional cost (USD)",
    "tokens": "Additional tokens (e.g. 5M, 500k)",
    "time": "Additional time (e.g. 30m, 2h)",
    "iterations": "Additional iterations",
}


def limit_input(limit: Optional[Dict[str, Any]]) -> tuple:
    """``(input spec, args)`` of ``raiseLimit``: an amount field prefilled with the suggestion.

    ``input.arg`` names the command arg the typed text goes to (``amount``); ``input.value`` is
    the prefill. Every surface renders this generically (web, Android, terminal)."""
    if not limit:
        return ({"label": "Amount (e.g. 25 for $25, 30m, 5M tokens)", "optional": False, "multiline": False,
                 "arg": "amount", "value": "", "type": "amount"}, {})
    from ..limits import fmt
    kind = str(limit.get("kind") or "")
    hint = f"Limit {fmt(kind, limit.get('limit'))}, used {fmt(kind, limit.get('used'))}."
    spec = {"label": _AMOUNT_LABELS.get(kind, "Additional amount"), "optional": False, "multiline": False,
            "arg": "amount", "value": str(limit.get("suggested_text") or ""), "type": "amount", "hint": hint,
            "unit": str(limit.get("unit") or "")}
    args = {k: limit.get(k) for k in ("kind", "scope", "stage")}
    args["amount"] = limit.get("suggested_text") or ""
    return spec, args


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
