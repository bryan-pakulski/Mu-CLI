"""Feature-mode introspection.

Exposes the active FeaturePlan to the GUI so the side-panel can render
a kanban of tasks grouped by phase, the current task's exit-criteria
checklist, and the recent event log. State lives on disk under each
feature's metadata_path (workspace `feature_plan.json`
or session-history `<HISTORY_DIR>/sessions/<id>/features/<id>.json`).
The SessionManager keeps a registry of stubs; the rich plan structure
is read from the registry record's embedded `feature_plan` field, or
hydrated from the metadata file when the in-memory copy looks empty.

Mutating endpoints (drag-and-drop transitions, exit-criterion toggles)
take the per-session lock so they serialize with concurrent chat turns.
Invalid transitions raise 409 — the engine's state machine is the
source of truth, the panel just surfaces it.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from mu.feature.engine import (
    FeaturePlan,
    FeaturePhase,
    FeatureTask,
    load_feature_plan,
    save_feature_plan,
    summarize_feature_plan,
    transition_task_status,
)
from mu.session.helpers import _slugify_feature_id, derive_feature_state_status

router = APIRouter()
_logger = logging.getLogger(__name__)


def _hydrate_plan(record: Optional[Dict[str, Any]]) -> Optional[FeaturePlan]:
    """Load a FeaturePlan from the registry record's metadata file when
    available; fall back to the embedded `feature_plan` dict otherwise.

    Disk is the source of truth because the engine's mutation paths
    (transition_task_status, update_task_status) save through
    save_feature_plan() — the in-memory `feature_state` only catches up
    on `set_feature_state` calls, which may lag behind direct edits.
    """
    if not isinstance(record, dict):
        return None
    metadata_path = str(record.get("metadata_path") or "").strip()
    if metadata_path and os.path.exists(metadata_path):
        try:
            return load_feature_plan(metadata_path)
        except (OSError, ValueError, KeyError) as exc:
            _logger.warning("feature: could not load %s: %s", metadata_path, exc)

    embedded = record.get("feature_plan")
    if isinstance(embedded, dict):
        # The embedded copy is keyed by FeaturePlan field names but
        # carries derived fields (overall_status, phases, next_phase)
        # that the constructor rejects. load_feature_plan() does the
        # field stripping when reading from disk; here we write to a
        # temp file and reuse that path. Cheaper than duplicating the
        # parse logic.
        scratch = embedded  # noqa: F841 — kept for clarity in error path
        try:
            # Mimic load_feature_plan's stripping inline so we don't
            # need a temp file roundtrip.
            data = {**embedded}
            data.pop("overall_status", None)
            data.pop("phases_completed", None)
            data.pop("phase_count", None)
            data.pop("phases", None)
            data.pop("next_phase", None)
            data.pop("next_task", None)
            data.pop("tasks_completed", None)
            data.pop("event_count", None)
            data.pop("review_count", None)
            data.pop("diff_proposal_count", None)
            data.pop("active_tasks", None)
            data.pop("review_summaries", None)
            data.pop("workflow", None)
            data.pop("execution", None)
            from dataclasses import fields
            from mu.feature.engine import (
                FeatureTask,
                FeaturePhase,
                FeatureEvent,
                TaskReviewRecord,
                DiffProposal,
            )
            tasks = [FeatureTask(**t) for t in (data.pop("tasks", []) or [])]
            phases_meta = [
                FeaturePhase(**p) for p in (data.pop("phases_meta", []) or [])
            ]
            event_log = [
                FeatureEvent(**e) for e in (data.pop("event_log", []) or [])
            ]
            review_records = [
                TaskReviewRecord(**r) for r in (data.pop("review_records", []) or [])
            ]
            diff_proposals = [
                DiffProposal(**d) for d in (data.pop("diff_proposals", []) or [])
            ]
            allowed = {f.name for f in fields(FeaturePlan)}
            data = {k: v for k, v in data.items() if k in allowed}
            return FeaturePlan(
                tasks=tasks,
                phases_meta=phases_meta,
                event_log=event_log,
                review_records=review_records,
                diff_proposals=diff_proposals,
                **data,
            )
        except (TypeError, ValueError, KeyError) as exc:
            _logger.warning(
                "feature: could not rebuild plan from embedded dict: %s", exc
            )
    return None


def _kanban_phases(summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Group tasks under their phase for the kanban view.

    `summarize_feature_plan` returns a flat task list plus phase metadata
    on `phases_meta`. Pre-joining here keeps the template trivial.
    """
    phases_meta = summary.get("phases_meta") or []
    # Build {phase_id: [task,...]} preserving the task order from
    # the flat list (which is already the engine's natural order).
    tasks_by_phase: Dict[Any, List[Dict[str, Any]]] = {}
    for task in summary.get("phases") or []:  # `phases` is misleading; it's the task list
        if not isinstance(task, dict):
            continue
        tasks_by_phase.setdefault(task.get("phase_id"), []).append(task)

    out: List[Dict[str, Any]] = []
    placed_ids: set[int] = set()
    for phase in phases_meta:
        if not isinstance(phase, dict):
            continue
        pid = phase.get("id")
        phase_tasks = tasks_by_phase.get(pid, [])
        for t in phase_tasks:
            tid = t.get("id")
            if isinstance(tid, int):
                placed_ids.add(tid)
        out.append(
            {
                "id": pid,
                "title": phase.get("title") or f"Phase {pid}",
                "goal": phase.get("goal") or "",
                "order": phase.get("order") or 0,
                "status": phase.get("status"),
                "tasks": phase_tasks,
            }
        )

    # Tasks not claimed by any phase — show in a synthetic bucket
    # rather than dropping them.
    loose = [
        t
        for t in (summary.get("phases") or [])
        if isinstance(t, dict) and t.get("id") not in placed_ids
    ]
    if loose:
        out.append(
            {
                "id": "_loose",
                "title": "(unassigned)",
                "goal": "",
                "order": 999,
                "status": None,
                "tasks": loose,
            }
        )

    out.sort(key=lambda p: (p.get("order") or 0, str(p.get("id") or "")))
    return out


