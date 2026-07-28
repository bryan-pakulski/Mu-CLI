"""Session CRUD + multi-session focus endpoints."""

from __future__ import annotations

import asyncio
import datetime
import glob
import json
import os
import shutil
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request

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



def _normalize_workspace_paths(values: Any) -> list[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        raise HTTPException(status_code=400, detail="workspaces must be a list of directory paths")

    normalized: list[str] = []
    for raw in values:
        value = str(raw or "").strip()
        if not value:
            continue
        path = os.path.abspath(os.path.expanduser(value))
        if not os.path.isdir(path):
            raise HTTPException(
                status_code=400,
                detail=f"Workspace path is not a directory: {path}",
            )
        if path not in normalized:
            normalized.append(path)
    return normalized


def _workspace_paths_for(request: Request, name: str) -> list[str]:
    session = request.app.state.sessions.get(name)
    if session is not None:
        folder_context = getattr(session, "folder_context", None)
        return list(getattr(folder_context, "folders", []) or [])

    data = _read_session_data(name)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Session '{name}' not found.")
    folder_context = data.get("folder_context") or {}
    return list(folder_context.get("folders") or [])


def _suggest_workspace_paths(raw_path: str, limit: int) -> Dict[str, Any]:
    raw = str(raw_path or "").strip()
    expanded = os.path.expanduser(raw or "~")
    if not os.path.isabs(expanded):
        expanded = os.path.join(os.getcwd(), expanded)
    resolved = os.path.abspath(expanded)

    if not raw:
        base_dir = resolved
        prefix = ""
    elif raw.endswith(os.sep):
        base_dir = resolved
        prefix = ""
    elif os.path.isdir(resolved):
        base_dir = resolved
        prefix = ""
    else:
        base_dir = os.path.dirname(resolved) or os.getcwd()
        prefix = os.path.basename(resolved)

    suggestions: list[str] = []
    try:
        with os.scandir(base_dir) as entries:
            for entry in entries:
                if not entry.is_dir(follow_symlinks=False):
                    continue
                if entry.name.startswith(".") and not prefix.startswith("."):
                    continue
                if prefix and not entry.name.lower().startswith(prefix.lower()):
                    continue
                suggestions.append(os.path.abspath(entry.path))
    except (OSError, PermissionError):
        suggestions = []

    suggestions.sort(key=lambda value: value.lower())
    return {
        "query": raw,
        "resolved_path": resolved,
        "exists": os.path.isdir(resolved),
        "suggestions": suggestions[:limit],
    }


def _busy_session_names(request: Request) -> set[str]:
    state = request.app.state
    out: set[str] = set()
    for name, evt in state.session_busy.items():
        if evt.is_set():
            out.add(name)
    return out


def _ollama_seed_vars(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Pull the ollama mode/host/key the GUI sent in a create/load
    payload, if any. Empty/absent values are dropped so defaults stand."""
    out: Dict[str, Any] = {}
    for key in ("ollama_mode", "ollama_host", "ollama_api_key"):
        val = payload.get(key)
        if val is not None and str(val).strip() != "":
            out[key] = str(val).strip()
    return out


def _apply_ollama_vars(session, ollama_vars: Dict[str, Any]) -> None:
    """Seed ollama mode/host/key onto a freshly-built session's variables
    and re-sync the running provider so a cloud session created from the
    welcome modal starts on ollama.com with its key."""
    if not ollama_vars or session is None:
        return
    session.variables.update(ollama_vars)
    try:
        from mucli import sync_provider_settings

        sync_provider_settings(session)
    except ImportError:
        pass
    try:
        session.session_manager.save_history(session.folder_context)
    except Exception:
        pass


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
        "workspaces": list(getattr(session.folder_context, "folders", []) or []),
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


@router.get("/workspaces/suggest")
async def suggest_workspaces(
    path: str = Query(default=""),
    limit: int = Query(default=12, ge=1, le=30),
):
    """Suggest host directories while creating or editing a workspace."""
    return await asyncio.to_thread(_suggest_workspace_paths, path, limit)


@router.get("/{name}/workspace")
async def get_session_workspace(name: str, request: Request):
    return {"name": name, "workspaces": _workspace_paths_for(request, name)}


@router.put("/{name}/workspace")
async def update_session_workspace(name: str, request: Request, payload: Dict[str, Any]):
    values = payload.get("workspaces", payload.get("workspace", []))
    workspaces = _normalize_workspace_paths(values)
    state = request.app.state
    session = state.sessions.get(name)

    if session is not None:
        if state.session_busy_for(name).is_set():
            raise HTTPException(
                status_code=409,
                detail="Workspace cannot be changed while the session is running.",
            )
        with state.session_lock_for(name):
            folder_context = session.folder_context
            for current in list(folder_context.folders):
                folder_context.remove_folder(current)
            for workspace in workspaces:
                if not folder_context.add_folder(workspace):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Could not attach workspace: {workspace}",
                    )
            session.session_manager.save_history(folder_context)
        return {"ok": True, "name": name, "workspaces": list(folder_context.folders)}

    data = _read_session_data(name)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Session '{name}' not found.")
    folder_context = dict(data.get("folder_context") or {})
    folder_context["folders"] = workspaces
    data["folder_context"] = folder_context
    session_path = os.path.join(_config.HISTORY_DIR, "sessions", name, "session.json")
    with open(session_path, "w") as fh:
        json.dump(data, fh, indent=2)
    return {"ok": True, "name": name, "workspaces": workspaces}


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

    workspace = str(payload.get("workspace") or "").strip()
    if workspace:
        workspace = os.path.expanduser(workspace)
        if not os.path.isdir(workspace):
            raise HTTPException(
                status_code=400,
                detail=f"Workspace path is not a directory: {workspace}",
            )

    if not activate:
        data: Dict[str, Any] = {
            "history": [],
            "provider_config": {"provider": provider, "model": model},
        }
        if workspace:
            data["folder_context"] = {"folders": [workspace]}
        ollama_vars = _ollama_seed_vars(payload) if provider == "ollama" else {}
        if ollama_vars:
            # Seed into the persisted variables so the first load starts
            # on the chosen endpoint (e.g. cloud + key) without an extra
            # switch round-trip.
            data["variables"] = ollama_vars
        path = os.path.join(_config.HISTORY_DIR, "sessions", name)
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "session.json"), "w") as fh:
            json.dump(data, fh, indent=2)
        return {"ok": True, "name": name, "active": False}

    ollama_vars = _ollama_seed_vars(payload) if provider == "ollama" else {}
    # Persist the connection selection *before* building the session.  Model
    # discovery during build must use the same endpoint as the welcome modal;
    # otherwise an OLLAMA_API_KEY can make a locally selected model appear to
    # be missing from ollama.com before the later variable sync runs.
    data: Dict[str, Any] = {
        "history": [],
        "provider_config": {"provider": provider, "model": model},
    }
    if ollama_vars:
        data["variables"] = ollama_vars
    path = os.path.join(_config.HISTORY_DIR, "sessions", name)
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "session.json"), "w") as fh:
        json.dump(data, fh, indent=2)

    load_payload: Dict[str, Any] = {"provider": provider, "model": model}
    load_payload.update(ollama_vars)
    result = await load_session(name, request, payload=load_payload)

    if workspace:
        session = request.app.state.session_by_name(name)
        if session:
            session.folder_context.add_folder(workspace)
            session.session_manager.save_history(session.folder_context)

    return result


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
        await asyncio.to_thread(
            state.load_session, name=name, provider=provider, model=model
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    # Seed ollama mode/host/key supplied by the GUI (welcome modal) onto
    # the freshly-built session so a cloud session starts on ollama.com.
    ollama_vars = _ollama_seed_vars(payload) if provider == "ollama" else {}
    if ollama_vars:
        _apply_ollama_vars(state.session_by_name(name), ollama_vars)

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
        # Never wait behind an agent turn while servicing a UI action.  The
        # old lock acquisition made the Leave button appear frozen exactly
        # when it was most needed.  The client can interrupt first, or use
        # /detach below to leave the GUI without touching the running session.
        if state.session_busy_for(cur).is_set():
            raise HTTPException(
                status_code=409,
                detail="Session has a turn in flight; interrupt it before unloading.",
            )
        with state.session_lock_for(cur):
            state.unload_session(name=cur)
    return {"ok": True, "active": False}


@router.post("/active/detach")
async def detach_active_session(request: Request):
    """Leave the current GUI session without unloading it.

    This is deliberately safe during a stuck/erroring turn: it only clears
    the browser focus target, leaving the in-memory session and its worker
    intact until the user returns and interrupts/unloads it deliberately.
    """
    request.app.state.current_session_name = None
    return {"ok": True, "active": False, "detached": True}


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
