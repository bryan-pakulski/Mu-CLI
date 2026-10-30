"""Shared mode-state API (modes v2 §3, §5.2): check runs, blockers, work items.

``GET  /api/mode-state/checks/{id}``          one CheckRun (the shared row + sheet)
``GET  /api/mode-state/checks/{id}/output``   its full output (the sheet's "Full output")
``GET  /api/mode-state/blockers``             open and recent blockers
``POST /api/mode-state/blockers/{id}/resolve`` the user resolves a blocker
``GET  /api/mode-state/work``                 the session's work items (the Plan disclosure)
``POST /api/mode-state/work/{id}``            the user starts, unblocks, finishes or drops one

Every route reads the requested session's own states (``?session_name=``);
mutations take the session lock and record the user surface as the actor.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..mode_session import mode_session, mode_session_lock, turn_saves

router = APIRouter()


def surface_actor(request: Request) -> str:
    """``user:mobile`` for the Android app (bearer token), else ``user:gui``."""
    auth = str(request.headers.get("authorization") or "")
    client = str(request.headers.get("x-mucli-client") or "").lower()
    return "user:mobile" if client == "mobile" or auth.lower().startswith("bearer ") else "user:gui"


def states_for(request: Request):
    session = mode_session(request)
    manager = getattr(session, "session_manager", None)
    states = getattr(manager, "mode_state", None)
    if session is None or states is None:
        raise HTTPException(status_code=412, detail="no session active")
    return session, states


def save(session: Any) -> None:
    if turn_saves():
        return
    manager = getattr(session, "session_manager", None)
    if manager is not None and getattr(manager, "current_session_name", ""):
        manager.save_history(getattr(session, "folder_context", None))


def check_payload(run: Any) -> Dict[str, Any]:
    from mu.modes.evidence import describe_predicate

    data = run.to_dict()
    data["predicate_text"] = describe_predicate(run.predicate)
    data.pop("log_path", None)
    return data


@router.get("/checks/{run_id}")
def get_check(request: Request, run_id: str) -> Dict[str, Any]:
    _session, states = states_for(request)
    run = states.get("checks").get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"no check run {run_id}")
    return check_payload(run)


@router.get("/checks/{run_id}/output")
def get_check_output(request: Request, run_id: str) -> Dict[str, Any]:
    from mu.modes.evidence import read_output

    _session, states = states_for(request)
    run = states.get("checks").get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"no check run {run_id}")
    return {"id": run.id, "output": read_output(run)}


@router.get("/blockers")
def list_blockers(request: Request) -> Dict[str, Any]:
    _session, states = states_for(request)
    state = states.get("blockers")
    blockers = [b.to_dict() for b in state.blockers.values()]
    return {"open": [b for b in blockers if b["open"]],
            "resolved": [b for b in blockers if not b["open"]][-20:]}


class ResolveBody(BaseModel):
    resolution: str = ""


def _resolve(request: Request, blocker_id: str, resolution: str):
    """Resolve under the session lock; True when it put a blocked mission back to running."""
    session, states = states_for(request)
    loop = states.peek("loop")
    was_blocked = bool(loop is not None and loop.mission is not None and loop.mission.status == "blocked")
    with mode_session_lock(request, session):
        try:
            blocker = states.get("blockers").resolve(blocker_id, resolution, actor=surface_actor(request))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        save(session)
    resumed = was_blocked and loop.mission is not None and loop.mission.status == "running"
    return blocker, resumed


@router.post("/blockers/{blocker_id}/resolve")
async def resolve_blocker(request: Request, blocker_id: str, body: Optional[ResolveBody] = None) -> Dict[str, Any]:
    """The answer resolves the blocker; a loop mission it was holding resumes with a turn
    that carries the answer, exactly as if the user had replied in chat."""
    import asyncio

    from .loop import RESUME_TEXT, begin_work

    resolution = (body.resolution if body else "").strip()
    blocker, resumed = await asyncio.to_thread(_resolve, request, blocker_id, resolution)
    if resumed:
        await begin_work(request, resolution or RESUME_TEXT)
    return {"ok": True, "blocker": blocker.to_dict(), "resumed": resumed}


def _work_payload(states: Any) -> Dict[str, Any]:
    work = states.get("work")
    items = [i.to_dict() for i in work.list()]
    current = work.current()
    return {"items": items, "current": current.id if current else None, "counts": work.counts()}


@router.get("/work")
def list_work(request: Request) -> Dict[str, Any]:
    _session, states = states_for(request)
    return _work_payload(states)


class WorkStatusBody(BaseModel):
    status: str
    evidence: List[str] = []
    override_reason: str = ""
    note: str = ""


@router.post("/work/{item_id}")
def set_work_status(request: Request, item_id: str, body: WorkStatusBody) -> Dict[str, Any]:
    """The user moves a work item: start, unblock, finish or drop it. In loop mode
    ``done`` needs a passing check run or an override reason (recorded)."""
    from mu.modes.events import GateError

    session, states = states_for(request)
    loop_mode = str(session.variables.get("agent_mode", "default")) == "loop"
    with mode_session_lock(request, session):
        try:
            states.get("work").set_status(
                item_id, body.status, evidence=body.evidence, note=body.note.strip(),
                actor=surface_actor(request), require_evidence=loop_mode,
                override_reason=body.override_reason)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc).strip("'\""))
        except GateError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        save(session)
    return _work_payload(states)


__all__ = ["check_payload", "router", "save", "states_for", "surface_actor"]


@router.get("/receipts")
def list_receipts(request: Request) -> Dict[str, Any]:
    """Default mode's turn receipts (newest last), so a reload can show them again."""
    _session, states = states_for(request)
    return {"receipts": list(states.get("receipts").receipts)}