def _features_list(sm) -> List[Dict[str, Any]]:
    """Compact list of all features in the registry for the switcher chips."""
    out: List[Dict[str, Any]] = []
    for fid, record in (sm.feature_registry or {}).items():
        if not isinstance(record, dict):
            continue
        out.append(
            {
                "feature_id": fid,
                "feature_name": record.get("feature_name") or fid,
                "status": record.get("status"),
                "is_active": fid == sm.active_feature_id,
                "archived": bool(record.get("archived")),
                "updated_at": record.get("updated_at"),
            }
        )
    out.sort(key=lambda f: (f["archived"], not f["is_active"], str(f["feature_id"] or "")))
    return out


@router.get("/state")
async def get_feature_state(request: Request) -> Dict[str, Any]:
    session = request.app.state.session_by_name()
    if session is None:
        return {
            "active": False,
            "active_feature_id": None,
            "plan": None,
            "features": [],
            "metadata_path": None,
        }
    sm = session.session_manager

    record = sm.feature_state
    if record is None and sm.active_feature_id:
        record = (sm.feature_registry or {}).get(sm.active_feature_id)

    plan = _hydrate_plan(record)
    if plan is None:
        return {
            "active": True,
            "active_feature_id": sm.active_feature_id,
            "plan": None,
            "features": _features_list(sm),
            "metadata_path": None,
        }

    summary = summarize_feature_plan(plan)
    summary["phase_columns"] = _kanban_phases(summary)
    # Keep payload trim — the panel doesn't render every field returned
    # by summarize_feature_plan, but it's small enough that pruning is
    # premature optimization. Leave the full summary for now.

    return {
        "active": True,
        "active_feature_id": sm.active_feature_id,
        "plan": summary,
        "features": _features_list(sm),
        "metadata_path": plan.metadata_path or None,
    }


class TransitionBody(BaseModel):
    to_status: str
    notes: Optional[str] = None
    blocked_reason: Optional[str] = None


