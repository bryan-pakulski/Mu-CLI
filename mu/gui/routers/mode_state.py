"""Shared mode-state API (modes v2 §3, §5.2): check runs, blockers, work items.

``GET  /api/mode-state/checks/{id}``          one CheckRun (the shared row + sheet)
``GET  /api/mode-state/checks/{id}/output``   its full output (the sheet's "Full output")
``GET  /api/mode-state/blockers``             open and recent blockers
``POST /api/mode-state/blockers/{id}/resolve`` the user resolves a blocker
``GET  /api/mode-state/work``                 the session's work items (the Plan disclosure)

Every route reads the requested session's own states (``?session_name=``);
mutations take the session lock and record the user surface as the actor.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..mode_session import mode_session, mode_session_lock

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
async def get_check(request: Request, run_id: str) -> Dict[str, Any]:
    _session, states = states_for(request)
    run = states.get("checks").get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"no check run {run_id}")
    return check_payload(run)


@router.get("/checks/{run_id}/output")
async def get_check_output(request: Request, run_id: str) -> Dict[str, Any]:
    from mu.modes.evidence import read_output

    _session, states = states_for(request)
    run = states.get("checks").get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"no check run {run_id}")
    return {"id": run.id, "output": read_output(run)}


@router.get("/blockers")
async def list_blockers(request: Request) -> Dict[str, Any]:
    _session, states = states_for(request)
    state = states.get("blockers")
    blockers = [b.to_dict() for b in state.blockers.values()]
    return {"open": [b for b in blockers if b["open"]],
            "resolved": [b for b in blockers if not b["open"]][-20:]}


class ResolveBody(BaseModel):
    resolution: str = ""


@router.post("/blockers/{blocker_id}/resolve")
async def resolve_blocker(request: Request, blocker_id: str, body: Optional[ResolveBody] = None) -> Dict[str, Any]:
    session, states = states_for(request)
    with mode_session_lock(request, session):
        try:
            blocker = states.get("blockers").resolve(blocker_id, (body.resolution if body else ""),
                                                     actor=surface_actor(request))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        save(session)
    return {"ok": True, "blocker": blocker.to_dict()}


@router.get("/work")
async def list_work(request: Request) -> Dict[str, Any]:
    _session, states = states_for(request)
    work = states.get("work")
    items = [i.to_dict() for i in work.list()]
    current = work.current()
    return {"items": items, "current": current.id if current else None, "counts": work.counts()}


__all__ = ["check_payload", "router", "save", "states_for", "surface_actor"]


@router.get("/receipts")
async def list_receipts(request: Request) -> Dict[str, Any]:
    """Default mode's turn receipts (newest last), so a reload can show them again."""
    _session, states = states_for(request)
    return {"receipts": list(states.get("receipts").receipts)}
