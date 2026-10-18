"""Implement-stage inputs and outcome shared by the sandbox runner and the worker."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

from .models import AttentionReason, Job


@dataclass
class JobRunOutcome:
    kind: str
    status: str = ""
    error: str = ""
    cost_usd: float = 0.0
    attention_reason: AttentionReason = AttentionReason.NONE
    attention_detail: str = ""
    attention_payload: Dict[str, Any] = field(default_factory=dict)
    result: Dict[str, Any] = field(default_factory=dict)
    # True when a failed outcome was caused by a transient provider/network
    # error (rate limit, 5xx, timeout): the worker may requeue with backoff
    # under the job's retry budget instead of failing terminally.
    transient: bool = False
    # Why the stage stopped (sandbox worker): "provider_transient" | "rate_limited" (both set
    # ``transient``) | "iteration_limit" | "context_overflow" (already retried once with a fresh
    # conversation by the sandbox runner) | "fatal"; "" = completed or waiting on a human.
    error_class: str = ""


def session_name(job: Job) -> str:
    return f"job-{job.id[:20]}"


def _verification_feedback(payload: Dict[str, Any]) -> list[str]:
    lines = ["", "The previous implementation failed deterministic verification."]
    verification_id = str(payload.get("verification_id") or "")
    if verification_id:
        lines.append(f"Verification: {verification_id}")
    for check in list(payload.get("failed_checks") or [])[:4]:
        if not isinstance(check, dict):
            continue
        command = str(check.get("command") or "verification command")
        lines.append(f"- FAILED: {command}")
        if check.get("timed_out"):
            lines.append("  timed out")
        error = str(check.get("error") or "").strip()
        if error:
            lines.append(f"  error: {error[:1200]}")
        output = str(check.get("stderr") or check.get("stdout") or "").strip()
        if output:
            lines.append("  output:")
            lines.append(output[-2500:])
    dirty = str(payload.get("dirty_status") or "").strip()
    if dirty:
        lines.extend(["- Verification left the worktree dirty:", dirty[-2000:]])
    lines.append("Repair the implementation in the existing job branch and re-run relevant checks.")
    return lines


def implement_prompt(service, job: Job) -> str:
    """The implement stage prompt: ticket, approved plan and every pending piece of feedback."""
    lines = ["DURABLE ENGINEERING JOB", f"Title: {job.title}"]
    if job.description:
        lines.extend(["", "Description:", job.description])
    if job.acceptance_criteria:
        lines.extend(["", "Acceptance criteria:", *[f"- {v}" for v in job.acceptance_criteria]])
    if job.validation_commands:
        lines.extend(["", "Validation expected by the controller:", *[f"- {v}" for v in job.validation_commands]])
    if job.branch:
        lines.extend(["", f"Managed job branch: {job.branch}"])

    events = service.events(job.id)
    for event in reversed(events):
        if event.event_type == "verification_failed":
            lines.extend(_verification_feedback(event.payload))
            break
    # Typed query: service.events() returns only the oldest events of a long job.
    for event in reversed(service.store.list_events_by_type(job.id, ("guard_blocked", "verification_pending"))):
        # A host safety check parked the job; once resumed, the agent fixes what it flagged.
        if event.event_type == "verification_pending":
            break
        if event.event_type == "guard_blocked":
            lines.extend(["", f"The host's safety check blocked the previous {event.payload.get('stage') or 'stage'} "
                          "result and parked the job:", str(event.payload.get("error") or event.reason),
                          "Fix this before finishing: remove the flagged file or secret, or move test fixtures "
                          "under tests/, fixtures/ or testdata/. Never put real credentials in the repository."])
            break
    approved_text = None
    try:
        from .core.plan_ops import approved_plan_text

        approved_text = approved_plan_text(job)
    except (OSError, ValueError):
        approved_text = None
    from .core.plan_ops import plan_store
    doc = plan_store(job).load()
    plan = {"summary": doc.summary, "steps": doc.approach} if doc else {}
    if approved_text:
        version = (job.metadata or {}).get("plan_approved_version")
        lines.extend(["", f"Approved plan (v{version}, follow it verbatim):", approved_text])
    elif plan.get("summary") or plan.get("steps"):
        lines.extend(["", "Agreed plan:"])
        if plan.get("summary"):
            lines.append(str(plan["summary"]))
        lines.extend(f"- {step}" for step in plan.get("steps") or [])
    for event in reversed(events):
        if event.event_type == "review_changes_requested":
            from .stages import review_feedback_lines

            lines.extend(review_feedback_lines(event.payload or {}))
            break
    for event in reversed(events):
        if event.event_type == "human_response":
            detail = str(event.payload.get("detail") or "").strip()
            if detail:
                lines.extend(["", "Latest human response:", detail])
            break

    reprompt = (job.metadata or {}).get("reprompt") or {}
    if reprompt:
        lines.extend(["", "Human-selected review findings to address:"])
        for finding in reprompt.get("findings") or []:
            lines.append(f"- [{finding.get('id')}] {finding.get('file') or ''}:{finding.get('line_start') or ''} "
                         f"{finding.get('title')}: {finding.get('detail')}")
        if reprompt.get("guidance"):
            lines.extend(["Human guidance:", reprompt["guidance"]])

    from .core.notes import HANDOFF_INSTRUCTIONS
    from .evidence import INSTRUCTIONS as EVIDENCE_INSTRUCTIONS
    from .stages import stage_brief
    brief = stage_brief(job, "plan")
    if brief:
        lines.extend(["", brief])
    lines.extend([
        "",
        "Implement the ticket and validate the result where possible.",
        "Work only inside the attached job workspace; do not modify the user's primary checkout.",
        "The controller, not the agent, decides whether the job is ready for review.",
        "",
        EVIDENCE_INSTRUCTIONS,
        "",
        HANDOFF_INSTRUCTIONS,
    ])
    return "\n".join(lines)
