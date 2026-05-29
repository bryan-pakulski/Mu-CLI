"""Agent mode endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from utils.config import AGENT_MODE_METADATA, AGENTIC_MODES

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
    for key in AGENTIC_MODES:
        meta = AGENT_MODE_METADATA.get(key, {})
        needs_workspace = key != "default"
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
    return {"current": current, "modes": modes, "has_workspace": has_ws}


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
