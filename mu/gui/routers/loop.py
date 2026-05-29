"""Loop-mode introspection.

Surfaces the autonomous loop's goal, active flag, spawned features,
and the current todo backlog (scratchpad-backed) so the GUI can show
a dashboard while the agent is running hands-off.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List

from fastapi import APIRouter, Request

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
        }
    sm = session.session_manager
    variables = sm.variables

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
    }
