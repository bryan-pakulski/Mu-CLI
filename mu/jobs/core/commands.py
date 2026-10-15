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
    """Atomic top-level metadata merge (concurrent writers cannot lose keys)."""
    return service.store.patch_metadata(job_id, updates)


def _review_service(service):
    from ..review import JobReviewService

    return JobReviewService(service)


def dispatch(service, cmd: Command | Dict[str, Any], *, bus: Optional[ChangeBus] = None) -> Result:
    if isinstance(cmd, dict) and cmd.get("type") == "create":
        args = cmd.get("args")
        if not isinstance(args, dict):
            raise CommandError("create requires job fields in args")
        job = service.create_from_payload(args)
        service.store.append_event(job.id, "command", reason="create", payload={"command": "create"})
        (bus or default_bus()).emit_job(job.id, ["state", "pipeline"])
        return Result(True, "create", job.id, ["state", "pipeline"], {"job": job.to_dict()})
    if isinstance(cmd, dict):
        cmd = Command.from_payload(cmd)
    with service.store.command_lock(cmd.job_id):
        return _dispatch_locked(service, cmd, bus=bus)


def ensure_controller_daemon():
    from ..host import ensure_controller_daemon as ensure
    return ensure()


def import_jira(service, *, keys, jql, defaults, force=False):
    """``force`` re-imports tickets whose latest job is done (terminal ``--force``)."""
    from mu.integrations.jira import JiraClient, import_issues
    from mu.integrations.jira_settings import resolve_config
    template = str(((defaults or {}).get("execution") or {}).get("template") or "").strip()
    if template:
        # "Run in" for every ticket: an unknown template fails the import once (ValueError).
        from ..sandbox.environments import normalize_template_choice
        normalize_template_choice({"template": template})
    return import_issues(service, JiraClient(resolve_config()), keys=keys, jql=jql, defaults=defaults, force=force)


def set_job_rule(path: str, text: Optional[str]):
    """Save one run rule from typed text (``None`` resets it to the default).

    Returns the rule's label, effective value/display and whether it is overridden.
    Raises ``ValueError`` with a readable message for unknown paths or invalid values."""
    from ..job_settings import parse_rule_text
    return set_job_rule_value(path, None if text is None else parse_rule_text(path, text))


def set_job_rule_value(path: str, value: Any):
    """Save one run rule from an already-parsed JSON value (``None`` resets); same result as ``set_job_rule``."""
    from ..job_settings import FIELD_BY_PATH, format_rule_value, rule, rule_overrides, set_rule
    saved = set_rule(path, value)
    field = FIELD_BY_PATH[path]
    value = rule(path, saved)
    return {"path": path, "label": field["label"], "value": value, "display": format_rule_value(field, value),
            "set": path in rule_overrides(saved), "overrides": rule_overrides(saved)}


def build_toolchain(base_image: str) -> Dict[str, Any]:
    """Start a background build of a saved Settings → Toolchains row's derived image; returns its status."""
    from ..sandbox.toolchain import IMAGE_RE, build_in_background, toolchain_rows
    base = str(base_image or "").strip()
    if not base or not IMAGE_RE.fullmatch(base) or base.startswith("-"):
        raise ValueError("a valid base image is required")
    if base not in {row["base_image"] for row in toolchain_rows()}:
        raise ValueError(f"{base} is not a saved toolchain base image; add it to Settings → Toolchains first")
    return build_in_background(base)


def parse_environment_spec(text: str) -> Dict[str, Any]:
    """CLI ``name[:paths][:docker]`` → binding dict (raises ``ValueError`` with a readable message)."""
    from ..sandbox.environments import parse_spec
    return parse_spec(text).to_dict()


def environment_sources() -> Dict[str, Any]:
    """Pickers for the Build environments editor (environments, templates, images, DinD/GPU status)."""
    from ..sandbox.environments import list_sources
    return list_sources()


def is_repository_url(text: str) -> bool:
    """``True`` for a clonable repository URL (https/ssh/scp-style/file), ``False`` for a path."""
    from ..clone import looks_like_url
    return looks_like_url(text)


def ensure_repository_clone(url: str):
    """Clone or refresh ``url`` under $MUCLI_HOME/repos (W8); raises ``ValueError`` (CloneError)."""
    from ..clone import ensure_clone
    return ensure_clone(url)


