"""Loop-mode introspection.

Surfaces the autonomous loop's goal, active flag, spawned features,
and the current todo backlog (scratchpad-backed) so the GUI can show
a dashboard while the agent is running hands-off.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..mode_workspace import loop_workspace

router = APIRouter()
_logger = logging.getLogger(__name__)


def _entry_dict(entry) -> Dict[str, Any]:
    return {
        "id": entry.id,
        "content": entry.content,
        "tags": list(entry.tags or []),
        "source": entry.source,
        "kind": getattr(entry, "kind", ""),
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
    }


@router.get("/state")
async def get_loop_state(request: Request) -> Dict[str, Any]:
    session = request.app.state.session_by_name()
    if session is None:
        return {
            "active": False,
            "loop_goal": "",
            "loop_active": False,
            "loop_features": [],
            "backlog": [],
            "memory": [],
            "workspace": loop_workspace(
                "", [], [], [], loop_active=False, active=False
            ),
        }
    sm = session.session_manager
    variables = sm.variables
    mode_active = variables.get("agent_mode", "default") == "loop"

    loop_goal = str(variables.get("loop_goal", "") or "").strip()
    loop_active = bool(variables.get("loop_active", False))

    raw_features = variables.get("loop_features")
    if isinstance(raw_features, str):
        import json
        try:
            raw_features = json.loads(raw_features)
        except (ValueError, TypeError):
            raw_features = []
    loop_features = list(raw_features or [])

    # Backlog: todo items from the scratchpad (tagged "todo").
    backlog: List[Dict[str, Any]] = []
    try:
        entries = sm.turn_scratchpad.list_entries(limit=50)
        for e in entries:
            tags = [t.lower() for t in (e.tags or [])]
            if "todo" in tags:
                status = "pending"
                for t in tags:
                    if t.startswith("status:"):
                        status = t.split(":", 1)[1]
                backlog.append({**_entry_dict(e), "status": status})
    except Exception as exc:
        _logger.warning("loop: scratchpad read failed: %s", exc)

    # Memory snapshot: recent task_memory entries.
    memory: List[Dict[str, Any]] = []
    try:
        entries = sm.task_memory.list_entries(limit=5)
        memory = [_entry_dict(e) for e in entries]
    except Exception as exc:
        _logger.warning("loop: task_memory read failed: %s", exc)

    return {
        "active": True,
        "loop_goal": loop_goal,
        "loop_active": loop_active,
        "loop_features": loop_features,
        "backlog": backlog,
        "memory": memory,
        "workspace": loop_workspace(
            loop_goal,
            backlog,
            loop_features,
            memory,
            loop_active=loop_active,
            active=mode_active,
        ),
    }


class BacklogItemBody(BaseModel):
    content: str
    status: str = "pending"


class LoopControlBody(BaseModel):
    active: bool
    goal: str = ""


@router.post("/control")
async def control_loop(request: Request, body: LoopControlBody) -> Dict[str, Any]:
    """Pause or resume mission execution without rebuilding its backlog.

    Starting a brand-new loop remains a chat/model operation.  This control is
    intentionally narrower: it toggles an existing mission and preserves its
    workstreams, memory, and queue.
    """
    session = request.app.state.session_by_name()
    if session is None:
        raise HTTPException(status_code=412, detail="no session active")

    goal = body.goal.strip() or str(session.variables.get("loop_goal", "") or "").strip()
    if body.active and not goal:
        raise HTTPException(status_code=409, detail="set a loop goal before resuming")

    with request.app.state.session_lock_for():
        session.variables["loop_active"] = body.active
        if body.active:
            session.variables["loop_goal"] = goal
            session.variables["agent_mode"] = "loop"
            session._ensure_loop_goal_persistence()
        session.session_manager.save_history(session.folder_context)

    return {"ok": True, "loop_active": body.active, "loop_goal": goal}


@router.post("/backlog")
async def add_backlog_item(request: Request, body: BacklogItemBody) -> Dict[str, Any]:
    """Add a new todo item to the loop backlog (scratchpad)."""
    session = request.app.state.session_by_name()
    if session is None:
        raise HTTPException(status_code=412, detail="no session active")
    sm = session.session_manager
    content = body.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required")

    tags = ["todo", f"status:{body.status}"]
    lock = request.app.state.session_lock_for()
    with lock:
        entry = sm.turn_scratchpad.save(content, tags=tags, source="gui", kind="todo")
        sm.save_history()

    return {"ok": True, "id": entry.id}
