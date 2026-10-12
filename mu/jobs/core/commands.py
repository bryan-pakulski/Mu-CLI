"""Single mutation entry point (guide §3.3, Phase 3).

``dispatch(service, cmd)`` validates the command against ``available_actions``
(disabled → ``CommandRejected`` → HTTP 409 with the reason), performs it via
the existing services, and emits a typed change on the ``ChangeBus``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .actions import check_allowed
from .changes import ChangeBus, default_bus
from .states import JobState


class CommandRejected(RuntimeError):
    """Command disabled for the job's current state (HTTP 409)."""


class CommandError(ValueError):
    """Malformed command payload (HTTP 400)."""


@dataclass
class Command:
    type: str
    job_id: str
    args: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "Command":
        if not isinstance(payload, dict):
            raise CommandError("command must be an object")
        ctype = str(payload.get("type") or payload.get("command") or "").strip()
        job_id = str(payload.get("job_id") or payload.get("id") or "").strip()
        if not ctype:
            raise CommandError("command type is required")
        if not job_id:
            raise CommandError("job_id is required")
        args = payload.get("args")
        if args is None:
            args = {k: v for k, v in payload.items() if k not in {"type", "command", "job_id", "id"}}
        if not isinstance(args, dict):
            raise CommandError("args must be an object")
        return cls(ctype, job_id, dict(args))


@dataclass
class Result:
    ok: bool
    command: str
    job_id: str
    parts: List[str] = field(default_factory=list)
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "command": self.command, "job_id": self.job_id,
                "parts": list(self.parts), "data": dict(self.data)}


def _archived(service, job_id: str) -> bool:
    try:
        from ..management import JobManagementService

        return job_id in JobManagementService(service).archived_ids()
    except Exception:  # noqa: BLE001 - archive table optional in minimal stores
        return False


def _merge_meta(service, job_id: str, **updates: Any):
    job = service.get(job_id)
    meta = dict(job.metadata or {})
    meta.update(updates)
    return service.store.update_runtime_fields(job_id, metadata_json=meta)


def _review_service(service):
    from ..review import JobReviewService

    return JobReviewService(service)


def dispatch(service, cmd: Command | Dict[str, Any], *, bus: Optional[ChangeBus] = None) -> Result:
    if isinstance(cmd, dict):
        cmd = Command.from_payload(cmd)
    from .vm import facts_for

    job = service.get(cmd.job_id)  # KeyError → 404
    facts = facts_for(service, job, archived=_archived(service, job.id))
    reason = check_allowed(facts, cmd.type)
    if reason:
        raise CommandRejected(reason)
    handler = _HANDLERS.get(cmd.type)
    if handler is None:
        raise CommandError(f"unknown command {cmd.type!r}")
    parts, data = handler(service, job, facts, cmd.args)
    service.store.append_event(job.id, "command", reason=cmd.type,
                               payload={"command": cmd.type, "args": _audit_args(cmd.args)})
    bus = bus or default_bus()
    if cmd.type == "delete":
        bus.emit("home", "", ["state"])
    else:
        bus.emit_job(job.id, parts)
    return Result(True, cmd.type, job.id, parts, data)