def respond(service, job_id, answer):
    """Compatibility parsing for terminal human responses; commands own writes."""
    job = service.get(job_id)
    core = service.store.core_state(job_id)
    if core.state == JobState.PLAN_REVIEW:
        decision, _, detail = answer.partition(" ")
        if decision in {"approve", "yes"}:
            from .plan_ops import plan_store
            return dispatch(service, {"type": "approvePlan", "job_id": job_id,
                                      "args": {"version": plan_store(job).latest_version(), "detail": detail}})
        if decision in {"replan", "reject"}:
            return dispatch(service, {"type": "rejectPlan", "job_id": job_id, "args": {"feedback": detail}})
        raise CommandError("Use approve or replan <feedback> at the plan gate")
    return dispatch(service, {"type": "resume", "job_id": job_id, "args": {"guidance": answer}})


def _dispatch_locked(service, cmd: Command, *, bus=None) -> Result:
    from .vm import facts_for

    job = service.get(cmd.job_id)  # KeyError → 404
    service.store.append_event(job.id, "command", reason=cmd.type,
                               payload={"command": cmd.type, "args": _audit_args(cmd.args)})
    handler = _HANDLERS.get(cmd.type)
    if handler is None:
        raise CommandError(f"unknown command {cmd.type!r}")
    facts = facts_for(service, job, archived=_archived(service, job.id))
    reason = check_allowed(facts, cmd.type)
    if reason:
        raise CommandRejected(reason)
    parts, data = handler(service, job, facts, cmd.args)
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
    service.store.transition_core(job.id, JobState.IMPLEMENTING, queued=True, reason="approved plan")
    return ["state", "pipeline", "plan"], {"approved_version": service.get(job.id).metadata.get("plan_approved_version")}


def _reject_plan(service, job, facts, args):
    from .plan_ops import reject_plan

    feedback = str(args.get("feedback") or "").strip()
    if not feedback:
        raise CommandError("rejectPlan requires feedback")
    reject_plan(service, job.id, feedback=feedback)
    service.store.transition_core(job.id, JobState.PLANNING, reason="human requested re-plan")
    return ["state", "pipeline", "plan"], {}


def _save_plan_draft(service, job, facts, args):
    from .plan_ops import save_plan_draft

    if "plan" not in args:
        raise CommandError("savePlanDraft requires plan")
    if "version" in args:
        try:
            version = int(args["version"])
        except (TypeError, ValueError) as exc:
            raise CommandError("version must be an integer") from exc
        if version != facts.plan_version:
            raise CommandRejected("plan changed; reload before saving")
    data = save_plan_draft(service, job.id, args["plan"], autosave=bool(args.get("autosave")))
    return ["plan"], data


def _message(service, job, facts, args):
    from .states import effective_state
    text = str(args.get("text") or "").strip()
    if not text:
        raise CommandError("message requires text")
    # Delivered to the live stage by the stage runner (picked up from events).
    service.store.append_event(job.id, "stage_message", reason="human message",
                               payload={"text": text[:8000], "stage": effective_state(facts.core).value})
    return ["session"], {}


def _resume(service, job, facts, args):
    if facts.pending_approval:
        raise CommandRejected("Approve or deny the pending tool request before resuming")
    guidance = str(args.get("guidance") or "").strip()
    if guidance:
        _merge_meta(service, job.id, resume_guidance=guidance)
    if job.status.value == "conflicted":
        _review_service(service).resolve_conflict(job.id, detail=guidance)
    elif facts.core.paused_from in (JobState.PLANNING, JobState.CHECKING, JobState.REVIEWING, JobState.PUSHING):
        target = {JobState.PLANNING: "planning", JobState.CHECKING: "verifying",
                  JobState.REVIEWING: "reviewing", JobState.PUSHING: "publishing"}[facts.core.paused_from]
        service.transition(job.id, target, reason="resumed stage", payload={"guidance": guidance})
    else:
        service.resume(job.id, detail=guidance or "resumed")
    return ["state", "pipeline", "session"], {}


_STAGE_OF_STATE = {JobState.PLANNING: "plan", JobState.IMPLEMENTING: "implement", JobState.REVIEWING: "review"}


