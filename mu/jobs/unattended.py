"""Jobs run unattended (user decision, 2026-10-15).

A stage never prompts anyone and never parks on an in-stage prompt:

* implement / checks / repair: every tool call is approved automatically. The sandbox (container
  isolation, read-only rootfs outside the worktree, egress allowlist, no credentials) is the
  control.
* plan / review: read-only. Write or side-effecting tool calls are denied automatically with
  :func:`deny_message`; read-only tools run without asking.
* Agent questions (ask_user / choice / quiz / confirm / input) are answered with
  :data:`AUTO_ANSWER`.

Each automatic decision is recorded as an ``auto_permission`` / ``auto_answered`` event.
The pipeline gates (plan approval → ``plan_review``, ``review_done`` → push) are not in-stage
prompts and are unchanged.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

READ_ONLY_STAGES = frozenset({"plan", "review"})
# ``repair``: the environment-repair stage (job_templates.md §7), treated like implement.
WRITE_STAGES = frozenset({"implement", "checks", "repair"})

AUTO_ANSWER = ("No one is available to answer during an automated job stage. Proceed with your best judgement, "
               "state the assumption in your summary, and continue.")

# Pipeline state → the agent stage it runs (a parked job's ``paused_from``).
_STATE_STAGE = {"planning": "plan", "implementing": "implement", "checking": "checks", "reviewing": "review"}


def read_only(stage: str) -> bool:
    return str(stage or "").strip().lower() in READ_ONLY_STAGES


def deny_message(stage: str) -> str:
    return f"This is the read-only {stage} stage; describe the change instead."


def decide(stage: str) -> Tuple[bool, str]:
    """``(allow, message)`` for a tool call that would otherwise need approval in ``stage``."""
    if read_only(stage):
        return False, deny_message(stage)
    return True, ""


def permission_event(stage: str, tool_name: str, *, allow: bool, source: str = "") -> Dict[str, Any]:
    """Payload of an ``auto_permission`` event."""
    payload: Dict[str, Any] = {"stage": str(stage or ""), "tool_name": str(tool_name or ""),
                               "decision": "approve" if allow else "deny"}
    if not allow:
        payload["message"] = deny_message(stage)
    if source:
        payload["source"] = source
    return payload


def answered_event(stage: str, shape: str, question: str = "") -> Dict[str, Any]:
    """Payload of an ``auto_answered`` event."""
    return {"stage": str(stage or ""), "shape": str(shape or "input"), "question": str(question or "")[:2000],
            "answer": AUTO_ANSWER}


def event_reason(event_type: str, payload: Dict[str, Any]) -> str:
    """One-line activity text for an ``auto_permission`` / ``auto_answered`` event."""
    stage = str(payload.get("stage") or "")
    if event_type == "auto_answered":
        question = " ".join(str(payload.get("question") or "").split())[:160]
        return f"Question answered automatically ({stage} stage runs unattended)" + (f": {question}" if question else "")
    tool = str(payload.get("tool_name") or "tool")
    if payload.get("decision") == "approve":
        return f"Auto-approved {tool} ({stage} stage runs unattended in the sandbox)"
    return f"Denied {tool} automatically: {payload.get('message') or deny_message(stage)}"


def stage_of_state(state: Any) -> str:
    """The agent stage of a pipeline state (``JobState`` or its value); ``implement`` when unknown."""
    value = str(getattr(state, "value", state) or "").lower()
    return _STATE_STAGE.get(value, "implement")


def interaction_response(stage: str, kind: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Host-side automatic response to a stage interaction request (the relay ``/interaction``
    fallback): approval → approve/deny by stage; anything else → :data:`AUTO_ANSWER`."""
    if str(kind or "") == "approval_required":
        allow, message = decide(stage)
        return {"kind": "approval_required", "decision": "approve" if allow else "deny", "detail": message,
                "auto": True}
    return {"kind": "question", "decision": "", "detail": AUTO_ANSWER, "value": AUTO_ANSWER, "selected": [],
            "auto": True}


# ------------------------------------------------------------------ jobs parked before this rule


def resume_parked_approval(service, job) -> bool:
    """Resolve a job parked on ``approval_required`` (from before jobs ran unattended) without a
    human: approve the pending call in implement/checks, deny it in plan/review, and resume.
    Returns True when the job was resumed."""
    from .models import JobStatus
    from .review import JobReviewService
    job = service.get(job.id)
    reason = str(getattr(job.attention_reason, "value", job.attention_reason) or "")
    if job.status != JobStatus.NEEDS_HUMAN or reason != "approval_required":
        return False
    core = service.store.core_state(job.id)
    stage = stage_of_state(core.paused_from)
    review = JobReviewService(service)
    context = review._interaction_context(job.id)
    allow, message = decide(stage)
    if allow and context.get("can_approve") is False:
        allow, message = False, "This tool call could not be previewed, so it was not run; try another approach."
    payload = permission_event(stage, str(context.get("tool_name") or "tool"), allow=allow, source="parked")
    if not allow:
        payload["message"] = message
    service.store.append_event(job.id, "auto_permission", reason=event_reason("auto_permission", payload),
                               payload=payload)
    review.respond(job.id, decision="approve" if allow else "deny", detail=message)
    return True


def resume_parked_approvals(service, *, limit: int = 200) -> list:
    """Controller tick: resume every job still parked on a tool approval. Never raises."""
    from .models import JobStatus
    resumed = []
    try:
        jobs = service.store.list_jobs(statuses=[JobStatus.NEEDS_HUMAN], limit=limit)
    except Exception:  # noqa: BLE001 - the tick must not fail on this
        return resumed
    for job in jobs:
        if str(getattr(job.attention_reason, "value", job.attention_reason) or "") != "approval_required":
            continue
        try:
            if resume_parked_approval(service, job):
                resumed.append(job.id)
        except Exception:  # noqa: BLE001 - one job never blocks the others
            continue
    return resumed
