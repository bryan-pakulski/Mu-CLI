"""Agent mode endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from utils.config import AGENT_MODE_METADATA, AGENTIC_MODES

from ..deps import require_session

router = APIRouter()


@router.get("")
async def list_modes(request: Request):
    session = request.app.state.session_by_name()
    current = session.variables.get("agent_mode", "default") if session else None
    modes = []
    for key in AGENTIC_MODES:
        meta = AGENT_MODE_METADATA.get(key, {})
        modes.append(
            {
                "name": key,
                "display_name": meta.get("display_name", key.title()),
                "description": meta.get("description", ""),
                "is_current": key == current,
            }
        )
    return {"current": current, "modes": modes}


@router.post("/{name}")
async def set_mode(name: str, request: Request, session=Depends(require_session)):
    if name not in AGENTIC_MODES:
        raise HTTPException(status_code=400, detail=f"Unknown mode: {name}")
    with request.app.state.session_lock_for():
        session.variables["agent_mode"] = name
        try:
            session.session_manager.save_history(session.folder_context)
        except Exception:
            pass
    return {"ok": True, "current": name}