def _raise_limit(service, job, facts, args):
    """Approve more of a cap: ``{kind, scope, amount[, stage]}``. Paused on that cap: the paused
    stage continues (same conversation and harness session). Otherwise the cap is raised ahead of
    time. Missing ``kind``/``scope``/``stage`` default to the cap the job is paused on; else cost
    and time mean the job caps, tokens and iterations the current stage's."""
    from ..limits import JOB_KINDS, LimitError, raise_limit, stage_name
    from .states import effective_state
    pending = facts.limit_reached if facts.core.state == JobState.NEEDS_INPUT else None
    pending = pending or {}
    kind = str(args.get("kind") or pending.get("kind") or "").strip().lower()
    if not kind:
        raise CommandError("raiseLimit requires kind (cost, tokens, time or iterations)")
    amount = args.get("amount")
    if amount is None or (isinstance(amount, str) and not amount.strip()):
        raise CommandError("raiseLimit requires amount")
    same_kind = kind == pending.get("kind")
    scope = str(args.get("scope") or (pending.get("scope") if same_kind else "") or "").strip().lower()
    if not scope:
        scope = "job" if kind in JOB_KINDS and getattr(job, "max_cost_usd" if kind == "cost" else
                                                           "max_runtime_seconds", None) else "stage"
    stage = stage_name(args.get("stage") or (pending.get("stage") if same_kind else "") or "")
    if not stage:
        stage = _STAGE_OF_STATE.get(effective_state(facts.core), "implement")
    try:
        record = raise_limit(service, job, kind=kind, scope=scope, stage=stage, amount=amount,
                             by=str(args.get("by") or "human"))
    except LimitError as exc:
        raise CommandError(str(exc)) from exc
    resumes = bool(pending) and same_kind and scope == pending.get("scope") and (
        scope == "job" or stage == stage_name(pending.get("stage")))
    if resumes:
        service.store.patch_metadata(job.id, remove=("limit_reached",))
        fresh = service.get(job.id)
        _resume(service, fresh, facts_for_job(service, fresh), {"guidance": str(args.get("guidance") or "")})
    return ["state", "pipeline", "session", "costs"], {"limit": record, "resumed": resumes}


def _restart_stage(service, job, facts, args):
    from .states import effective_state
    stage = effective_state(facts.core).value
    aliases = {"plan": "planning", "implement": "implementing", "review": "reviewing", "checks": "checking"}
    requested = str(args.get("stage") or stage)
    if aliases.get(requested, requested) != stage:
        raise CommandRejected("restartStage must target the current stage")
    service.store.append_event(job.id, "stage_restart_requested", reason="human restart",
                               payload={"stage": stage})
    if facts.core.state == JobState.NEEDS_INPUT:
        _resume(service, job, facts, args)
    return ["state", "pipeline", "session"], {"stage": stage}


def _retry(service, job, facts, args):
    origin = facts.core.failed_from
    if origin in (JobState.PLAN_REVIEW, JobState.REVIEW_DONE):
        service.store.transition_core(job.id, origin, reason="retry returns to human gate")
    elif origin in (JobState.PLANNING, JobState.CHECKING, JobState.REVIEWING, JobState.PUSHING):
        target = {JobState.PLANNING: "planning", JobState.CHECKING: "verifying",
                  JobState.REVIEWING: "reviewing", JobState.PUSHING: "publishing"}[origin]
        service.transition(job.id, target, reason="retry failed stage")
    else:
        service.retry(job.id, reason="retry requested")
    return ["state", "pipeline"], {}


def _skip_setup(service, job, facts, args):
    """"Continue without setup": the failed template job setup is marked skipped for the
    job's current image and the parked stage resumes."""
    from ..sandbox.job_setup import skip_failed_setup
    skipped = skip_failed_setup(service, job.id)
    fresh = service.get(job.id)
    _resume(service, fresh, facts_for_job(service, fresh), {"guidance": ""})
    return ["state", "pipeline", "session"], {"skipped": skipped}


def _continue_unverified(service, job, facts, args):
    """Review a job whose checks could not all run (job_templates.md §5). The review verdict is
    capped at needs_work and pushing needs an override reason."""
    _merge_meta(service, job.id, checks_unverified=None, continued_unverified=True)
    service.store.append_event(job.id, "continue_unverified", reason="human continued with unverified checks",
                               payload={"reason": str(args.get("reason") or "")[:2000]})
    service.transition(job.id, "reviewing", reason="continue unverified: review with unrunnable checks")
    return ["state", "pipeline", "checks", "review"], {}