def _audit_args(args: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for key, value in args.items():
        if key in {"plan", "edited_plan"}:
            out[key] = "<plan>"
        else:
            text = value if isinstance(value, (int, float, bool, list)) else str(value)[:500]
            out[key] = text
    return out


# ----------------------------------------------------------------- handlers
def _approve_plan(service, job, facts, args):
    from .plan_ops import approve_plan

    if "version" not in args:
        raise CommandError("approvePlan requires version")
    try:
        version = int(args["version"])
    except (TypeError, ValueError) as exc:
        raise CommandError("version must be an integer") from exc
    approve_plan(service, job.id, version=version, edited_plan=args.get("edited_plan"),
                 detail=str(args.get("detail") or ""))
    _review_service(service).respond(job.id, decision="approve", detail=str(args.get("detail") or ""))
    return ["state", "pipeline", "plan"], {"approved_version": service.get(job.id).metadata.get("plan_approved_version")}


def _reject_plan(service, job, facts, args):
    from .plan_ops import reject_plan

    feedback = str(args.get("feedback") or "").strip()
    if not feedback:
        raise CommandError("rejectPlan requires feedback")
    reject_plan(service, job.id, feedback=feedback)
    _review_service(service).respond(job.id, decision="replan", detail=feedback)
    return ["state", "pipeline", "plan"], {}


def _save_plan_draft(service, job, facts, args):
    from .plan_ops import save_plan_draft

    if "plan" not in args:
        raise CommandError("savePlanDraft requires plan")
    data = save_plan_draft(service, job.id, args["plan"], autosave=bool(args.get("autosave")))
    return ["plan"], data


def _message(service, job, facts, args):
    text = str(args.get("text") or "").strip()
    if not text:
        raise CommandError("message requires text")
    # Delivered to the live stage by the stage runner (picked up from events).
    service.store.append_event(job.id, "stage_message", reason="human message",
                               payload={"text": text[:8000], "stage": facts.core.state.value})
    return ["session"], {}


def _resume(service, job, facts, args):
    guidance = str(args.get("guidance") or "").strip()
    if guidance:
        _merge_meta(service, job.id, resume_guidance=guidance)
    if job.status.value == "conflicted":
        _review_service(service).resolve_conflict(job.id, detail=guidance)
    else:
        service.resume(job.id, detail=guidance or "resumed")
    return ["state", "pipeline", "session"], {}


def _restart_stage(service, job, facts, args):
    stage = str(args.get("stage") or facts.core.state.value)
    service.store.append_event(job.id, "stage_restart_requested", reason="human restart",
                               payload={"stage": stage})
    if job.status.value == "needs_human":
        service.resume(job.id, detail=f"restart {stage}")
    return ["state", "pipeline", "session"], {"stage": stage}


def _retry(service, job, facts, args):
    service.retry(job.id, reason="retry requested")
    return ["state", "pipeline"], {}


def _recheck(service, job, facts, args):
    # Re-run Checks + Review on the current HEAD (no implement run).
    _merge_meta(service, job.id, recheck_requested=True)
    service.transition(job.id, "queued", reason="recheck requested")
    return ["state", "pipeline", "checks", "review"], {}


def _load_report(job):
    from .artifacts import artifacts_for
    from .review_report import ReviewReport

    art = artifacts_for(job)
    raw = art.read_json("review.json") if art.exists() else None
    if not raw:
        raise CommandRejected("no review report")
    return art, ReviewReport.from_dict(raw, job_id=job.id)


def _reprompt(service, job, facts, args):
    ids = [str(i) for i in (args.get("finding_ids") or [])]
    if not ids:
        raise CommandError("reprompt requires finding_ids")
    _, report = _load_report(job)
    selected = report.select(ids)
    if len(selected) != len(set(ids)):
        raise CommandError("unknown finding ids")
    _merge_meta(service, job.id, reprompt={
        "findings": [f.to_dict() if hasattr(f, "to_dict") else dict(f.__dict__) for f in selected],
        "guidance": str(args.get("guidance") or "").strip(),
    })
    service.transition(job.id, "running", reason="re-prompt with selected findings")
    return ["state", "pipeline", "review"], {"findings": ids}


def _mark_findings(service, job, facts, args):
    ids = [str(i) for i in (args.get("ids") or [])]
    disposition = str(args.get("disposition") or "")
    if not ids:
        raise CommandError("markFindings requires ids")
    art, report = _load_report(job)
    try:
        changed = report.set_disposition(ids, disposition)
    except ValueError as exc:
        raise CommandError(str(exc)) from exc
    art.write_json("review.json", report.to_dict())
    return ["review", "state"], {"changed": changed, "verdict": report.verdict,
                                 "merge_readiness": report.merge_readiness}


def _push(service, job, facts, args):
    override = str(args.get("override_reason") or "").strip()
    if facts.verdict == "do_not_merge" and not override:
        raise CommandRejected("verdict is do_not_merge: push requires override_reason")
    if override:
        service.store.append_event(job.id, "push_override", reason="human override",
                                   payload={"verdict": facts.verdict, "reason": override[:2000]})
    service.transition(job.id, "publishing", reason="human push")
    return ["state", "pipeline"], {}


def _mark_merged(service, job, facts, args):
    service.transition(job.id, "merged", reason="marked merged")
    return ["state", "pipeline"], {}


def _discard(service, job, facts, args):
    _review_service(service).discard(job.id, reason=str(args.get("reason") or "discarded"))
    return ["state", "pipeline"], {}


def _delete(service, job, facts, args):
    from ..management import JobManagementService

    data = JobManagementService(service).delete(job.id, force=True)
    return ["state"], {"deleted": bool(data.get("deleted"))}


_HANDLERS = {
    "approvePlan": _approve_plan, "rejectPlan": _reject_plan, "savePlanDraft": _save_plan_draft,
    "message": _message, "resume": _resume, "restartStage": _restart_stage, "retry": _retry,
    "recheck": _recheck, "reprompt": _reprompt, "markFindings": _mark_findings, "push": _push,
    "markMerged": _mark_merged, "discard": _discard, "delete": _delete,
}

__all__ = ["Command", "CommandError", "CommandRejected", "Result", "dispatch", "JobState"]
