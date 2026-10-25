"""Debug-mode state: the session's debug cases (``mu.debug``).

``GET  /api/debug/state``         current case (hypotheses + evidence, suspects, runs),
                                  every case, past root causes for the files involved
``POST /api/debug/cases/{id}/select``
``POST /api/debug/close``         the user closes the verified current case

``active`` is true only in debug mode. Runs are full ``CheckRun`` payloads so
the shared row/sheet can render them.
"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..mode_session import mode_session, mode_session_lock
from .mode_state import check_payload, save, states_for, surface_actor

router = APIRouter()


def _empty(active: bool) -> Dict[str, Any]:
    return {"active": active, "case": None, "cases": [], "runs": [], "past_root_causes": [],
            "plan_mode": False, "blockers": []}


def debug_payload(session: Any) -> Dict[str, Any]:
    from mu.debug.engine import case_summary, past_root_causes

    manager = session.session_manager
    active = str(manager.variables.get("agent_mode", "default")) == "debug"
    states = manager.mode_state
    state = states.peek("debug")
    if state is None:
        return _empty(active)
    with state.lock:
        case = state.current_case()
        cases = [case_summary(c) for c in state.cases.values()]
        case_data = case.to_dict() if case is not None else None
    runs: List[Dict[str, Any]] = []
    if case is not None:
        checks = states.get("checks")
        runs = [check_payload(r) for r in checks.list(ref=case.id)]
    files: List[str] = []
    if case_data:
        files = list(case_data["fix"].get("files") or []) + [s["path"] for s in case_data["suspects"]]
    return {
        "active": active,
        "case": case_data,
        "cases": cases,
        "runs": runs,
        "past_root_causes": past_root_causes(session, files) if files else [],
        "blockers": [b.to_dict() for b in states.get("blockers").open_blockers()],
        "plan_mode": bool(manager.variables.get("plan_mode")),
    }


@router.get("/state")
async def get_debug_state(request: Request) -> Dict[str, Any]:
    session = mode_session(request)
    if session is None or getattr(session.session_manager, "mode_state", None) is None:
        return _empty(False)
    return debug_payload(session)


@router.post("/cases/{case_id}/select")
async def select(request: Request, case_id: str) -> Dict[str, Any]:
    from mu.debug.engine import select_case
    from mu.modes.events import GateError

    session, _states = states_for(request)
    with mode_session_lock(request, session):
        try:
            select_case(session, case_id, actor=surface_actor(request))
        except GateError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        save(session)
    return debug_payload(session)


class CloseBody(BaseModel):
    cause: str = ""
    signature: str = ""
    unreproducible_reason: str = ""


@router.post("/close")
async def close(request: Request, body: CloseBody) -> Dict[str, Any]:
    from mu.debug.engine import close_case
    from mu.modes.events import GateError

    session, _states = states_for(request)
    with mode_session_lock(request, session):
        try:
            close_case(session, cause=body.cause, signature=body.signature,
                       unreproducible_reason=body.unreproducible_reason, actor=surface_actor(request))
        except (GateError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        save(session)
    return debug_payload(session)
