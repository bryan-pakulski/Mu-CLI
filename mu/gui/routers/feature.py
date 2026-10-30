"""Feature-mode state and user actions (``mu.feature.engine``).

``GET /api/feature/state`` returns the active plan (``plan_view``: status,
approval, tasks with criteria and evidence, reviews, diff proposals, events,
completion gaps), the session's features, open blockers and the check runs the
plan cites. Every mutation calls the engine with the user surface as the actor,
so the panel, mobile and ``/feature`` share one set of gates:

``POST /approve``                          approve the draft (user-only)
``POST /tasks/{id}/start``                 start a task
``POST /tasks/{id}/transition``            generic move (routes through the engine)
``POST /tasks/{id}/criteria/{cid}/attest`` the user marks a criterion met (recorded)
``POST /tasks/{id}/complete``              complete; ``override_reason`` records an override
``POST /diffs/{id}/decide``                approve / reject a diff proposal
``POST /complete``                         mark the feature complete (preconditions enforced)

Feature records (``list/load/unload/archive/unarchive/delete/preview/create``)
are the session's pointers to plan files.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from mu.feature import engine
from mu.session.helpers import _slugify_feature_id

from ..mode_session import mode_session, mode_session_lock
from .mode_state import check_payload, save, states_for, surface_actor

router = APIRouter()


def _features_list(sm) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for fid, record in (sm.feature_registry or {}).items():
        if not isinstance(record, dict):
            continue
        out.append({
            "feature_id": fid,
            "feature_name": record.get("feature_name") or fid,
            "status": record.get("status"),
            "task_count": record.get("task_count", 0),
            "completed_count": record.get("completed_count", 0),
            "is_active": fid == sm.active_feature_id,
            "archived": bool(record.get("archived")),
            "created_at": record.get("created_at") or 0,
            "updated_at": record.get("updated_at"),
        })
    out.sort(key=lambda f: (f["archived"], not f["is_active"], -(f["created_at"] or 0)))
    return out


def _runs_for(states: Any, view: Dict[str, Any]) -> List[Dict[str, Any]]:
    checks = states.get("checks")
    ids = set()
    for task in view["tasks"]:
        for crit in task["criteria"]:
            ids |= {e["ref"] for e in crit["evidence"] if e.get("kind") == "run"}
    ids |= {r["check_run"] for r in view["review_records"] if r.get("check_run")}
    if view.get("completion") and view["completion"].get("suite_run"):
        ids.add(view["completion"]["suite_run"])
    suite = [r.id for r in checks.list(purpose="suite", ref=view["feature_id"])]
    ids |= set(suite[-3:])
    return [check_payload(checks.get(i)) for i in sorted(ids, key=lambda x: int(x[1:]) if x[1:].isdigit() else 0)
            if checks.get(i) is not None]


def feature_payload(session: Any, plan: Optional[engine.FeaturePlan] = None, *, read_only: bool = False) -> Dict[str, Any]:
    sm = session.session_manager
    active = str(sm.variables.get("agent_mode", "default")) == "feature"
    plan = plan if plan is not None else engine.active_plan(session, cached=True)
    states = sm.mode_state
    blockers = [b.to_dict() for b in states.get("blockers").open_blockers()]
    if plan is None:
        return {"active": active, "active_feature_id": sm.active_feature_id, "plan": None,
                "features": _features_list(sm), "blockers": blockers, "runs": [], "read_only": read_only}
    view = engine.plan_view(plan, session)
    task_refs = {f"t{t['id']}" for t in view["tasks"]}
    return {
        "active": active,
        "active_feature_id": sm.active_feature_id,
        "plan": view,
        "features": _features_list(sm),
        "blockers": [b for b in blockers if b["ref"] in task_refs or b["mode"] == "feature"],
        "runs": _runs_for(states, view),
        "read_only": read_only,
    }


@router.get("/state")
def get_feature_state(request: Request) -> Dict[str, Any]:
    session = mode_session(request)
    if session is None or getattr(session.session_manager, "mode_state", None) is None:
        return {"active": False, "active_feature_id": None, "plan": None, "features": [], "blockers": [],
                "runs": [], "read_only": False}
    return feature_payload(session)


def _act(request: Request, fn) -> Dict[str, Any]:
    from mu.modes.events import GateError

    session, _states = states_for(request)
    with mode_session_lock(request, session):
        try:
            fn(session, surface_actor(request))
        except GateError as exc:
            raise HTTPException(status_code=409, detail={"message": str(exc), "code": exc.code, **(exc.data or {})})
        except (ValueError, KeyError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        save(session)
    return feature_payload(session)


@router.post("/approve")
def approve(request: Request) -> Dict[str, Any]:
    return _act(request, lambda s, actor: engine.approve(s, actor=actor))


@router.post("/{feature_id}/approve")
def approve_named(request: Request, feature_id: str) -> Dict[str, Any]:
    """Earlier clients (Android) post to /{feature_id}/approve."""
    session, _states = states_for(request)
    if session.session_manager.active_feature_id != _slugify_feature_id(feature_id):
        raise HTTPException(status_code=409, detail="Load the feature before approving it.")
    return _act(request, lambda s, actor: engine.approve(s, actor=actor))


@router.post("/tasks/{task_id}/start")
def start(request: Request, task_id: int) -> Dict[str, Any]:
    return _act(request, lambda s, actor: engine.start_task(s, task_id, actor=actor))


class TransitionBody(BaseModel):
    to_status: str
    reason: str = ""
    # Earlier clients send these.
    notes: Optional[str] = None
    blocked_reason: Optional[str] = None


@router.post("/tasks/{task_id}/transition")
def transition(request: Request, task_id: int, body: TransitionBody) -> Dict[str, Any]:
    reason = body.reason or body.blocked_reason or body.notes or ""
    return _act(request, lambda s, actor: engine.transition(s, task_id, body.to_status, actor=actor, reason=reason))


class AttestBody(BaseModel):
    note: str


@router.post("/tasks/{task_id}/criteria/{criterion_id}/attest")
def attest(request: Request, task_id: int, criterion_id: str, body: AttestBody) -> Dict[str, Any]:
    return _act(request, lambda s, actor: engine.attest_criterion(s, task_id, criterion_id, note=body.note, actor=actor))


class CompleteTaskBody(BaseModel):
    override_reason: str = ""


@router.post("/tasks/{task_id}/complete")
def complete_task(request: Request, task_id: int, body: Optional[CompleteTaskBody] = None) -> Dict[str, Any]:
    reason = body.override_reason if body else ""
    return _act(request, lambda s, actor: engine.complete_task(s, task_id, actor=actor, override_reason=reason))


class DecideBody(BaseModel):
    decision: str
    reason: str = ""


@router.post("/diffs/{proposal_id}/decide")
def decide(request: Request, proposal_id: str, body: DecideBody) -> Dict[str, Any]:
    return _act(request, lambda s, actor: engine.decide_diff(s, proposal_id, decision=body.decision,
                                                            reason=body.reason, actor=actor))


@router.post("/complete")
def complete_feature(request: Request) -> Dict[str, Any]:
    return _act(request, lambda s, actor: engine.complete_feature(s, actor=actor))


# ------------------------------------------------------------------ feature records


def _session_or_412(request: Request):
    session = mode_session(request)
    if session is None:
        raise HTTPException(status_code=412, detail="no session active")
    return session


def _record_or_404(sm, feature_id: str) -> Dict[str, Any]:
    record = (sm.feature_registry or {}).get(feature_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Feature '{feature_id}' not found.")
    return record


@router.delete("/{feature_id}")
def delete_feature(request: Request, feature_id: str) -> Dict[str, Any]:
    session = _session_or_412(request)
    sm = session.session_manager
    feature_id = _slugify_feature_id(feature_id)
    if sm.active_feature_id == feature_id:
        raise HTTPException(status_code=409, detail=f"Feature '{feature_id}' is currently loaded — unload it first.")
    _record_or_404(sm, feature_id)
    with mode_session_lock(request, session, exclusive=True):
        sm.delete_feature(feature_id)
    return {"ok": True, "features": _features_list(sm)}


@router.post("/{feature_id}/load")
def load_feature(request: Request, feature_id: str) -> Dict[str, Any]:
    session = _session_or_412(request)
    sm = session.session_manager
    feature_id = _slugify_feature_id(feature_id)
    record = _record_or_404(sm, feature_id)
    if record.get("archived"):
        raise HTTPException(status_code=409, detail="Unarchive the feature first.")
    with mode_session_lock(request, session, exclusive=True):
        sm.activate_feature(feature_id)
    return {"ok": True, "features": _features_list(sm)}


@router.post("/{feature_id}/unload")
def unload_feature(request: Request, feature_id: str) -> Dict[str, Any]:
    session = _session_or_412(request)
    sm = session.session_manager
    feature_id = _slugify_feature_id(feature_id)
    if sm.active_feature_id != feature_id:
        _record_or_404(sm, feature_id)
        raise HTTPException(status_code=409, detail=f"Feature '{feature_id}' is not the active feature.")
    with mode_session_lock(request, session, exclusive=True):
        sm.active_feature_id = None
        sm.feature_state = None
        sm.save_history()
    return {"ok": True, "features": _features_list(sm)}


@router.post("/{feature_id}/archive")
def archive_feature(request: Request, feature_id: str) -> Dict[str, Any]:
    session = _session_or_412(request)
    sm = session.session_manager
    feature_id = _slugify_feature_id(feature_id)
    if sm.active_feature_id == feature_id:
        raise HTTPException(status_code=409, detail="Unload the feature before archiving.")
    _record_or_404(sm, feature_id)
    with mode_session_lock(request, session, exclusive=True):
        sm.archive_feature(feature_id)
    return {"ok": True, "features": _features_list(sm)}


@router.post("/{feature_id}/unarchive")
def unarchive_feature(request: Request, feature_id: str) -> Dict[str, Any]:
    session = _session_or_412(request)
    sm = session.session_manager
    feature_id = _slugify_feature_id(feature_id)
    _record_or_404(sm, feature_id)
    with mode_session_lock(request, session, exclusive=True):
        sm.unarchive_feature(feature_id)
    return {"ok": True, "features": _features_list(sm)}


@router.get("/{feature_id}/preview")
def preview_feature(request: Request, feature_id: str) -> Dict[str, Any]:
    session = _session_or_412(request)
    sm = session.session_manager
    feature_id = _slugify_feature_id(feature_id)
    record = _record_or_404(sm, feature_id)
    plan = engine.read_feature_plan(str(record.get("metadata_path") or ""))
    if plan is None:
        raise HTTPException(status_code=404, detail="No plan data found for this feature.")
    return feature_payload(session, plan, read_only=True)


class CreateTaskBody(BaseModel):
    title: str
    objective: str = ""
    objectives: List[str] = []
    exit_criteria: List[Any] = []


class CreatePhaseBody(BaseModel):
    title: str
    goal: str = ""
    tasks: List[CreateTaskBody] = []


class CreateFeatureBody(BaseModel):
    feature_name: str
    feature_request: str = ""
    directory: str = ""
    phases: List[CreatePhaseBody] = []


@router.post("/create")
def create_feature(request: Request, body: CreateFeatureBody) -> Dict[str, Any]:
    """Create a feature as a draft; with phases, the draft is filled in (still needs approval)."""
    session = _session_or_412(request)
    sm = session.session_manager
    name = body.feature_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="feature_name is required")
    folders = getattr(sm.folder_context, "folders", None) or []
    directory = body.directory.strip() or (folders[0] if folders else os.getcwd())
    with mode_session_lock(request, session, exclusive=True):
        record = sm.create_feature_record(name, directory=directory, feature_request=body.feature_request.strip())
        if body.phases:
            phases = [{"title": p.title, "goal": p.goal, "tasks": [
                {"title": t.title, "objective": t.objective or (t.objectives[0] if t.objectives else ""),
                 "exit_criteria": t.exit_criteria} for t in p.tasks]} for p in body.phases]
            try:
                engine.propose_plan(session, name=name, goal=body.feature_request.strip() or name, phases=phases,
                                    actor=surface_actor(request))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
        sm.save_history()
    return {"ok": True, "feature_id": record["feature_id"], "features": _features_list(sm)}