def _recheck(service, job, facts, args):
    # Re-run Checks + Review on the current HEAD (no implement run).
    if not job.worktree:
        from ..worktree import JobWorktreeManager
        JobWorktreeManager(service).prepare(job)
    service.store.set_fix_attempts(job.id, 0)
    _merge_meta(service, job.id, checks_trigger="recheck")
    service.transition(job.id, "verifying", reason="recheck requested")
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
    if not isinstance(args.get("finding_ids"), list):
        raise CommandError("finding_ids must be an array")
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
    }, checks_trigger="fix")
    service.store.set_fix_attempts(job.id, 0)
    service.store.transition_core(job.id, JobState.IMPLEMENTING, queued=True, reason="re-prompt with selected findings")
    return ["state", "pipeline", "review"], {"findings": ids}


def _mark_findings(service, job, facts, args):
    if not isinstance(args.get("ids"), list):
        raise CommandError("ids must be an array")
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
    from ..checks import push_check_reason
    from ..verification import VerificationStore
    run = VerificationStore(service.store).latest(job.id)
    reason = push_check_reason(service, job, run=run, allow_unverified=True)
    if reason:
        raise CommandRejected(reason)
    override = str(args.get("override_reason") or "").strip()
    if facts.verdict == "do_not_merge" and not override:
        raise CommandRejected("verdict is do_not_merge: push requires override_reason")
    unverified = run is not None and not run.verified
    if unverified and not override:
        raise CommandRejected("checks are unverified (some could not run): push requires override_reason")
    publish_override = _publish_override(args)
    if override:
        service.store.append_event(job.id, "push_override", reason="human override",
                                   payload={"verdict": facts.verdict, "reason": override[:2000], "unverified": unverified})
    # Always (re)written so a stale override from an earlier push never leaks.
    _merge_meta(service, job.id, publish_override=publish_override,
                unverified_override={"head_sha": run.head_sha, "verification_id": run.id, "reason": override[:2000]}
                if unverified else None)
    service.store.transition_core(job.id, JobState.PUSHING, reason="human push")
    return ["state", "pipeline"], {"publish_override": publish_override}


def _publish_override(args):
    """Validate optional push-dialog fields (contract §2)."""
    title, body = args.get("pr_title"), args.get("pr_body")
    for name, value, limit in (("pr_title", title, 200), ("pr_body", body, 20000)):
        if value is not None and not isinstance(value, str):
            raise CommandError(f"{name} must be a string")
        if isinstance(value, str) and len(value) > limit:
            raise CommandError(f"{name} must be at most {limit} characters")
    draft = args.get("draft", True)
    if draft is None:
        draft = True
    if not isinstance(draft, bool):
        raise CommandError("draft must be a boolean")
    return {"title": (title or "").strip(), "body": (body or "").strip(), "draft": draft}


def _repush(service, job, facts, args):
    """The host PR was closed unmerged: push again and open a NEW PR.

    ``publish.open_draft_pr`` never reuses a closed ``metadata.pr``; the closed
    record stays until the new PR replaces it (its head SHA still proves which
    remote commits this job pushed)."""
    service.store.append_event(job.id, "repush_requested", reason="human re-push after PR closed",
                               payload={"closed_pr": ((job.metadata or {}).get("pr") or {}).get("url", "")})
    service.store.transition_core(job.id, JobState.PUSHING, reason="human re-push (PR closed)")
    return ["state", "pipeline"], {}


def _mark_merged(service, job, facts, args):
    service.store.transition_core(job.id, JobState.DONE, reason="marked merged")
    _release_environments(service, job.id, "job done")
    return ["state", "pipeline"], {}


def _discard(service, job, facts, args):
    _review_service(service).discard(job.id, reason=str(args.get("reason") or "discarded"))
    _release_environments(service, job.id, "job discarded")
    return ["state", "pipeline"], {}


def _release_environments(service, job_id, reason):
    from ..sandbox.environments import cleanup_quietly
    cleanup_quietly(service, job_id, reason)


