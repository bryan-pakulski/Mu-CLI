"""Feature-mode tools over the feature plan engine (``mu.feature.engine``).

Approval of the plan, diff decisions and feature completion are the user's:
there is no tool for them. The agent proposes, executes one task at a time,
meets each exit criterion with evidence, reviews, and requests completion.
"""

from __future__ import annotations

from typing import Any, Dict

from mu.feature import engine
from mu.modes.preview import register_preview
from mu.modes.tooling import envelope, guarded, owner, run_line
from mu.tools import tool

_CRITERIA_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "What must be true."},
            "check": {"type": "string", "description": "Optional command that proves it (exit 0), e.g. a focused test."},
        },
        "required": ["text"],
    },
}
_TASK_PROPS = {
    "title": {"type": "string"},
    "objective": {"type": "string"},
    "actions": {"type": "array", "items": {"type": "string"}},
    "exit_criteria": _CRITERIA_SCHEMA,
}
_TASK_SCHEMA = {"type": "object", "properties": _TASK_PROPS, "required": ["title", "exit_criteria"]}


def _ctx(context):
    return owner(context)


def _plan_text(plan: engine.FeaturePlan) -> str:
    lines = [f"{plan.feature_id} \"{plan.feature_name}\" — {plan.status}, revision {plan.revision}"]
    for phase in sorted(plan.phases_meta, key=lambda p: p.order or p.id):
        lines.append(f"Phase {phase.id}: {phase.title}")
        for task in [t for t in plan.tasks if t.phase_id == phase.id]:
            crits = ", ".join(f"{c.id}{'✓' if c.met else '○'}" for c in task.criteria)
            lines.append(f"  t{task.id} [{task.status}{', reviewed' if task.reviewed else ''}] {task.title} ({crits})")
    return "\n".join(lines)


@tool(
    name="propose_feature_plan",
    description=(
        "Propose the feature plan as a DRAFT in one call: goal, phases, tasks with objectives, "
        "actions and exit criteria (each optionally with a check command). The user reviews and "
        "approves it; implementation starts only after approval. Calling it again replaces the draft "
        "(or makes a new revision of an approved plan that needs re-approval)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Short feature name."},
            "goal": {"type": "string", "description": "The user's request in one or two sentences."},
            "phases": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "goal": {"type": "string"},
                        "tasks": {"type": "array", "items": _TASK_SCHEMA},
                    },
                    "required": ["title", "tasks"],
                },
            },
        },
        "required": ["name", "goal", "phases"],
    },
    requires_approval=False, phase="feature", execution_kind="memory", preview_policy="none",
    server_policy="session_only",
)
@guarded("propose_feature_plan")
def propose_feature_plan(args: Dict[str, Any], context) -> Dict[str, Any]:
    plan = engine.propose_plan(_ctx(context), name=str(args.get("name") or ""), goal=str(args.get("goal") or ""),
                               phases=args.get("phases") or [])
    return envelope("propose_feature_plan", True,
                    f"Draft saved.\n{_plan_text(plan)}\nShow the user the plan and ask them to approve it "
                    "(/feature approve or the panel's Approve). Do not start implementation until then.",
                    {"feature_id": plan.feature_id, "status": plan.status, "revision": plan.revision,
                     "hash": engine.plan_hash(plan)})


@tool(
    name="revise_feature_plan",
    description=(
        "Edit the plan: add_phases [{title, goal}], add_tasks [{phase_id, title, objective, actions, "
        "exit_criteria}], update_tasks [{id, ...fields}], remove_tasks [ids], goal. Changing an approved "
        "plan makes a new revision that needs the user's re-approval; only changed tasks reset."
    ),
    parameters={
        "type": "object",
        "properties": {
            "add_phases": {"type": "array", "items": {"type": "object", "properties": {
                "title": {"type": "string"}, "goal": {"type": "string"}}}},
            "add_tasks": {"type": "array", "items": {"type": "object", "properties": {
                **_TASK_PROPS, "phase_id": {"type": "integer"}}, "required": ["title", "exit_criteria"]}},
            "update_tasks": {"type": "array", "items": {"type": "object", "properties": {
                "id": {"type": "integer"}, **_TASK_PROPS}, "required": ["id"]}},
            "remove_tasks": {"type": "array", "items": {"type": "integer"}},
            "goal": {"type": "string"},
        },
    },
    requires_approval=False, phase="feature", execution_kind="memory", preview_policy="none",
    server_policy="session_only",
)
@guarded("revise_feature_plan")
def revise_feature_plan(args: Dict[str, Any], context) -> Dict[str, Any]:
    plan = engine.revise_plan(_ctx(context), args)
    note = " It needs the user's re-approval." if plan.approval is None else ""
    return envelope("revise_feature_plan", True, f"Plan revised (revision {plan.revision}).{note}\n{_plan_text(plan)}",
                    {"status": plan.status, "revision": plan.revision, "needs_approval": plan.approval is None})


