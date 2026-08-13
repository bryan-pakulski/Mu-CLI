"""Agent mode endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from mu.tools.capabilities import normalize_session_type

from utils.config import AGENT_MODE_METADATA, AGENTIC_MODES, GUI_VIEW_PANELS

from ..deps import require_session

router = APIRouter()


def _has_workspace(session) -> bool:
    if session is None:
        return False
    fc = getattr(session.session_manager, "folder_context", None)
    return bool(fc and getattr(fc, "folders", None))


def _is_container_session(session) -> bool:
    if session is None:
        return False
    return getattr(session, "container_ref", None) is not None


def _session_type(session) -> str:
    if session is None:
        return "workspace"
    return normalize_session_type(session.variables.get("session_type"))


def _has_execution_workspace(session) -> bool:
    """Whether strategy modes have an execution filesystem to operate on."""
    return _has_workspace(session) or _session_type(session) == "container"


@router.get("")
async def list_modes(
    request: Request,
    session_name: Optional[str] = Query(default=None),
):
    session = request.app.state.session_by_name(session_name)
    current = session.variables.get("agent_mode", "default") if session else None
    has_ws = _has_workspace(session)
    has_execution_ws = _has_execution_workspace(session)
    session_type = _session_type(session)
    modes = []
    # Only `default` works without an execution workspace — every other real
    # agent mode operates on host workspace or container files. (history/memory/systemPrompts
    # are view-only panels, not agent modes — surfaced separately as `views`.)
    _NO_WORKSPACE_NEEDED = {"default"}

    for key in AGENTIC_MODES:
        meta = AGENT_MODE_METADATA.get(key, {})
        needs_workspace = key not in _NO_WORKSPACE_NEEDED
        modes.append(
            {
                "name": key,
                "display_name": meta.get("display_name", key.title()),
                "description": meta.get("description", ""),
                "is_current": key == current,
                "needs_workspace": needs_workspace,
                "disabled": needs_workspace and not has_execution_ws,
            }
        )
    # GUI-only view panels — read-only, never settable as agent_mode. Most
    # render off session state and never need a workspace; the Files panel
    # is the exception (it edits workspace files), so it honors
    # `needs_workspace` and is disabled when no folder is attached.
    has_container = _is_container_session(session)
    views = [
        {
            "name": panel["name"],
            "display_name": panel["display_name"],
            "description": panel["description"],
            "view_only": True,
            "needs_workspace": bool(panel.get("needs_workspace")),
            "needs_container": bool(panel.get("needs_container")),
            "disabled": (
                bool(panel.get("needs_workspace")) and not has_ws
            ) or (
                bool(panel.get("needs_container")) and not has_container
            ),
            # External full-page routes (e.g. the Trace Analyzer) open in a
            # new tab rather than rendering as an in-page panel.
            "external": bool(panel.get("external")),
            "route": panel.get("route", ""),
            # Session-scoped external routes get ?session=<current> appended
            # by the tools dropdown so they open on the active session.
            "route_session": bool(panel.get("route_session")),
        }
        for panel in GUI_VIEW_PANELS
    ]
    return {
        "current": current,
        "modes": modes,
        "views": views,
        "has_workspace": has_ws,
        "has_execution_workspace": has_execution_ws,
        "has_container": has_container,
        "session_type": session_type,
        "execution_boundary": "container" if session_type == "container" else "host",
    }


@router.post("/{name}")
async def set_mode(name: str, request: Request, session=Depends(require_session)):
    if name not in AGENTIC_MODES:
        raise HTTPException(status_code=400, detail=f"Unknown mode: {name}")
    if name != "default" and not _has_execution_workspace(session):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Mode '{name}' requires an attached workspace or a container "
                "session. Add a folder via the inspector or create a container session."
            ),
        )
    session_name = session.session_manager.current_session_name
    with request.app.state.session_lock_for(session_name):
        session.variables["agent_mode"] = name
        try:
            session.session_manager.save_history(session.folder_context)
        except Exception:
            pass
    bus = getattr(request.app.state, "bus", None)
    if bus is not None:
        await bus.publish(
            {
                "kind": "mode_changed",
                "mode": name,
                "session_type": _session_type(session),
                "session_name": session_name,
            }
        )
    return {
        "ok": True,
        "current": name,
        "session_type": _session_type(session),
        "has_execution_workspace": _has_execution_workspace(session),
    }
