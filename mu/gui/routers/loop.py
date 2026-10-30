"""Loop-mode state and controls: the mission (``mu.loop``).

``GET  /api/loop/state``           mission (goal, status, budget + tightest meter, done-when,
                                   increments), backlog (work items), open blockers, check runs
``POST /api/loop/start``           the user starts a mission (goal, done_when, budget); work begins
``POST /api/loop/control``         pause | resume | stop (real: the watchdog obeys); resume restarts work
``POST /api/loop/goal``            the user changes the goal
``POST /api/loop/budget``          the user raises / sets budgets
``POST /api/loop/criteria/{id}/attest``  the user marks a done-when criterion met (recorded)
``POST /api/loop/backlog``         the user adds a backlog item
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..mode_session import mode_session, mode_session_lock
from .mode_state import check_payload, save, states_for, surface_actor

router = APIRouter()


def loop_payload(session: Any) -> Dict[str, Any]:
    from mu.loop.engine import tightest_budget

    manager = session.session_manager
    active = str(manager.variables.get("agent_mode", "default")) == "loop"
    states = manager.mode_state
    state = states.get("loop")
    work = states.get("work")
    with state.lock:
        mission = state.mission.to_dict() if state.mission else None
        meter = tightest_budget(state.mission) if state.mission else None
    runs: List[Dict[str, Any]] = []
    if mission:
        checks = states.get("checks")
        ids = {r for inc in mission["increments"] for r in inc["evidence"]}
        ids |= {r for c in mission["done_when"] for r in c["evidence"]}
        runs = [check_payload(checks.get(r)) for r in sorted(ids, key=lambda x: int(x[1:]) if x[1:].isdigit() else 0)
                if checks.get(r) is not None]
    current = work.current()
    return {
        "active": active,
        "mission": mission,
        "budget_meter": meter,
        "backlog": [i.to_dict() for i in work.list()],
        "current_item": current.id if current else None,
        "blockers": [b.to_dict() for b in states.get("blockers").open_blockers()],
        "runs": runs,
        "past": [{"id": p.get("id"), "goal": p.get("goal"), "status": p.get("status")}
                 for p in state.past[-5:]],
    }


@router.get("/state")
def get_loop_state(request: Request) -> Dict[str, Any]:
    session = mode_session(request)
    if session is None or getattr(session.session_manager, "mode_state", None) is None:
        return {"active": False, "mission": None, "budget_meter": None, "backlog": [],
                "current_item": None, "blockers": [], "runs": [], "past": []}
    return loop_payload(session)


def _act(request: Request, fn) -> Dict[str, Any]:
    from mu.modes.events import GateError

    session, _states = states_for(request)
    with mode_session_lock(request, session):
        try:
            fn(session, surface_actor(request))
        except GateError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        save(session)
    return loop_payload(session)


RESUME_TEXT = "Resume the mission."


async def begin_work(request: Request, text: str) -> None:
    """Start a turn on the mission unless one is already in flight.

    Starting or resuming from the panel is the user asking the loop to work;
    the turn runs exactly as if they had sent ``text`` in chat.
    """
    from .chat import launch_turn

    session = mode_session(request)
    name = session.session_manager.current_session_name
    if request.app.state.session_busy_for(name).is_set():
        return
    await launch_turn(request.app, session, text)


class StartBody(BaseModel):
    goal: str
    done_when: List[Any] = []
    budget: Dict[str, float] = {}


@router.post("/start")
async def start(request: Request, body: StartBody) -> Dict[str, Any]:
    from mu.loop.engine import start_mission

    def run(session, actor):
        start_mission(session, body.goal, done_when=body.done_when, budget=body.budget, actor=actor)
        session.variables["agent_mode"] = "loop"

    payload = await asyncio.to_thread(_act, request, run)
    await begin_work(request, body.goal.strip())
    return payload


class ControlBody(BaseModel):
    action: str = ""
    reason: str = ""
    # Earlier clients (Android) send {active: bool}.
    active: Optional[bool] = None


@router.post("/control")
async def control(request: Request, body: ControlBody) -> Dict[str, Any]:
    from mu.loop.engine import set_status

    action = body.action or ("resume" if body.active else "pause" if body.active is not None else "")
    status = {"pause": "paused", "resume": "running", "stop": "stopped"}.get(action)
    if status is None:
        raise HTTPException(status_code=400, detail="action must be pause, resume or stop")
    payload = await asyncio.to_thread(_act, request, lambda session, actor: set_status(session, status, reason=body.reason, actor=actor))
    if status == "running":
        await begin_work(request, RESUME_TEXT)
    return payload


class GoalBody(BaseModel):
    goal: str


@router.post("/goal")
def goal(request: Request, body: GoalBody) -> Dict[str, Any]:
    from mu.loop.engine import set_goal

    return _act(request, lambda session, actor: set_goal(session, body.goal, actor=actor))


@router.post("/budget")
def budget(request: Request, body: Dict[str, float]) -> Dict[str, Any]:
    from mu.loop.engine import set_budget

    return _act(request, lambda session, actor: set_budget(session, body, actor=actor))


class AttestBody(BaseModel):
    note: str


@router.post("/criteria/{criterion_id}/attest")
def attest(request: Request, criterion_id: str, body: AttestBody) -> Dict[str, Any]:
    from mu.loop.engine import attest as attest_criterion

    return _act(request, lambda session, actor: attest_criterion(session, criterion_id, note=body.note, actor=actor))


class BacklogItemBody(BaseModel):
    content: str
    status: str = "pending"


@router.post("/backlog")
def add_backlog_item(request: Request, body: BacklogItemBody) -> Dict[str, Any]:
    from mu.modes.work import work_state

    if not body.content.strip():
        raise HTTPException(status_code=400, detail="content is required")
    return _act(request, lambda session, actor: work_state(session).add(
        body.content, status=body.status, actor=actor, mode="loop"))