def _set_environments(service, job, facts, args):
    """Job override (§4 source 1): a list of bindings, or empty/null to fall back to repo/session/settings."""
    from ..sandbox.environments import bindings_from_list, preview, request_refresh
    raw = args.get("environments")
    if raw is not None and not isinstance(raw, list):
        raise CommandError("environments must be a list")
    try:
        bindings = bindings_from_list(raw or [])
    except ValueError as exc:
        raise CommandError(str(exc)) from exc
    execution = dict(job.execution or {})
    if bindings:
        execution["environments"] = [b.to_dict() for b in bindings]
    else:
        execution.pop("environments", None)
    service.store.update_runtime_fields(job.id, execution_json=execution)
    if (job.metadata or {}).get("build_environments"):
        request_refresh(service, job.id)
    fresh = service.get(job.id)
    return ["state"], {"environments": preview(fresh)}


def _refresh_environments(service, job, facts, args):
    from ..sandbox.environments import prepare_in_background, request_refresh
    request_refresh(service, job.id)
    if not args.get("defer"):
        prepare_in_background(service, job.id)
    return ["state"], {"refresh_requested": True}


def _keep_environments(service, job, facts, args):
    keep = args.get("keep")
    if keep is not None and not isinstance(keep, bool):
        raise CommandError("keep must be a boolean")
    keep = (not facts.environments_kept) if keep is None else keep
    _merge_meta(service, job.id, environments_kept=keep)
    service.store.append_event(job.id, "environments_kept" if keep else "environments_unkept",
                               reason="build environments kept past idle retention" if keep else
                               "build environments follow idle retention again")
    return ["state"], {"kept": keep}


def _environment_target(facts, args):
    pending = facts.environment_approval or {}
    if str(args.get("fingerprint") or "") != str(pending.get("fingerprint") or ""):
        raise CommandRejected("environment request changed; reload before responding")
    return pending


def _approve_environment(service, job, facts, args):
    """Explicit human approval of a repository-declared image/Dockerfile (then requeue the parked
    job) or of repository egress (recorded only; a running job keeps running)."""
    from ..sandbox.devcontainer import approve
    pending = _environment_target(facts, args)
    approve(pending["repository"], pending["fingerprint"], kind=pending.get("kind", ""), ref=pending.get("ref", ""))
    service.store.append_event(job.id, "build_environment_approved", reason="human approved build environment",
                               payload={k: pending.get(k, "") for k in ("repository", "fingerprint", "kind", "ref", "label")})
    if "environment_rejected" in (service.get(job.id).metadata or {}):
        service.store.patch_metadata(job.id, remove=("environment_rejected",))
    if pending.get("blocking") is False:
        # Non-blocking (repository egress): the job never stopped for it. Only
        # record the approval; it applies from the next stage — no requeue.
        return ["state", "session"], {"approved": pending["fingerprint"], "requeued": False}
    fresh = service.get(job.id)
    _resume(service, fresh, facts_for_job(service, fresh), {"guidance": ""})
    return ["state", "pipeline", "session"], {"approved": pending["fingerprint"]}


def _reject_environment(service, job, facts, args):
    """Decline the repository's environment; the job stays paused so the user can pick another one."""
    pending = _environment_target(facts, args)
    reason = str(args.get("reason") or "").strip()[:500]
    _merge_meta(service, job.id, environment_rejected=pending["fingerprint"])
    service.store.append_event(job.id, "build_environment_rejected", reason=reason or "human rejected build environment",
                               payload={k: pending.get(k, "") for k in ("repository", "fingerprint", "kind", "ref", "label")})
    return ["state", "session"], {"rejected": pending["fingerprint"]}


def facts_for_job(service, job):
    from .vm import facts_for
    return facts_for(service, job, archived=_archived(service, job.id))