@router.post("/tasks/{task_id}/transition")
async def transition_task(
    request: Request, task_id: int, body: TransitionBody
) -> Dict[str, Any]:
    """Move a task to a new status. The engine's state machine validates
    the transition; we surface invalid moves as 409 so the UI can snap
    the dragged card back to its origin.
    """
    session = request.app.state.session_by_name()
    if session is None:
        raise HTTPException(status_code=412, detail="no session active")
    sm = session.session_manager

    record = sm.feature_state or (sm.feature_registry or {}).get(sm.active_feature_id)
    plan = _hydrate_plan(record)
    if plan is None:
        raise HTTPException(status_code=404, detail="no active feature plan")

    lock = request.app.state.session_lock_for()
    with lock:
        try:
            transition_task_status(
                plan,
                task_id=task_id,
                to_status=body.to_status,
                notes=body.notes,
                blocked_reason=body.blocked_reason,
                actor="gui",
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        save_feature_plan("", plan)
        # Keep the session's in-memory copy in sync with disk so other
        # views (TUI, future panels) see the new state without reload.
        summary = summarize_feature_plan(plan)
        if isinstance(sm.feature_state, dict):
            sm.feature_state["feature_plan"] = summary
        if sm.active_feature_id and sm.active_feature_id in (sm.feature_registry or {}):
            sm.feature_registry[sm.active_feature_id]["feature_plan"] = summary

    return {"ok": True, "task_id": task_id, "to_status": body.to_status}


@router.post("/tasks/{task_id}/exit-criteria/{idx}/toggle")
async def toggle_exit_criterion(
    request: Request, task_id: int, idx: int
) -> Dict[str, Any]:
    """Flip the verified state of one exit criterion on a task. The
    engine stores `verified_exit_criteria` as a list of criterion
    strings; toggling adds/removes the indexed criterion from that list.
    """
    session = request.app.state.session_by_name()
    if session is None:
        raise HTTPException(status_code=412, detail="no session active")
    sm = session.session_manager

    record = sm.feature_state or (sm.feature_registry or {}).get(sm.active_feature_id)
    plan = _hydrate_plan(record)
    if plan is None:
        raise HTTPException(status_code=404, detail="no active feature plan")

    lock = request.app.state.session_lock_for()
    with lock:
        task = next((t for t in plan.tasks if t.id == task_id), None)
        if task is None:
            raise HTTPException(status_code=404, detail=f"task {task_id} not found")
        if idx < 0 or idx >= len(task.exit_criteria):
            raise HTTPException(
                status_code=400,
                detail=f"exit criterion index {idx} out of range",
            )
        criterion = task.exit_criteria[idx]
        verified = list(task.verified_exit_criteria or [])
        if criterion in verified:
            verified = [v for v in verified if v != criterion]
        else:
            verified.append(criterion)
        task.verified_exit_criteria = verified
        save_feature_plan("", plan)

        summary = summarize_feature_plan(plan)
        if isinstance(sm.feature_state, dict):
            sm.feature_state["feature_plan"] = summary
        if sm.active_feature_id and sm.active_feature_id in (sm.feature_registry or {}):
            sm.feature_registry[sm.active_feature_id]["feature_plan"] = summary

    return {
        "ok": True,
        "task_id": task_id,
        "criterion_index": idx,
        "verified": criterion in (task.verified_exit_criteria or []),
    }


@router.post("/{feature_id}/approve")
async def approve_feature(request: Request, feature_id: str) -> Dict[str, Any]:
    """Approve the feature plan so implementation can begin."""
    session = request.app.state.session_by_name()
    if session is None:
        raise HTTPException(status_code=412, detail="no session active")
    sm = session.session_manager
    feature_id = _slugify_feature_id(feature_id)

    record = (sm.feature_registry or {}).get(feature_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Feature '{feature_id}' not found.")

    plan = _hydrate_plan(record)
    if plan is None:
        raise HTTPException(status_code=404, detail="No plan data found for this feature.")
    if plan.approved:
        return {"ok": True, "features": _features_list(sm)}

    lock = request.app.state.session_lock_for()
    with lock:
        plan.approved = True
        save_feature_plan("", plan)
        summary = summarize_feature_plan(plan)
        record["feature_plan"] = summary
        record["status"] = derive_feature_state_status(summary)
        record["updated_at"] = time.time()
        sm.save_history()
        if sm.active_feature_id == feature_id and isinstance(sm.feature_state, dict):
            sm.feature_state["feature_plan"] = summary
            sm.feature_state["status"] = record["status"]

    return {"ok": True, "features": _features_list(sm)}


@router.delete("/{feature_id}")
async def delete_feature(request: Request, feature_id: str) -> Dict[str, Any]:
    """Delete a feature from the registry. Refuses if the feature is
    currently active (loaded) — the user must unload it first."""
    session = request.app.state.session_by_name()
    if session is None:
        raise HTTPException(status_code=412, detail="no session active")
    sm = session.session_manager
    feature_id = _slugify_feature_id(feature_id)

    if sm.active_feature_id == feature_id:
        raise HTTPException(
            status_code=409,
            detail=f"Feature '{feature_id}' is currently loaded — unload it first.",
        )

    record = (sm.feature_registry or {}).get(feature_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Feature '{feature_id}' not found.")

    lock = request.app.state.session_lock_for()
    with lock:
        sm.delete_feature(feature_id)

    return {"ok": True, "features": _features_list(sm)}


@router.post("/{feature_id}/load")
async def load_feature(request: Request, feature_id: str) -> Dict[str, Any]:
    """Load (activate) a feature plan so the kanban view shows it."""
    session = request.app.state.session_by_name()
    if session is None:
        raise HTTPException(status_code=412, detail="no session active")
    sm = session.session_manager
    feature_id = _slugify_feature_id(feature_id)

    record = (sm.feature_registry or {}).get(feature_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Feature '{feature_id}' not found.")
    if record.get("archived"):
        raise HTTPException(status_code=409, detail="Unarchive the feature first.")

    lock = request.app.state.session_lock_for()
    with lock:
        sm.activate_feature(feature_id)

    return {"ok": True, "features": _features_list(sm)}


@router.post("/{feature_id}/unload")
async def unload_feature(request: Request, feature_id: str) -> Dict[str, Any]:
    """Unload the active feature plan. Clears active_feature_id and
    feature_state so the feature list view is shown again."""
    session = request.app.state.session_by_name()
    if session is None:
        raise HTTPException(status_code=412, detail="no session active")
    sm = session.session_manager
    feature_id = _slugify_feature_id(feature_id)

    if sm.active_feature_id != feature_id:
        if (sm.feature_registry or {}).get(feature_id) is None:
            raise HTTPException(status_code=404, detail=f"Feature '{feature_id}' not found.")
        raise HTTPException(
            status_code=409,
            detail=f"Feature '{feature_id}' is not the active feature.",
        )

    lock = request.app.state.session_lock_for()
    with lock:
        sm.active_feature_id = None
        sm.feature_state = None
        sm.save_history()

    return {"ok": True, "features": _features_list(sm)}


@router.post("/{feature_id}/archive")
async def archive_feature(request: Request, feature_id: str) -> Dict[str, Any]:
    """Archive a feature — hides it from the active list."""
    session = request.app.state.session_by_name()
    if session is None:
        raise HTTPException(status_code=412, detail="no session active")
    sm = session.session_manager
    feature_id = _slugify_feature_id(feature_id)

    if sm.active_feature_id == feature_id:
        raise HTTPException(
            status_code=409, detail="Unload the feature before archiving."
        )
    record = (sm.feature_registry or {}).get(feature_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Feature '{feature_id}' not found.")

    lock = request.app.state.session_lock_for()
    with lock:
        sm.archive_feature(feature_id)

    return {"ok": True, "features": _features_list(sm)}


@router.post("/{feature_id}/unarchive")
async def unarchive_feature(request: Request, feature_id: str) -> Dict[str, Any]:
    """Restore a feature from the archive."""
    session = request.app.state.session_by_name()
    if session is None:
        raise HTTPException(status_code=412, detail="no session active")
    sm = session.session_manager
    feature_id = _slugify_feature_id(feature_id)

    record = (sm.feature_registry or {}).get(feature_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Feature '{feature_id}' not found.")

    lock = request.app.state.session_lock_for()
    with lock:
        sm.unarchive_feature(feature_id)

    return {"ok": True, "features": _features_list(sm)}


class CreateTaskBody(BaseModel):
    title: str
    objectives: List[str] = []
    exit_criteria: List[str] = []


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
async def create_feature(request: Request, body: CreateFeatureBody) -> Dict[str, Any]:
    """Create a new feature with optional phases and tasks."""
    session = request.app.state.session_by_name()
    if session is None:
        raise HTTPException(status_code=412, detail="no session active")
    sm = session.session_manager

    name = body.feature_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="feature_name is required")

    directory = body.directory.strip() or os.getcwd()

    lock = request.app.state.session_lock_for()
    with lock:
        record = sm.create_feature_record(
            name,
            directory=directory,
            feature_request=body.feature_request.strip(),
        )
        feature_id = record["feature_id"]
        metadata_path = record["metadata_path"]

        if body.phases:
            phases_meta = []
            tasks = []
            task_id = 1
            for pi, phase_body in enumerate(body.phases):
                phase_id = pi + 1
                task_ids = []
                for task_body in phase_body.tasks:
                    tasks.append(FeatureTask(
                        id=task_id,
                        title=task_body.title.strip(),
                        phase_id=phase_id,
                        objectives=[o.strip() for o in task_body.objectives if o.strip()],
                        exit_criteria=[c.strip() for c in task_body.exit_criteria if c.strip()],
                    ))
                    task_ids.append(task_id)
                    task_id += 1
                phases_meta.append(FeaturePhase(
                    id=phase_id,
                    title=phase_body.title.strip(),
                    goal=(phase_body.goal or "").strip(),
                    order=pi,
                    task_ids=task_ids,
                ))

            plan = FeaturePlan(
                feature_id=feature_id,
                feature_name=name,
                feature_request=body.feature_request.strip() or name,
                directory=directory,
                metadata_path=metadata_path,
                tasks=tasks,
                phases_meta=phases_meta,
            )
            save_feature_plan("", plan)
            summary = summarize_feature_plan(plan)
            reg_record = sm.feature_registry.get(feature_id)
            if isinstance(reg_record, dict):
                reg_record["feature_plan"] = summary
                reg_record["status"] = derive_feature_state_status(summary)
                reg_record["updated_at"] = time.time()
            if sm.active_feature_id == feature_id and isinstance(sm.feature_state, dict):
                sm.feature_state["feature_plan"] = summary
                sm.feature_state["status"] = (
                    reg_record.get("status") if isinstance(reg_record, dict) else None
                )
            sm.save_history()

    return {"ok": True, "feature_id": feature_id, "features": _features_list(sm)}