@tool(
    name="feature_status",
    description="The active feature plan: status, approval, tasks with criteria, the current task and what blocks completion.",
    parameters={"type": "object", "properties": {}},
    requires_approval=False, phase="feature", execution_kind="read", preview_policy="none",
    server_policy="session_only",
)
@guarded("feature_status")
def feature_status(args: Dict[str, Any], context) -> Dict[str, Any]:
    plan = engine.require_plan(_ctx(context))
    view = engine.plan_view(plan, _ctx(context))
    text = _plan_text(plan)
    if view.get("completion_gaps"):
        text += "\nCompletion needs: " + "; ".join(view["completion_gaps"])
    return envelope("feature_status", True, text, view)


@tool(
    name="start_task",
    description="Start a task (exactly one is in progress; the plan must be approved). Records the git tree so its diff is known.",
    parameters={"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]},
    requires_approval=False, phase="feature", execution_kind="memory", preview_policy="none",
    server_policy="session_only",
)
@guarded("start_task")
def start_task(args: Dict[str, Any], context) -> Dict[str, Any]:
    task = engine.start_task(_ctx(context), args.get("task_id"))
    crits = "\n".join(f"  {c.id} {c.text}" + (f" (check: {c.check['command']})" if c.check else "")
                      for c in task.criteria)
    return envelope("start_task", True, f"t{task.id} in progress: {task.title}\nExit criteria:\n{crits}",
                    engine.task_view(task))


@tool(
    name="check_criterion",
    description="Run an exit criterion's declared check command; the criterion is met when it passes.",
    parameters={
        "type": "object",
        "properties": {"task_id": {"type": "integer"}, "criterion": {"type": "string", "description": "c<n>"}},
        "required": ["task_id", "criterion"],
    },
    requires_approval=True, phase="feature", execution_kind="mutate", preview_policy="optional",
    server_policy="session_only",
)
@guarded("check_criterion")
def check_criterion(args: Dict[str, Any], context) -> Dict[str, Any]:
    plan = engine.require_plan(_ctx(context))
    task = plan.task(args.get("task_id"))
    crit = task.criterion(str(args.get("criterion") or "")) if task else None
    if crit is not None and not crit.check:
        return envelope("check_criterion", False,
                        f"{crit.id} has no check command; use cite_evidence with a passing run or a file.",
                        error_code="no_check")
    crit, run = engine.check_criterion(_ctx(context), args.get("task_id"), str(args.get("criterion") or ""))
    data = {"criterion": crit.id, "met": crit.met, "run": run.brief() if run else None}
    if crit.met:
        return envelope("check_criterion", True, f"{crit.id} met ({run_line(run)}).", data)
    return envelope("check_criterion", False, f"{crit.id} not met ({run_line(run)}).", data, "check_failed")


@tool(
    name="cite_evidence",
    description=(
        "Meet an exit criterion with evidence you already have: passing check runs (r<n>), or for "
        "criteria that aren't commands, a file plus a one-line justification."
    ),
    parameters={
        "type": "object",
        "properties": {
            "task_id": {"type": "integer"},
            "criterion": {"type": "string", "description": "c<n>"},
            "runs": {"type": "array", "items": {"type": "string"}},
            "file": {"type": "string"},
            "justification": {"type": "string"},
        },
        "required": ["task_id", "criterion"],
    },
    requires_approval=False, phase="feature", execution_kind="memory", preview_policy="none",
    server_policy="session_only",
)
@guarded("cite_evidence")
def cite_evidence(args: Dict[str, Any], context) -> Dict[str, Any]:
    if not args.get("runs") and not args.get("file"):
        return envelope("cite_evidence", False, "Cite runs (r<n>) or a file with a justification.")
    crit, _run = engine.check_criterion(_ctx(context), args.get("task_id"), str(args.get("criterion") or ""),
                                        evidence=args.get("runs") or [], file=str(args.get("file") or ""),
                                        justification=str(args.get("justification") or ""))
    return envelope("cite_evidence", True, f"{crit.id} met.", {"criterion": crit.id, "evidence": crit.evidence})


@tool(
    name="complete_task",
    description="Complete the in-progress task. Refused until every exit criterion is met with evidence.",
    parameters={"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]},
    requires_approval=False, phase="feature", execution_kind="memory", preview_policy="none",
    server_policy="session_only",
)
@guarded("complete_task")
def complete_task(args: Dict[str, Any], context) -> Dict[str, Any]:
    task = engine.complete_task(_ctx(context), args.get("task_id"))
    plan = engine.require_plan(_ctx(context))
    message = f"t{task.id} completed. Review it with review_task."
    if plan.status == "review":
        message += " All tasks are complete: review each, run the full suite, then request completion."
    return envelope("complete_task", True, message,
                    {"task": task.id, "plan_status": plan.status, "diff_stat": task.diff_stat})


@tool(
    name="block_task",
    description="Block a task on something only the user can provide; raises a blocker tied to the task.",
    parameters={
        "type": "object",
        "properties": {"task_id": {"type": "integer"}, "summary": {"type": "string"}, "needs": {"type": "string"}},
        "required": ["task_id", "summary", "needs"],
    },
    requires_approval=False, phase="feature", execution_kind="control", preview_policy="none",
    server_policy="session_only",
)
@guarded("block_task")
def block_task(args: Dict[str, Any], context) -> Dict[str, Any]:
    task = engine.block_task(_ctx(context), args.get("task_id"), summary=str(args.get("summary") or ""),
                             needs=str(args.get("needs") or ""))
    session = getattr(context, "session", None)
    if session is not None:
        session._loop_blocker_raised = True
    return envelope("block_task", True, f"t{task.id} blocked; tell the user what you need and stop.",
                    {"task": task.id})


@tool(
    name="review_task",
    description=(
        "Review a completed task against its objective and criteria. Needs a passing check after the "
        "last edit; the engine attaches the task's diff. Issues: follow_up (becomes a task) or blocking "
        "(raises a blocker)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "task_id": {"type": "integer"},
            "summary": {"type": "string"},
            "issues": {"type": "array", "items": {"type": "object", "properties": {
                "title": {"type": "string"},
                "severity": {"type": "string", "enum": list(engine.ISSUE_SEVERITIES)},
                "detail": {"type": "string"}}, "required": ["title"]}},
        },
        "required": ["task_id", "summary"],
    },
    requires_approval=False, phase="feature", execution_kind="memory", preview_policy="none",
    server_policy="session_only",
)
@guarded("review_task")
def review_task(args: Dict[str, Any], context) -> Dict[str, Any]:
    review = engine.review_task(_ctx(context), args.get("task_id"), summary=str(args.get("summary") or ""),
                                issues=args.get("issues") or [])
    return envelope("review_task", True,
                    f"Review {review.id} recorded for t{review.task_id} (check {review.check_run}, "
                    f"{len(review.issues)} issue(s)).",
                    {"review": review.id, "issues": review.issues, "diff_stat": review.diff_stat})


@tool(
    name="run_feature_suite",
    description="Run the full test suite as the feature's completion evidence (must pass after the last change).",
    parameters={"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
    requires_approval=True, phase="feature", execution_kind="mutate", preview_policy="optional",
    server_policy="session_only",
)
@guarded("run_feature_suite")
def run_feature_suite(args: Dict[str, Any], context) -> Dict[str, Any]:
    run = engine.run_suite(_ctx(context), str(args.get("command") or ""))
    return envelope("run_feature_suite", run.passed, run_line(run), run.brief(),
                    None if run.passed else "suite_failed")


@tool(
    name="propose_task_diff",
    description="Propose a diff for the user to decide on in the panel (they approve or reject it; then apply approved diffs).",
    parameters={
        "type": "object",
        "properties": {"task_id": {"type": "integer"}, "diff": {"type": "string"}, "rationale": {"type": "string"}},
        "required": ["task_id", "diff"],
    },
    requires_approval=False, phase="feature", execution_kind="memory", preview_policy="none",
    server_policy="session_only",
)
@guarded("propose_task_diff")
def propose_task_diff(args: Dict[str, Any], context) -> Dict[str, Any]:
    proposal = engine.propose_diff(_ctx(context), args.get("task_id"), diff=str(args.get("diff") or ""),
                                   rationale=str(args.get("rationale") or ""))
    return envelope("propose_task_diff", True, f"Proposal {proposal.id} is waiting for the user's decision.",
                    {"proposal": proposal.id})


@tool(
    name="request_feature_completion",
    description="Ask the user to mark the feature complete. Lists what is still missing if it isn't ready.",
    parameters={"type": "object", "properties": {}},
    requires_approval=False, phase="feature", execution_kind="memory", preview_policy="none",
    server_policy="session_only",
)
@guarded("request_feature_completion")
def request_feature_completion(args: Dict[str, Any], context) -> Dict[str, Any]:
    _plan, gaps = engine.request_completion(_ctx(context))
    if gaps:
        return envelope("request_feature_completion", False, "Not ready: " + "; ".join(gaps) + ".",
                        {"gaps": gaps}, "not_ready")
    return envelope("request_feature_completion", True,
                    "Completion requested. The user confirms it (/feature complete or the panel).", {"gaps": []})


@register_preview("check_criterion")
def _preview_check(args, session):
    plan = engine.active_plan(session, cached=True)
    task = plan.task(args.get("task_id")) if plan else None
    crit = task.criterion(str(args.get("criterion") or "")) if task else None
    return {"will_run": crit.check["command"]} if crit is not None and crit.check else {}


__all__ = [
    "block_task", "check_criterion", "cite_evidence", "complete_task", "feature_status",
    "propose_feature_plan", "propose_task_diff", "request_feature_completion", "review_task",
    "revise_feature_plan", "run_feature_suite", "start_task",
]