def approve_repository_environment(repository: str, fingerprint: str, *, session: str = "") -> Dict[str, Any]:
    """New job sheet: approve the image/devcontainer ``environments_vm`` currently suggests for ``repository``.

    Only a fingerprint the repository actually declares right now can be approved (raises ``ValueError``)."""
    from types import SimpleNamespace
    from ..sandbox.devcontainer import approve
    from ..sandbox.environments import preview
    repository, fingerprint = str(repository or "").strip(), str(fingerprint or "").strip()
    if not repository or not fingerprint:
        raise ValueError("repository and fingerprint are required")
    probe = SimpleNamespace(id="preview", repository=repository, execution={},
                            metadata={"repository_root": repository, "origin_session": session})
    resolved = preview(probe)
    offered = []
    suggestion = (resolved.get("suggestion") or {}).get("approval")
    if isinstance(suggestion, dict):
        offered.append(suggestion)
    for row in resolved.get("approval_required") or []:
        offered.append({"repository": repository, "fingerprint": row.get("fingerprint"), "kind": "", "ref": row.get("ref")})
    match = next((o for o in offered if str(o.get("fingerprint") or "") == fingerprint), None)
    if match is None:
        raise ValueError("this repository does not currently request that build environment")
    record = approve(str(match.get("repository") or repository), fingerprint, kind=str(match.get("kind") or ""),
                     ref=str(match.get("ref") or ""))
    return {"approved": fingerprint, "record": record}


def _delete(service, job, facts, args):
    from ..management import JobManagementService

    data = JobManagementService(service).delete(job.id, force=True)
    return ["state"], data


def _tool_response(service, job, facts, args, decision):
    try:
        matches = int(args.get("event_id", -1)) == facts.pending_approval["event_id"]
    except (TypeError, ValueError):
        matches = False
    if not matches:
        raise CommandRejected("tool request changed; reload before responding")
    message = args.get("message")
    if message is not None and not isinstance(message, str):
        raise CommandError("message must be a string")
    message = (message or "").strip() if decision == "deny" else ""
    if len(message) > 2000:
        raise CommandError("message must be at most 2000 characters")
    # The reason rides on the interaction response: the stage UI turns a deny
    # with detail into "User denied this tool call. Reason: ..." for the agent,
    # and resume() records it on the human_response event.
    _review_service(service).respond(job.id, decision=decision, detail=message)
    return ["state", "pipeline", "session"], {"message": message} if message else {}


def _mark_viewed(service, job, facts, args):
    from .artifacts import artifacts_for

    path = args.get("path")
    if not isinstance(path, str) or not path.strip():
        raise CommandError("markViewed requires path")
    viewed = args.get("viewed", True)
    if not isinstance(viewed, bool):
        raise CommandError("viewed must be a boolean")
    art = artifacts_for(job)
    if viewed and path not in art.diff_hashes():
        raise CommandError(f"{path!r} is not part of the diff")
    return ["diff"], {"viewed": art.set_viewed(path, viewed)}


_HANDLERS = {
    "approveTool": lambda s, j, f, a: _tool_response(s, j, f, a, "approve"),
    "denyTool": lambda s, j, f, a: _tool_response(s, j, f, a, "deny"),
    "approvePlan": _approve_plan, "rejectPlan": _reject_plan, "savePlanDraft": _save_plan_draft,
    "message": _message, "resume": _resume, "raiseLimit": _raise_limit, "restartStage": _restart_stage, "retry": _retry,
    "recheck": _recheck, "skipSetup": _skip_setup, "continueUnverified": _continue_unverified, "reprompt": _reprompt, "markFindings": _mark_findings, "markViewed": _mark_viewed, "push": _push, "repush": _repush,
    "markMerged": _mark_merged, "discard": _discard, "delete": _delete,
    "setEnvironments": _set_environments, "refreshEnvironments": _refresh_environments,
    "keepEnvironments": _keep_environments,
    "approveEnvironment": _approve_environment, "rejectEnvironment": _reject_environment,
}

__all__ = ["Command", "CommandError", "CommandRejected", "Result", "dispatch", "JobState"]


def save_agent_secret(key: str, value: Optional[str]) -> None:
    """Store (empty value: clear) a harness secret in ``$MUCLI_HOME/secrets.env`` (0600)."""
    from ..harness import write_secret
    write_secret(key, value)


def save_publish_token(provider: str, value: Optional[str]) -> None:
    """Store (empty value: clear) a PR/MR provider token (host-side publish only, 0600)."""
    from ..publish_tokens import write_token
    write_token(provider, value)


def setup_action(step: str, action: str, inputs=None, *, wait: bool = False, on_line=None) -> Dict[str, Any]:
    """Run one setup action (mu.jobs.setup); ``job`` actions block when ``wait``."""
    from ..setup import get_setup
    return get_setup().action(step, action, inputs, wait=wait, on_line=on_line)


def setup_finish() -> Dict[str, Any]:
    from ..setup import get_setup
    return get_setup().finish()
