"""View models (guide §3, Phase 3).

Read-only projections shared by web, mobile and TUI. UIs render these and the
``actions`` list; they never decide availability themselves.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Iterable, List, Optional

from .actions import JobFacts, available_actions
from .artifacts import artifacts_for
from .pipeline import HistoryEntry, derive_pipeline, mini_indicator
from .states import TERMINAL, CoreState, JobState, WAITING, from_legacy

PARTS = ("state", "pipeline", "plan", "diff", "review", "checks", "session", "costs")


def get_default_job_service():
    from ..service import get_default_job_service as get_service
    return get_service()


def resolve_job(service, token):
    try:
        return service.get(token)
    except KeyError:
        matches = [j for j in service.list(limit=1000) if j.id.startswith(token)] if token else []
        if len(matches) > 1:
            raise ValueError(f"job id prefix {token!r} is ambiguous")
        if not matches:
            raise KeyError(token)
        return matches[0]


def receipt_vm(service, job_id):
    from ..receipt import JobReceiptBuilder
    return JobReceiptBuilder(service).build(job_id)


def diff_vm(service, job_id):
    from ..review import build_job_diff
    return build_job_diff(service, job_id).to_dict()


def session_defaults(*, workspace, provider, model, variables):
    from ..job_settings import missing_stages
    stages = {s: {"provider": provider, "model": model} for s in missing_stages({})} if provider and model else {}
    return {"workspace_root": workspace, "execution": {
        **({"stages": stages} if stages else {}),
        "agent_mode": variables.get("agent_mode", "default"),
        "session_type": variables.get("session_type", "workspace"),
        # Jobs always run unattended (mu.jobs.unattended); no per-job auto-approve choice.
    }}


def job_settings_vm():
    """Run rules for the Settings screens: sections of fields with value/display/set flags."""
    from ..job_settings import effective_rules, load_settings, RULES_SCHEMA, rule_overrides, rules_view, settings_path
    data = load_settings()
    return {"sections": rules_view(data), "path": settings_path(),
            "rules": {"schema": RULES_SCHEMA, "values": effective_rules(data), "overrides": rule_overrides(data)}}


def job_settings_text():
    """Plain-text listing of every run rule (``/job settings``)."""
    from ..job_settings import rules_text
    return rules_text()


def toolchains_vm():
    """Settings → Toolchains rows with their derived image status (built/building/failed/missing)."""
    from ..sandbox.toolchain import toolchains_status
    return {"toolchains": toolchains_status()}


def _history(service, job_id: str) -> List[HistoryEntry]:
    """Core-state history derived from legacy ``status_changed`` events."""
    out: List[HistoryEntry] = []
    try:
        events = service.store.list_events_by_type(job_id, ("status_changed", "job_created"))
    except Exception:  # noqa: BLE001 - history is cosmetic (timings)
        return out
    for ev in events:
        if ev.event_type not in ("status_changed", "job_created") or not ev.to_status:
            continue
        payload = ev.payload or {}
        try:
            record = payload.get("core_state")
            core = CoreState(JobState(record["state"])) if record else from_legacy(ev.to_status.value, str(payload.get("attention_reason") or ""))
        except ValueError:
            continue
        state = core.state
        if state == JobState.NEEDS_INPUT and core.paused_from:
            state = JobState.NEEDS_INPUT
        out.append(HistoryEntry(state, float(ev.created_at)))
    return out


def _review(job) -> Optional[Dict[str, Any]]:
    art = artifacts_for(job)
    if not art.exists():
        return None
    return art.read_json("review.json")


def _has_diff(art) -> bool:
    import os
    try:
        return art.exists() and os.path.getsize(art.diff) > 0
    except OSError:
        return False


def _checks_part(service, job) -> Dict[str, Any]:
    art = artifacts_for(job)
    runs: List[Dict[str, Any]] = []
    if art.exists():
        for n in art.check_runs():
            summary = art.read_json(f"checks/run-{n}/summary.json", {}) or {}
            if isinstance(summary, dict):
                runs.append(dict(summary, run=n))
    live = [e.to_dict() for e in service.store.recent_events(job.id) if e.event_type == "checks_output"]
    failed: List[Dict[str, Any]] = []
    latest = runs[-1] if runs else None
    for result in (latest or {}).get("suites") or []:
        if not isinstance(result, dict) or result.get("passed"):
            continue
        suite = str(result.get("suite") or "")
        preexisting = {str(x) for x in result.get("preexisting_failures") or []}
        cases = [c for c in ((result.get("junit") or {}).get("cases") or [])
                 if isinstance(c, dict) and c.get("status") == "failed"]
        for case in cases:
            cid = str(case.get("id") or "")
            failed.append({"run": latest["run"], "suite": suite, "name": str(case.get("name") or cid.split("::")[-1]),
                           "id": cid, "message": str(case.get("message") or ""),
                           "classname": str(case.get("suite") or ""), "new": cid not in preexisting})
        if not cases:
            # No per-test evidence (no JUnit): the suite itself is the failure.
            log = str(result.get("stderr") or result.get("stdout") or "")
            failed.append({"run": latest["run"], "suite": suite, "name": suite, "id": suite,
                           "message": log[-4000:].strip() if not result.get("timed_out") else "timed out",
                           "classname": "", "new": bool(result.get("new_failures", True))})
    history: Dict[str, List[Dict[str, Any]]] = {}
    for run in runs[-20:]:
        for result in run.get("suites") or []:
            if not isinstance(result, dict) or not result.get("suite"):
                continue
            duration = result.get("duration_ms")
            history.setdefault(str(result["suite"]), []).append({
                "run": run["run"], "passed": bool(result.get("passed")),
                "duration_ms": int(duration) if isinstance(duration, (int, float)) else None,
                "finished_at": run.get("finished_at"),
            })
    return {"runs": runs, "live": live, "failed_cases": failed, "history": history}


def facts_for(service, job, core: Optional[CoreState] = None, *, archived: bool = False) -> JobFacts:
    core = core or service.store.core_state(job.id)
    meta = dict(job.metadata or {})
    art = artifacts_for(job)
    review = art.read_json("review.json") if art.exists() else None
    open_findings = 0
    if review:
        open_findings = sum(1 for f in review.get("findings") or [] if f.get("disposition", "open") == "open")
    has_plan = bool(meta.get("plan")) or (art.exists() and art.read_text("plan.md") is not None)
    plan_version = 0
    if has_plan:
        try:
            from .plan_ops import plan_store

            plan_version = plan_store(job).latest_version()
        except (OSError, ValueError):
            plan_version = 0
    from ..checks import push_check_reason
    approval = None
    if core.state == JobState.NEEDS_INPUT and job.attention_reason.value == "approval_required":
        gates = service.store.list_events_by_type(job.id, ("status_changed",))
        gate = next((e for e in reversed(gates) if e.to_status and e.to_status.value == "needs_human"), None)
        if gate and gate.payload.get("tool_name"):
            approval = {**gate.payload, "event_id": gate.id}
    return JobFacts(
        core=core,
        archived=archived,
        has_plan=has_plan,
        plan_version=plan_version,
        has_review=bool(review),
        verdict=str((review or {}).get("verdict") or ""),
        open_findings=open_findings,
        has_branch=bool(job.branch or meta.get("review_branch")),
        has_worktree=bool(job.worktree),
        live_stage=bool(job.worker_id) and core.state in (JobState.PLANNING, JobState.IMPLEMENTING,
                                                          JobState.REVIEWING),
        git_workspace=True,
        pending_approval=approval,
        has_diff=_has_diff(art),
        has_worker=bool(job.worker_id),
        environments=len(meta.get("build_environments") or []) or len(((job.execution or {}).get("environments") or [])),
        environments_kept=bool(meta.get("environments_kept")),
        push_check_reason=push_check_reason(service, job, allow_unverified=True) if core.state == JobState.REVIEW_DONE
        else "push is available after review",
        environment_approval=environment_approval(service, job, core),
        pr_closed=isinstance(meta.get("pr"), dict) and meta["pr"].get("state") == "closed",
        limit_reached=_limit(job, core),
        checks_unverified=_checks_unverified(art),
        unverified_pending=core.state == JobState.NEEDS_INPUT and bool(meta.get("checks_unverified"))
        and str(getattr(job.attention_reason, "value", job.attention_reason) or "") == "verification_required",
        setup_failed=_setup_failed(job, core),
        environment_repair=_environment_repair(job),
    )


def _environment_repair(job) -> Optional[Dict[str, Any]]:
    """The latest repair that committed an image (the repair card's actions)."""
    from ..sandbox.repair import latest_repair
    record = latest_repair(job, committed=True)
    if not record:
        return None
    return {k: record.get(k) for k in ("attempt", "binding", "source", "template", "project_commands", "image",
                                       "outcome")}


def _checks_unverified(art) -> bool:
    """The latest check run has unrunnable checks (honest gate, job_templates.md §5)."""
    try:
        summary = art.read_json("checks/summary.json", {}) if art.exists() else {}
    except (OSError, ValueError):
        return False
    return isinstance(summary, dict) and summary.get("verified") is False


def _setup_failed(job, core: CoreState) -> Optional[Dict[str, Any]]:
    if core.state != JobState.NEEDS_INPUT:
        return None
    if str(getattr(job.attention_reason, "value", job.attention_reason) or "") != "environment_failure":
        return None
    from ..sandbox.job_setup import failed_setup
    return failed_setup(job)


def _limit(job, core: CoreState) -> Optional[Dict[str, Any]]:
    """The cap a paused job waits on (``metadata.limit_reached`` while attention is limit_reached)."""
    if core.state != JobState.NEEDS_INPUT:
        return None
    from ..limits import pending
    return pending(job)


def environment_approval(service, job, core: Optional[CoreState] = None) -> Optional[Dict[str, Any]]:
    """The repository environment approval this job waits on, or ``None``.

    * Blocking: the repository image/devcontainer of a job parked on an
      environment failure — latest ``build_environment_approval_required``
      event (payload = ``devcontainer.approval_request``). Approving requeues.
    * Non-blocking: a repository image/devcontainer/environments list the job
      skipped because it was not approved (jobs run unattended) — latest
      ``repository_environment_skipped`` event. The job runs in the next
      environment source; approving records it for future jobs.
    * Non-blocking: repository-declared egress (``[sandbox] egress``) — latest
      ``repository_egress_approval_required`` event of any non-terminal job.
      Stages keep running without the egress; approving only records it, and
      it applies from the next stage.

    Either one only while its fingerprint is neither approved nor rejected."""
    core = core or service.store.core_state(job.id)
    if core.state in TERMINAL:
        return None
    parked = (core.state == JobState.NEEDS_INPUT and
              str(getattr(job.attention_reason, "value", job.attention_reason) or "") == "environment_failure")
    if parked:
        found = _pending_approval(service, job, "build_environment_approval_required")
        if found is not None:
            return {**found, "blocking": True}
    found = _pending_approval(service, job, "repository_environment_skipped")
    if found is not None:
        return {**found, "blocking": False}
    found = _pending_approval(service, job, "repository_egress_approval_required")
    return {**found, "blocking": False} if found is not None else None


def _pending_approval(service, job, event_type: str) -> Optional[Dict[str, Any]]:
    try:
        events = service.store.list_events_by_type(job.id, (event_type,))
    except Exception:  # noqa: BLE001
        return None
    if not events:
        return None
    ev = events[-1]
    payload = dict(ev.payload or {})
    fp, repo = str(payload.get("fingerprint") or ""), str(payload.get("repository") or "")
    if not fp or not repo:
        return None
    if str((job.metadata or {}).get("environment_rejected") or "") == fp:
        return None
    try:
        from ..sandbox.devcontainer import is_approved
        if is_approved(repo, fp):
            return None
    except Exception:  # noqa: BLE001
        return None
    return {"repository": repo, "fingerprint": fp, "kind": str(payload.get("kind") or ""),
            "ref": str(payload.get("ref") or ""), "label": str(payload.get("label") or ""),
            "origin": str(payload.get("origin") or ""),
            "ignored_features": list(payload.get("ignored_features") or []),
            "message": str(ev.reason or ""), "event_id": ev.id}


def _pr_summary(job) -> Optional[Dict[str, Any]]:
    pr = (job.metadata or {}).get("pr")
    if not isinstance(pr, dict) or not (pr.get("url") or pr.get("number")):
        return None
    return {k: pr.get(k) for k in ("url", "number", "provider", "draft", "state", "merged_at", "checked_at")
            if pr.get(k) not in (None, "")}


def budget_vm(service, job, *, now: Optional[float] = None) -> Dict[str, Any]:
    """Header: running cost vs the job cap and worker-active time vs the runtime cap."""
    art = artifacts_for(job)
    cost = max(float(job.cost_usd or 0.0), sum(art.stage_costs().values()) if art.exists() else 0.0)
    try:
        active = service.store.active_runtime_seconds(job.id, now=now)
    except Exception:  # noqa: BLE001 - timing is cosmetic
        active = None
    if active is None:
        active = 0.0
    cap = float(job.max_cost_usd) if job.max_cost_usd else None
    return {"cost_usd": round(cost, 6), "max_cost_usd": cap,
            "cost_fraction": round(cost / cap, 4) if cap else None,
            "active_seconds": round(float(active), 1),
            "max_runtime_seconds": int(job.max_runtime_seconds) if job.max_runtime_seconds else None}


def _live_diff_eligible(core: CoreState) -> bool:
    from .states import ACTIVE
    return core.state in ACTIVE or core.state == JobState.NEEDS_INPUT


def _environments_summary(job) -> Dict[str, Any]:
    """Header pill: ``⬢ 3 environments`` (+ kept / status), from job metadata only."""
    from ..sandbox.environments import describe
    info = describe(job)
    statuses = {b["status"] for b in info["bindings"]}
    status = next((s for s in ("failed", "preparing", "cleaned", "ready") if s in statuses), "")
    return {"count": info["count"], "kept": info["kept"], "source": info["source"], "summary": info["summary"],
            "status": status, "bytes": info["bytes"]}


def environments_vm(service=None, *, repository: str = "", session: str = "", environments=None,
                    include_images: bool = True) -> Dict[str, Any]:
    """Build environments pickers + the set a new job would resolve to (New job sheet), and kept jobs."""
    from types import SimpleNamespace
    from ..sandbox.environments import kept_jobs, list_sources, preview
    probe = SimpleNamespace(id="preview", repository=repository, metadata={"repository_root": repository, "origin_session": session},
                            execution={"environments": environments} if environments else {})
    out = {**list_sources(include_images=include_images), "resolved": preview(probe)}
    out["remembered"] = None
    if repository:
        from ..sandbox.environments import remembered_template, template_fit
        out["remembered"] = remembered_template(repository)
        detected = (out["resolved"] or {}).get("detected") or {}
        for row in out.get("templates") or []:
            row["fit"] = template_fit(row.get("egress_allow") or [], detected)
    if service is not None:
        out["kept_jobs"] = kept_jobs(service)
    return out


def job_environments_vm(service, job_id: str) -> Dict[str, Any]:
    """The job page's environments panel."""
    from ..sandbox.environments import describe
    job = service.get(job_id)
    return {"id": job.id, **describe(job)}


def _stage_agents(job) -> Dict[str, Any]:
    """Agent per stage (mucli / Claude Code / Codex) and its model, for the job page."""
    from ..harness import AGENT_LABELS, stage_agents
    try:
        agents = stage_agents(job)
    except (OSError, ValueError, KeyError):
        return {}
    return {s: {**cfg, "label": AGENT_LABELS.get(cfg["agent"], cfg["agent"])} for s, cfg in agents.items()}


def _summary(service, job, *, now: float) -> Dict[str, Any]:
    from ..models import resolve_stage_model
    core = service.store.core_state(job.id)
    art = artifacts_for(job)
    steps = derive_pipeline(core, _history(service, job.id), now=now, costs=art.stage_costs(),
                            plan_approval=bool((job.execution or {}).get("plan_approval", True)))
    review = _review(job) or {}
    meta = dict(job.metadata or {})
    jira = {**dict(meta.get("jira") or {}), **dict(meta.get("ticket") or {})}
    return {
        "id": job.id,
        "key": str(jira.get("key") or meta.get("jira_key") or ""),
        "title": job.title,
        "state": core.state.value,
        "paused_from": core.paused_from.value if core.paused_from else None,
        "failed_from": core.failed_from.value if core.failed_from else None,
        "legacy_status": job.status.value,
        "attention_detail": job.attention_detail,
        "mini": mini_indicator(steps),
        "steps": [s.to_dict() for s in steps],
        "verdict": review.get("verdict") or "",
        "cost_usd": max(float(job.cost_usd or 0.0), sum(art.stage_costs().values())),
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "repository": job.repository,
        "origin_session": str((job.metadata or {}).get("origin_session") or ""),
        "environments": _environments_summary(job),
        "stage_models": {s: resolve_stage_model(job.execution, s) for s in ("plan", "implement", "review")},
        "stage_agents": _stage_agents(job),
        "max_cost_usd": float(job.max_cost_usd) if job.max_cost_usd else None,
        # The cap a paused job waits on: {kind, scope, stage, used, limit, unit, message, summary, ...}.
        "limit": _limit(job, core),
        "pr": _pr_summary(job),
        "actions": [a.to_dict() for a in available_actions(facts_for(service, job, core))],
    }


def job_vm(service, job_id: str, *, parts: Optional[Iterable[str]] = None,
           now: Optional[float] = None, plan_version: Optional[int] = None) -> Dict[str, Any]:
    """Full job page VM, or only the requested ``parts`` (change-stream refetch)."""
    job = service.get(job_id)
    from .artifacts import migrate_metadata
    job = migrate_metadata(service, job)
    now = time.time() if now is None else now
    wanted = set(parts or PARTS) | {"state"}
    core = service.store.core_state(job.id)
    out: Dict[str, Any] = {"id": job.id, "version": job.version}
    if "state" in wanted or "pipeline" in wanted:
        out.update(_summary(service, job, now=now))
        out["branch"] = job.branch
        out["worktree"] = job.worktree
        out["fix_attempts"] = core.fix_attempts
        out["pipeline"] = out.get("steps", [])
        from ..sandbox.environments import describe
        out["build_environments"] = describe(job)
        facts = facts_for(service, job, core)
        out["actions"] = [a.to_dict() for a in available_actions(facts)]
        out["environment_approval"] = facts.environment_approval
        # Jobs run unattended: a failed template job setup is a warning (the job continued).
        from ..sandbox.job_setup import setup_warning
        out["setup_warning"] = setup_warning(job)
        out["budget"] = budget_vm(service, job, now=now)
    if "plan" in wanted:
        try:
            from .plan_ops import plan_view

            out["plan"] = plan_view(job, plan_version)
        except (OSError, ValueError) as exc:
            out["plan"] = {"error": str(exc)}
    if "review" in wanted:
        out["review"] = _review(job)
    if "checks" in wanted:
        out["checks"] = _checks_part(service, job)
        from ..sandbox.repair import describe_repairs
        out["checks"]["repairs"] = describe_repairs(job)
    if "costs" in wanted:
        art = artifacts_for(job)
        out["costs"] = {"total_usd": max(float(job.cost_usd or 0.0), sum(art.stage_costs().values())),
                        "by_stage": art.stage_costs() if art.exists() else {},
                        "events": [e.to_dict() for e in service.store.recent_events(job.id)]}
    if "diff" in wanted:
        art = artifacts_for(job)
        out["diff"] = {"patch": (art.read_text("diff.patch") if art.exists() else None),
                       "viewed": art.viewed() if art.exists() else [], "live": None}
        if _live_diff_eligible(core):
            from .artifacts import live_diff
            try:
                out["diff"]["live"] = live_diff(job)
            except Exception:  # noqa: BLE001 - the checkpointed diff still renders
                out["diff"]["live"] = None
    if "session" in wanted:
        art = artifacts_for(job)
        stages = {s: art.read_json(f"logs/{s}-transcript.json", []) or [] for s in ("plan", "implement", "review")}
        # Environment repair (job_templates.md §7): the latest attempt's conversation.
        repair = art.read_json("logs/repair-transcript.json", []) if art.exists() else []
        if repair:
            stages["repair"] = repair
        events = [e.to_dict() for e in service.store.recent_events(job.id) if e.event_type in
                  ("sandbox_event", "stage_message", "sandbox_starting", "sandbox_stopped", "runtime_error",
                   "build_environments_preparing", "build_environment_progress", "build_environment_ready",
                   "build_environment_failed", "build_environments_recreated", "environment_command_started",
                   "environment_command", "environment_setup", "environment_setup_dirty", "environment_setup_skipped",
                   # Jobs run unattended: automatic decisions and non-blocking fallbacks.
                   "auto_permission", "auto_answered", "environment_setup_failed", "repository_environment_skipped",
                   "build_environment_skipped", "checks_unverified", "environment_repair")
                  # The stage's own copy of an automatic decision is shown once, as the job event.
                  and not (e.event_type == "sandbox_event"
                           and (e.payload or {}).get("event") in ("auto_permission", "auto_answered"))]
        out["session"] = {"session_name": job.session_name, "live": bool(job.worker_id),
                          "stages": stages, "events": events,
                          "approval": facts_for(service, job, core).pending_approval}
    return out


def jobs_vm(service, *, limit: int = 200, now: Optional[float] = None) -> Dict[str, Any]:
    now = time.time() if now is None else now
    jobs = service.list_unarchived(limit=limit)
    rows = [_summary(service, job, now=now) for job in jobs]
    return {"count": len(rows), "jobs": rows}


def home_vm(service, *, limit: int = 200, now: Optional[float] = None) -> Dict[str, Any]:
    """Dashboard: Needs you / Running now / Jobs (guide §8.2)."""
    data = jobs_vm(service, limit=limit, now=now)
    waiting = {s.value for s in WAITING} | {JobState.FAILED.value}
    running = {"provisioning", "planning", "implementing", "checking", "reviewing", "pushing"}
    primary = {"plan_review": "approvePlan", "review_done": "push", "needs_input": "resume",
               "failed": "retry"}
    # A job paused on a cap shows "Limit reached: cost $100.03 / $100.00" and offers "Approve more".
    needs = [dict(r, primary_action="raiseLimit", attention_summary=r["limit"]["summary"]) if r.get("limit") else
             dict(r, primary_action=primary.get(r["state"], "")) for r in data["jobs"] if r["state"] in waiting]
    # A pushed job whose PR was closed without merging (parked on a human with
    # attention ``pr_closed`` by the merge poll) needs a decision: re-push
    # (opens a new PR) or mark done.
    needs += [dict(r, primary_action="repush", attention_detail=r.get("attention_detail") or "PR closed without merging")
              for r in data["jobs"] if r["state"] == "pushed" and (r.get("pr") or {}).get("state") == "closed"]
    return {
        "needs_you": needs,
        "running": [r for r in data["jobs"] if r["state"] in running],
        "jobs": data["jobs"],
        "counts": {"needs_you": len(needs), "total": data["count"]},
    }


# ------------------------------------------------------------ parity additions
def push_preview_vm(service, job_id: str) -> Dict[str, Any]:
    """Push dialog defaults (contract §2)."""
    from ..publish import publish_preview
    return publish_preview(service, service.get(job_id))


def activity_vm(service, *, now: Optional[float] = None) -> Dict[str, Any]:
    """Agents, checks and containers (contract §5)."""
    from .activity import activity
    return activity(service, now=now)


def activity_status_vm(service) -> Dict[str, Any]:
    from .activity import status
    return status(service)


def activity_logs_vm(service, job_id: str, *, tail_bytes: int = 65536) -> Dict[str, Any]:
    from .activity import job_logs
    return job_logs(service, job_id, tail_bytes=tail_bytes)


def remove_container(service, name: str) -> Dict[str, Any]:
    """Raises ``ValueError`` (bad name), ``KeyError`` (unknown) or
    ``CommandRejected`` (job still active)."""
    from .activity import remove_container as remove
    return remove(service, name)


def publish_tokens_status() -> Dict[str, Dict[str, str]]:
    """Where each PR/MR provider token is set (environment / stored / unset); never values."""
    from ..publish_tokens import status
    return status()


def agents_status() -> Dict[str, Any]:
    """Agent harness setup/status (mucli / Claude Code / Codex) for the terminal and GUI surfaces."""
    from ..harness import status
    return status()


def parse_agent_spec(text: str) -> Dict[str, Dict[str, str]]:
    """``claude`` or ``plan=claude,implement=codex[:model]`` → ``execution.stage_agents`` (raises ValueError)."""
    from ..harness import parse_agent_spec as parse
    return parse(text)


def setup_vm(*, recheck: bool = False) -> Dict[str, Any]:
    """Guided jobs setup checklist (see mu.jobs.setup)."""
    from ..setup import get_setup
    setup = get_setup()
    if recheck or not setup.status:
        setup.recheck()
    return setup.vm()


def doctor_vm(*, deep: bool = False) -> List[Dict[str, Any]]:
    """Jobs doctor report: ``[{name, status: ok|warn|fail, detail}]``."""
    from ..setup import doctor
    return doctor(deep=deep)


def needs_setup() -> bool:
    from ..setup import needs_setup as needed
    return needed()
