"""Session CRUD + multi-session focus endpoints."""

from __future__ import annotations

import datetime
import glob
import json
import os
import shutil
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request

import utils.config as _config

router = APIRouter()


def _session_dirs() -> list[str]:
    return sorted(
        glob.glob(os.path.join(_config.HISTORY_DIR, "sessions", "*", "session.json"))
    )


def _summarize(
    path: str,
    *,
    current: Optional[str],
    loaded: set[str],
    busy_names: set[str],
) -> Dict[str, Any]:
    name = os.path.basename(os.path.dirname(path))
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0.0
    return {
        "name": name,
        "is_current": name == current,
        "is_loaded": name in loaded,
        "is_busy": name in busy_names,
        "modified_at": datetime.datetime.fromtimestamp(mtime).isoformat(timespec="seconds"),
        "modified_unix": mtime,
    }


def _read_session_data(name: str) -> Dict[str, Any] | None:
    path = os.path.join(_config.HISTORY_DIR, "sessions", name, "session.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _busy_session_names(request: Request) -> set[str]:
    state = request.app.state
    out: set[str] = set()
    for name, evt in state.session_busy.items():
        if evt.is_set():
            out.add(name)
    return out


def _resolve(request: Request, name: Optional[str]):
    return request.app.state.session_by_name(name)


@router.get("")
async def list_sessions(request: Request):
    state = request.app.state
    current = state.current_session_name
    loaded = set(state.sessions.keys())
    busy = _busy_session_names(request)
    return {
        "current": current,
        "active": current is not None,
        "loaded": sorted(loaded),
        "busy": sorted(busy),
        "sessions": [
            _summarize(p, current=current, loaded=loaded, busy_names=busy)
            for p in _session_dirs()
        ],
    }


@router.get("/active")
async def active_session(request: Request, session_name: Optional[str] = None):
    state = request.app.state
    session = _resolve(request, session_name)
    if session is None:
        return {"active": False, "external_active": False}
    sm = session.session_manager
    watcher = getattr(state, "watcher", None)
    is_busy = state.session_busy_for(sm.current_session_name).is_set()
    return {
        "active": True,
        "name": sm.current_session_name,
        "provider": session.provider.name if session.provider else None,
        "model": session.provider.model_name if session.provider else None,
        "history_length": len(sm.history),
        "tokens": dict(sm.token_counts),
        "agent_mode": session.variables.get("agent_mode", "default"),
        "external_active": bool(getattr(watcher, "external_active", False)),
        "external_last_at": float(getattr(watcher, "external_last_at", 0.0)),
        "is_busy": is_busy,
        "is_current": sm.current_session_name == state.current_session_name,
    }


@router.get("/current/history")
async def get_history(request: Request, session_name: Optional[str] = None):
    session = _resolve(request, session_name)
    if session is None:
        return {"name": "", "turns": []}
    sm = session.session_manager
    turns = []
    for idx, turn in enumerate(sm.history):
        role = turn.get("role")
        parts_out = []
        for part in turn.get("parts", []):
            ptype = part.get("type")
            if ptype == "text":
                parts_out.append({"type": "text", "text": part.get("text", "")})
            elif ptype == "tool_call":
                parts_out.append(
                    {
                        "type": "tool_call",
                        "tool_name": part.get("tool_name"),
                    }
                )
            elif ptype == "tool_result":
                parts_out.append(
                    {
                        "type": "tool_result",
                        "tool_name": part.get("tool_name"),
                        "preview": str(part.get("tool_result", ""))[:400],
                    }
                )
        turns.append({"index": idx, "role": role, "parts": parts_out})
    return {"name": sm.current_session_name, "turns": turns}


@router.post("")
async def create_session(request: Request, payload: Dict[str, Any]):
    name = str(payload.get("name") or "").strip()
    provider = str(payload.get("provider") or "").strip() or None
    model = str(payload.get("model") or "").strip() or None
    activate = bool(payload.get("activate", True))

    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    if not provider or not model:
        raise HTTPException(status_code=400, detail="provider and model are required")
    if _read_session_data(name) is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Session '{name}' already exists. Load it instead.",
        )

    if not activate:
        path = os.path.join(_config.HISTORY_DIR, "sessions", name)
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "session.json"), "w") as fh:
            json.dump(
                {"history": [], "provider_config": {"provider": provider, "model": model}},
                fh,
                indent=2,
            )
        return {"ok": True, "name": name, "active": False}

    return await load_session(name, request, payload={"provider": provider, "model": model})


@router.post("/{name}/load")
async def load_session(name: str, request: Request, payload: Dict[str, Any] | None = None):
    """Load `name` into the daemon and focus it. Idempotent — if
    already loaded, just focuses without rebuilding the Session."""
    payload = payload or {}
    provider = (str(payload.get("provider") or "").strip() or None)
    model = (str(payload.get("model") or "").strip() or None)

    state = request.app.state

    # Already loaded? Just focus it.
    if name in state.sessions:
        state.current_session_name = name
        return {"ok": True, "name": name, "active": True, "loaded": True}

    existing = _read_session_data(name)
    if existing is None and (not provider or not model):
        raise HTTPException(
            status_code=400,
            detail="Session does not exist; provider and model are required to create it.",
        )
    if existing is not None:
        saved = existing.get("provider_config") or {}
        if not (provider and model) and not (saved.get("provider") and saved.get("model")):
            raise HTTPException(
                status_code=400,
                detail="Session has no saved provider; supply provider and model.",
            )

    try:
        state.load_session(name=name, provider=provider, model=model)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"ok": True, "name": name, "active": True, "loaded": True}


@router.post("/{name}/focus")
async def focus_session(name: str, request: Request):
    """Mark `name` as the focused session without (re)loading. Used by
    the GUI when the user clicks a session already resident in memory."""
    state = request.app.state
    if name not in state.sessions:
        raise HTTPException(
            status_code=404,
            detail=f"Session {name!r} is not loaded. POST /load first.",
        )
    state.current_session_name = name
    return {"ok": True, "name": name}


@router.delete("/active")
async def unload_active_session(request: Request):
    """Drop the currently-focused session from memory (file untouched)."""
    state = request.app.state
    cur = state.current_session_name
    if cur and cur in state.sessions:
        with state.session_lock_for(cur):
            state.unload_session(name=cur)
    return {"ok": True, "active": False}


@router.post("/{name}/unload")
async def unload_named_session(name: str, request: Request):
    """Drop a specific session from memory (file untouched). Differs
    from DELETE /active in that you can unload one that isn't focused."""
    state = request.app.state
    if name not in state.sessions:
        raise HTTPException(
            status_code=404,
            detail=f"Session {name!r} is not loaded.",
        )
    if state.session_busy_for(name).is_set():
        raise HTTPException(
            status_code=409,
            detail=f"Session {name!r} has a turn in flight; refuse to unload.",
        )
    with state.session_lock_for(name):
        state.unload_session(name=name)
    return {"ok": True, "unloaded": name}


@router.delete("/{name}")
async def delete_session(name: str, request: Request):
    state = request.app.state
    if name in state.sessions:
        raise HTTPException(
            status_code=400,
            detail=f"Session {name!r} is loaded — unload it first.",
        )
    session_dir = os.path.join(_config.HISTORY_DIR, "sessions", name)
    if not os.path.isdir(session_dir):
        raise HTTPException(status_code=404, detail=f"Session '{name}' not found.")
    shutil.rmtree(session_dir, ignore_errors=True)
    return {"ok": True}
