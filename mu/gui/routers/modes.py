"""Agent mode endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from utils.config import AGENT_MODE_METADATA, AGENTIC_MODES, GUI_VIEW_PANELS

from ..deps import require_session

router = APIRouter()


def _has_workspace(session) -> bool:
    if session is None:
        return False
    fc = getattr(session.session_manager, "folder_context", None)
    return bool(fc and getattr(fc, "folders", None))


@router.get("")
async def list_modes(request: Request):
    session = request.app.state.session_by_name()
    current = session.variables.get("agent_mode", "default") if session else None
    has_ws = _has_workspace(session)
    modes = []
    # Only `default` works without a workspace attached — every other real
    # agent mode operates on workspace files. (history/memory/systemPrompts
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
                "disabled": needs_workspace and not has_ws,
            }
        )
    # GUI-only view panels — read-only, never settable as agent_mode. Most
    # render off session state and never need a workspace; the Files panel
    # is the exception (it edits workspace files), so it honors
    # `needs_workspace` and is disabled when no folder is attached.
    views = [
        {
            "name": panel["name"],
            "display_name": panel["display_name"],
            "description": panel["description"],
            "view_only": True,
            "needs_workspace": bool(panel.get("needs_workspace")),
            "disabled": bool(panel.get("needs_workspace")) and not has_ws,
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
    return {"current": current, "modes": modes, "views": views, "has_workspace": has_ws}


@router.post("/{name}")
async def set_mode(name: str, request: Request, session=Depends(require_session)):
    if name not in AGENTIC_MODES:
        raise HTTPException(status_code=400, detail=f"Unknown mode: {name}")
    if name != "default" and not _has_workspace(session):
        raise HTTPException(
            status_code=400,
            detail=f"Mode '{name}' requires a workspace. Add one via the inspector or /workspace folder <path>.",
        )
    with request.app.state.session_lock_for():
        session.variables["agent_mode"] = name
        try:
            session.session_manager.save_history(session.folder_context)
        except Exception:
            pass
    return {"ok": True, "current": name}
