"""Plan gate operations (guide §5, Phase 2).

``save_plan_draft`` / ``approve_plan`` / ``reject_plan`` are the only paths that
change a plan. ``commands.dispatch`` (Phase 3) calls these; legacy routes do too.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .artifacts import artifacts_for, backfill
from .plan_doc import CheckSpec, Criterion, PlanDoc, PlanStore, PlanValidationError, diff_versions


class PlanConflictError(RuntimeError):
    """Stale ``version`` on approve (HTTP 409)."""


class PlanGateError(RuntimeError):
    """Job is not at the plan gate."""


def _allowed(job: Any) -> frozenset:
    from ..dynamic_checks import allowed_executables

    return allowed_executables(job)


def plan_store(job: Any) -> PlanStore:
    art = artifacts_for(job)
    if not art.exists() or not PlanStore(art).versions():
        backfill(job)
    return PlanStore(art)


def record_planner_plan(job: Any, plan: Dict[str, Any]) -> Optional[PlanDoc]:
    """Store the planner's output as a new version (author=planner).

    Invalid planner output is still stored as the draft so a human can fix it
    in the editor; validation is enforced on human save/approve.
    """
    store = PlanStore(artifacts_for(job).ensure())
    doc = PlanDoc.from_legacy(plan, job)
    try:
        return store.save(doc, author="planner", allowed=_allowed(job))
    except PlanValidationError:
        store.save_draft(doc.render())
        return None


def _parse_input(plan: Any) -> PlanDoc:
    if isinstance(plan, PlanDoc):
        return plan
    if isinstance(plan, str):
        return PlanDoc.parse(plan)
    if isinstance(plan, dict):
        return PlanDoc.from_dict(plan)
    raise PlanValidationError(["plan must be markdown text or a structured object"])


def plan_view(job: Any, version: Optional[int] = None) -> Dict[str, Any]:
    store = plan_store(job)
    versions = store.versions()
    latest = store.load(version)
    approved = int((getattr(job, "metadata", None) or {}).get("plan_approved_version") or 0)
    current_version = latest.version if latest else 0
    previous = [v for v in versions if v < current_version]
    prev_text = store.load_text(previous[-1]) if previous else ""
    cur_text = store.load_text(version) or ""
    return {
        "versions": versions,
        "version": latest.version if latest else 0,
        "latest_version": store.latest_version(),
        "approved_version": approved,
        "doc": latest.to_dict() if latest else None,
        "markdown": cur_text,
        "draft": store.load_draft() if version is None else None,
        "diff_from_previous": diff_versions(prev_text or "", cur_text) if prev_text else "",
        "errors": latest.validate(_allowed(job)) if latest else ["no plan yet"],
    }


def save_plan_draft(service, job_id: str, plan: Any, *, autosave: bool = False) -> Dict[str, Any]:
    """Autosave → ``plans/draft.md`` only. Explicit save → validated new version."""
    job = service.get(job_id)
    store = plan_store(job)
    if autosave:
        store.save_draft(plan if isinstance(plan, str) else _parse_input(plan).render())
        return {"saved": "draft", "version": store.latest_version()}
    doc = store.save(_parse_input(plan), author="human", allowed=_allowed(job))
    service.store.append_event(job_id, "plan_saved", reason="plan edited",
                               payload={"version": doc.version, "author": "human"})
    return {"saved": "version", "version": doc.version}


def approve_plan(service, job_id: str, *, version: int, edited_plan: Any = None,
                 detail: str = "") -> Any:
    job = service.get(job_id)
    store = plan_store(job)
    if edited_plan is not None:
        if int(version) != store.latest_version():
            raise PlanConflictError(f"plan changed: approving v{version}, latest is v{store.latest_version()}")
        doc = store.save(_parse_input(edited_plan), author="human", allowed=_allowed(job))
        version = doc.version
    latest = store.latest_version()
    if int(version) != latest:
        raise PlanConflictError(f"plan changed: approving v{version}, latest is v{latest}")
    doc = store.load(latest)
    if doc is None:
        raise PlanGateError("no plan to approve")
    doc.ensure_valid(_allowed(job))  # re-validate at approval time
    meta = dict(job.metadata or {})
    meta["plan_approved_version"] = latest
    # Compatibility: downstream legacy readers still look at the contract.
    acceptance = [f"{c.id}: {c.text}" for c in doc.acceptance]
    commands = [c.cmd for c in doc.checks]
    service.store.update_runtime_fields(job_id, metadata_json=meta)
    if acceptance or commands:
        fields: Dict[str, Any] = {}
        if acceptance:
            fields["acceptance_json"] = acceptance
        if commands:
            fields["validation_json"] = commands
        try:
            service.store.update_runtime_fields(job_id, **fields)
        except (TypeError, ValueError):
            pass
    return service.store.append_event(job_id, "plan_version_approved", reason="human approved plan",
                                      payload={"version": latest, "detail": detail})


def reject_plan(service, job_id: str, *, feedback: str) -> Any:
    job = service.get(job_id)
    store = plan_store(job)
    meta = dict(job.metadata or {})
    meta["replan_feedback"] = str(feedback or "").strip()
    meta["replan_base_version"] = store.latest_version()
    service.store.update_runtime_fields(job_id, metadata_json=meta)
    return service.store.append_event(job_id, "plan_rejected", reason="human requested re-plan",
                                      payload={"feedback": meta["replan_feedback"],
                                               "version": meta["replan_base_version"]})


def approved_plan_text(job: Any) -> Optional[str]:
    """Approved plan.md verbatim for the implement prompt (guide §5.4)."""
    version = int((getattr(job, "metadata", None) or {}).get("plan_approved_version") or 0)
    art = artifacts_for(job)
    if not art.exists():
        return None
    store = PlanStore(art)
    if version:
        return store.load_text(version)
    return None


def legacy_approval_version(job: Any, value: Optional[Dict[str, Any]] = None) -> int:
    """Compat for the legacy plan gate (respond decision=approve).

    Edited criteria/commands become a new human version; returns the version
    to record as approved (0 when the job has no plan artifacts).
    """
    value = dict(value or {})
    try:
        store = plan_store(job)
    except (OSError, ValueError):
        return 0
    latest = store.load()
    if latest is None:
        return 0
    if "acceptance_criteria" in value or "validation_commands" in value:
        doc = PlanDoc.from_dict(latest.to_dict())
        if "acceptance_criteria" in value:
            doc.acceptance = [Criterion(f"AC-{i}", str(t).strip())
                              for i, t in enumerate(value["acceptance_criteria"] or [], 1) if str(t).strip()]
        if "validation_commands" in value:
            doc.checks = [CheckSpec(f"check{i}", str(c).strip())
                          for i, c in enumerate(value["validation_commands"] or [], 1) if str(c).strip()]
        try:
            return store.save(doc, author="human", allowed=_allowed(job)).version
        except PlanValidationError:
            return store.latest_version()
    return store.latest_version()
