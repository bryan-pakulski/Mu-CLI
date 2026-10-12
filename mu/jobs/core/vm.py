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
from .states import CoreState, JobState, WAITING, from_legacy

PARTS = ("state", "pipeline", "plan", "diff", "review", "checks", "session", "costs")


def _history(service, job_id: str) -> List[HistoryEntry]:
    """Core-state history derived from legacy ``status_changed`` events."""
    out: List[HistoryEntry] = []
    try:
        events = service.events(job_id, limit=500)
    except Exception:  # noqa: BLE001 - history is cosmetic (timings)
        return out
    for ev in events:
        if ev.event_type not in ("status_changed", "job_created") or not ev.to_status:
            continue
        payload = ev.payload or {}
        try:
            core = from_legacy(ev.to_status.value, str(payload.get("attention_reason") or ""))
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
        push_check_reason=push_check_reason(service, job) if core.state == JobState.REVIEW_DONE else "push is available after review",
    )


def _summary(service, job, *, now: float) -> Dict[str, Any]:
    core = service.store.core_state(job.id)
    steps = derive_pipeline(core, _history(service, job.id), now=now,
                            plan_approval=bool((job.execution or {}).get("plan_approval", True)))
    review = _review(job) or {}
    meta = dict(job.metadata or {})
    jira = dict(meta.get("jira") or {})
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
        "cost_usd": float(job.cost_usd or 0.0),
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "repository": job.repository,
    }


def job_vm(service, job_id: str, *, parts: Optional[Iterable[str]] = None,
           now: Optional[float] = None) -> Dict[str, Any]:
    """Full job page VM, or only the requested ``parts`` (change-stream refetch)."""
    job = service.get(job_id)
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
        out["actions"] = [a.to_dict() for a in available_actions(facts_for(service, job, core))]
    if "plan" in wanted:
        try:
            from .plan_ops import plan_view

            out["plan"] = plan_view(job)
        except (OSError, ValueError) as exc:
            out["plan"] = {"error": str(exc)}
    if "review" in wanted:
        out["review"] = _review(job)
    if "checks" in wanted:
        art = artifacts_for(job)
        runs = []
        if art.exists():
            for n in art.check_runs():
                summary = art.read_json(f"checks/run-{n}/summary.json", {}) or {}
                runs.append(dict(summary, run=n))
        out["checks"] = {"runs": runs}
    if "costs" in wanted:
        art = artifacts_for(job)
        out["costs"] = {"total_usd": float(job.cost_usd or 0.0),
                        "by_stage": art.stage_costs() if art.exists() else {}}
    if "diff" in wanted:
        art = artifacts_for(job)
        out["diff"] = {"patch": (art.read_text("diff.patch") if art.exists() else None)}
    if "session" in wanted:
        out["session"] = {"session_name": job.session_name, "live": bool(job.worker_id)}
    return out


def jobs_vm(service, *, limit: int = 200, now: Optional[float] = None) -> Dict[str, Any]:
    now = time.time() if now is None else now
    jobs = service.list(limit=limit) if hasattr(service, "list") else service.store.list_jobs(limit=limit)
    rows = [_summary(service, job, now=now) for job in jobs]
    return {"count": len(rows), "jobs": rows}


def home_vm(service, *, limit: int = 200, now: Optional[float] = None) -> Dict[str, Any]:
    """Dashboard: Needs you / Running now / Jobs (guide §8.2)."""
    data = jobs_vm(service, limit=limit, now=now)
    waiting = {s.value for s in WAITING} | {JobState.FAILED.value}
    running = {"provisioning", "planning", "implementing", "checking", "reviewing", "pushing"}
    primary = {"plan_review": "approvePlan", "review_done": "push", "needs_input": "resume",
               "failed": "retry"}
    needs = [dict(r, primary_action=primary.get(r["state"], "")) for r in data["jobs"] if r["state"] in waiting]
    return {
        "needs_you": needs,
        "running": [r for r in data["jobs"] if r["state"] in running],
        "jobs": data["jobs"],
        "counts": {"needs_you": len(needs), "total": data["count"]},
    }
