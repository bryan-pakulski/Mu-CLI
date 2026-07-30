"""Session CRUD + multi-session focus endpoints."""

from __future__ import annotations

import asyncio
import datetime
import glob
import json
import os
import shutil
import time
from typing import Any, Dict, Optional

from mu.artifact import ArtifactRegistry
from mu.container.docker_cli import ContainerRuntimeError
from mu.container.network import DEFAULT_EGRESS_ALLOW
from mu.tools.capabilities import normalize_session_type

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

import utils.config as _config

router = APIRouter()


def _set_container_creation_status(
    request: Request,
    name: str,
    *,
    stage: str,
    message: str,
    state: str = "running",
    detail: str | None = None,
    reset_output: bool = False,
) -> Dict[str, Any]:
    app_state = request.app.state
    lock = getattr(app_state, "container_creation_lock", None)
    statuses = getattr(app_state, "container_creation_status", None)
    if statuses is None:
        statuses = {}
        app_state.container_creation_status = statuses

    def update() -> Dict[str, Any]:
        previous = statuses.get(name) if isinstance(statuses.get(name), dict) else {}
        logs = [] if reset_output else list(previous.get("logs") or [])
        next_seq = 1 if reset_output else int(previous.get("next_log_seq") or 1)
        payload: Dict[str, Any] = {
            "name": name,
            "state": state,
            "stage": stage,
            "message": message,
            "updated_at": time.time(),
            "logs": logs,
            "next_log_seq": next_seq,
        }
        if detail:
            payload["detail"] = detail
        statuses[name] = payload
        return dict(payload)

    if lock is None:
        return update()
    with lock:
        return update()


def _append_container_creation_output(
    request: Request,
    name: str,
    stream: str,
    text: str,
) -> None:
    """Append one redacted command/stdout/stderr record to live status."""
    value = str(text or "").rstrip("\r\n")
    if not value:
        return
    app_state = request.app.state
    lock = getattr(app_state, "container_creation_lock", None)
    statuses = getattr(app_state, "container_creation_status", None)
    if statuses is None:
        statuses = {}
        app_state.container_creation_status = statuses

    def append() -> None:
        status = statuses.setdefault(
            name,
            {
                "name": name,
                "state": "running",
                "stage": "queued",
                "message": "Waiting for container creation to start…",
                "updated_at": time.time(),
                "logs": [],
                "next_log_seq": 1,
            },
        )
        logs = status.setdefault("logs", [])
        seq = int(status.get("next_log_seq") or 1)
        logs.append(
            {
                "seq": seq,
                "stream": stream if stream in {"command", "stdout", "stderr"} else "stdout",
                "text": value[:12000],
                "at": time.time(),
            }
        )
        # Bound polling payloads while retaining enough Docker output to debug.
        if len(logs) > 1200:
            del logs[: len(logs) - 1200]
        status["next_log_seq"] = seq + 1
        status["updated_at"] = time.time()

    if lock is None:
        append()
    else:
        with lock:
            append()


def _get_container_creation_status(
    request: Request,
    name: str,
    *,
    after: int = 0,
) -> Dict[str, Any]:
    app_state = request.app.state
    statuses = getattr(app_state, "container_creation_status", {})
    lock = getattr(app_state, "container_creation_lock", None)

    def read() -> Dict[str, Any] | None:
        value = statuses.get(name)
        if not isinstance(value, dict):
            return None
        payload = dict(value)
        payload["logs"] = [
            dict(item)
            for item in (value.get("logs") or [])
            if int(item.get("seq") or 0) > after
        ]
        return payload

    if lock is None:
        value = read()
    else:
        with lock:
            value = read()
    if value is not None:
        return value
    return {
        "name": name,
        "state": "idle",
        "stage": "idle",
        "message": "Waiting for container creation to start…",
        "updated_at": 0.0,
        "logs": [],
        "next_log_seq": 1,
    }


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
    data = _read_session_data(name) or {}
    variables = data.get("variables") or {}
    container_config = data.get("container_config") or {}
    return {
        "name": name,
        "is_current": name == current,
        "is_loaded": name in loaded,
        "is_busy": name in busy_names,
        "modified_at": datetime.datetime.fromtimestamp(mtime).isoformat(timespec="seconds"),
        "modified_unix": mtime,
        "session_type": normalize_session_type(variables.get("session_type")),
        "container_name": container_config.get("container_name"),
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


def _visualization_from_tool_result(value: Any) -> Dict[str, Any] | None:
    payload = value
    for _ in range(3):
        if not isinstance(payload, str):
            break
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return None
    if not isinstance(payload, dict):
        return None

    candidates = [payload.get("artifact")]
    data = payload.get("data")
    if isinstance(data, dict):
        candidates.append(data.get("artifact"))
    artifacts = payload.get("artifacts")
    if isinstance(artifacts, list):
        candidates.extend(artifacts)

    for candidate in candidates:
        if not isinstance(candidate, dict) or candidate.get("kind") != "visualization":
            continue
        allowed = {
            "artifact_id",
            "name",
            "title",
            "size",
            "mime_type",
            "created_at",
            "kind",
            "display",
            "height",
            "view_url",
            "download_url",
        }
        return {key: candidate[key] for key in allowed if key in candidate}
    return None


def _history_preview(value: Any, limit: int = 6000) -> str:
    """Return a bounded, readable trace preview for durable history replay."""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "\n… [history preview truncated]"


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
        "session_type": normalize_session_type(
            session.variables.get("session_type", "workspace")
        ),
        "container": (
            session.container_ref.to_dict(include_secret=False)
            if getattr(session, "container_ref", None) is not None
            else None
        ),
        "external_active": bool(
            watcher.external_active_for(sm.current_session_name)
            if watcher is not None and hasattr(watcher, "external_active_for")
            else False
        ),
        "external_last_at": float(
            watcher.external_last_at_for(sm.current_session_name)
            if watcher is not None and hasattr(watcher, "external_last_at_for")
            else 0.0
        ),
        "is_busy": is_busy,
        "is_current": sm.current_session_name == state.current_session_name,
        "workspaces": list(getattr(session.folder_context, "folders", []) or []),
    }


@router.get("/creation-status/{name}")
async def container_creation_status(
    name: str,
    request: Request,
    after: int = Query(default=0, ge=0),
):
    """Return live progress and output added after the supplied sequence."""
    return _get_container_creation_status(request, name, after=after)


@router.get("/current/history")
async def get_history(
    request: Request,
    session_name: Optional[str] = None,
    limit_turns: Optional[int] = Query(default=None, ge=1, le=500),
    artifact_limit: Optional[int] = Query(default=None, ge=0, le=100),
):
    session = _resolve(request, session_name)
    if session is None:
        return {"name": "", "turns": []}
    sm = session.session_manager
    total_turns = len(sm.history)
    start_index = (
        max(0, total_turns - limit_turns)
        if limit_turns is not None
        else 0
    )
    history_window = sm.history[start_index:]
    turns = []
    seen_visualization_ids: set[str] = set()
    for idx, turn in enumerate(history_window, start=start_index):
        role = turn.get("role")
        parts_out = []
        for part in turn.get("parts", []):
            ptype = part.get("type")
            if ptype == "text":
                parts_out.append({"type": "text", "text": part.get("text", "")})
            elif ptype in {"thinking", "reasoning", "thought"}:
                text = _history_preview(part.get("text", ""), limit=8000)
                if text:
                    parts_out.append(
                        {"type": "thinking", "text": text, "collapsed": True}
                    )
            elif ptype == "tool_call":
                parts_out.append(
                    {
                        "type": "tool_call",
                        "tool_name": part.get("tool_name"),
                        "tool_args": part.get("tool_args"),
                        "collapsed": True,
                    }
                )
            elif ptype == "tool_result":
                raw_result = part.get("tool_result", "")
                result_part = {
                    "type": "tool_result",
                    "tool_name": part.get("tool_name"),
                    "preview": _history_preview(raw_result),
                    "collapsed": True,
                }
                visualization = _visualization_from_tool_result(raw_result)
                if visualization is not None:
                    result_part["artifact"] = visualization
                    artifact_id = str(visualization.get("artifact_id") or "")
                    if artifact_id:
                        seen_visualization_ids.add(artifact_id)
                parts_out.append(result_part)
        turns.append({"index": idx, "role": role, "parts": parts_out})

    # Artifact registry is the durable source of truth. Tool-result history may
    # be compacted, truncated, or written by an older worker, so replay any
    # visualization not already represented by a surviving tool result.
    session_dir = os.path.join(
        _config.HISTORY_DIR, "sessions", sm.current_session_name
    )
    try:
        artifacts = ArtifactRegistry(session_dir).list()
        if artifact_limit is not None:
            # Registry order is newest-first. Limit before reversing so mobile
            # does not mount every historical visualization WebView at once.
            artifacts = artifacts[:artifact_limit]
        for artifact in reversed(artifacts):
            visualization = _visualization_from_tool_result({"artifact": artifact})
            if visualization is None:
                continue
            artifact_id = str(visualization.get("artifact_id") or "")
            if not artifact_id or artifact_id in seen_visualization_ids:
                continue
            seen_visualization_ids.add(artifact_id)
            turns.append(
                {
                    "index": total_turns + len(turns),
                    "role": "assistant",
                    "parts": [
                        {
                            "type": "tool_result",
                            "tool_name": "publish_visualization",
                            "preview": (
                                "Published visualization: "
                                f"{visualization.get('title') or visualization.get('name')}"
                            ),
                            "artifact": visualization,
                            "collapsed": True,
                        }
                    ],
                }
            )
    except OSError:
        pass

    return {
        "name": sm.current_session_name,
        "turns": turns,
        "total_turns": total_turns,
        "start_index": start_index,
        "has_more": start_index > 0,
    }


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


async def _run_container_creation_job(
    request: Request,
    *,
    name: str,
    provider: str,
    model: str,
    ollama_vars: Dict[str, Any],
    container_config: Dict[str, Any],
    existing_container: str | None = None,
) -> None:
    """Build or attach a container session after the create request returns."""

    def report_progress(stage: str, message: str) -> None:
        _set_container_creation_status(
            request,
            name,
            stage=stage,
            message=message,
        )

    def report_output(stream: str, text: str) -> None:
        _append_container_creation_output(request, name, stream, text)

    container_started = False
    attached_existing = False
    try:
        if existing_container:
            report_progress(
                "attaching_container",
                f"Attaching the session to {existing_container}…",
            )
            ref = await asyncio.to_thread(
                request.app.state.container_supervisor.attach_session,
                existing_container,
                name,
                supervisor_url=f"http://host.docker.internal:{request.app.state.port}",
                progress=report_progress,
                output=report_output,
            )
            attached_existing = True
            container_config = request.app.state.container_supervisor.configuration(ref.name)
            session_path = os.path.join(
                _config.HISTORY_DIR, "sessions", name, "session.json"
            )
            with open(session_path, "r", encoding="utf-8") as handle:
                saved = json.load(handle)
            saved["container_config"] = dict(container_config)
            with open(session_path, "w", encoding="utf-8") as handle:
                json.dump(saved, handle, indent=2)
        else:
            await asyncio.to_thread(
                request.app.state.container_supervisor.create,
                container_name=container_config["container_name"],
                session_name=name,
                dockerfile=container_config["dockerfile"],
                template_name=container_config.get("template_name"),
                mounts=container_config["mounts"],
                egress_allow=container_config["egress_allow"],
                egress_deny=container_config["egress_deny"],
                supervisor_url=f"http://host.docker.internal:{request.app.state.port}",
                progress=report_progress,
                output=report_output,
            )
        container_started = True
        _set_container_creation_status(
            request,
            name,
            stage="loading_session",
            message="Container is running; loading the MuCLI session…",
        )
        load_payload: Dict[str, Any] = {"provider": provider, "model": model}
        load_payload.update(ollama_vars)
        await load_session(name, request, payload=load_payload)
        _set_container_creation_status(
            request,
            name,
            stage="ready",
            message="Container session is ready.",
            state="ready",
        )
    except Exception as exc:  # keep the status endpoint alive for diagnostics
        detail = str(getattr(exc, "detail", None) or exc)
        report_output("stderr", detail)
        if attached_existing:
            try:
                await asyncio.to_thread(
                    request.app.state.container_supervisor.detach_session,
                    existing_container or container_config["container_name"],
                    name,
                    stop_if_idle=False,
                )
            except Exception:
                pass
        if not container_started:
            session_dir = os.path.join(_config.HISTORY_DIR, "sessions", name)
            shutil.rmtree(session_dir, ignore_errors=True)
        _set_container_creation_status(
            request,
            name,
            stage="failed",
            message="Container creation failed.",
            state="error",
            detail=detail,
        )
    finally:
        tasks = getattr(request.app.state, "container_creation_tasks", {})
        tasks.pop(name, None)


@router.post("")
async def create_session(request: Request, payload: Dict[str, Any]):
    name = str(payload.get("name") or "").strip()
    provider = str(payload.get("provider") or "").strip() or None
    model = str(payload.get("model") or "").strip() or None
    activate = bool(payload.get("activate", True))
    background_container = bool(payload.get("background_container", False))
    session_type = normalize_session_type(payload.get("session_type"))
    container_source = str(payload.get("container_source") or "new").strip().lower()
    existing_container = str(payload.get("existing_container") or "").strip() or None

    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    if not provider or not model:
        raise HTTPException(status_code=400, detail="provider and model are required")
    if _read_session_data(name) is not None:
        status = _get_container_creation_status(request, name)
        tasks = getattr(request.app.state, "container_creation_tasks", {})
        if name in tasks and status.get("state") == "running":
            return JSONResponse(
                status_code=202,
                content={
                    "ok": True,
                    "name": name,
                    "active": False,
                    "session_type": "container",
                    "creation_state": status.get("stage", "running"),
                },
            )
        raise HTTPException(
            status_code=409,
            detail=f"Session '{name}' already exists. Load it instead.",
        )

    workspace = str(payload.get("workspace") or "").strip()
    if session_type == "chat":
        workspace = ""
    if workspace:
        workspace = os.path.expanduser(workspace)
        if not os.path.isdir(workspace):
            raise HTTPException(
                status_code=400,
                detail=f"Workspace path is not a directory: {workspace}",
            )

    ollama_vars = _ollama_seed_vars(payload) if provider == "ollama" else {}
    variables: Dict[str, Any] = {**ollama_vars, "session_type": session_type}
    if session_type == "container":
        variables.update(
            {
                "yolo": True,
                "strict_mode": False,
                "plan_mode": False,
                "lazy_tools_enabled": False,
                "security_allow_secret_paths": False,
            }
        )

    data: Dict[str, Any] = {
        "history": [],
        "provider_config": {"provider": provider, "model": model},
        "variables": variables,
    }
    if workspace and session_type == "workspace":
        data["folder_context"] = {"folders": [workspace]}

    container_config: Dict[str, Any] | None = None
    if session_type == "container":
        if container_source not in {"new", "existing"}:
            raise HTTPException(status_code=400, detail="container_source must be new or existing")
        if container_source == "existing":
            if not existing_container:
                raise HTTPException(status_code=400, detail="existing_container is required")
            ref = request.app.state.container_supervisor.resolve(existing_container)
            if ref is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Managed container not found: {existing_container}",
                )
            container_config = request.app.state.container_supervisor.configuration(ref.name)
            existing_container = ref.name
        else:
            container_name = str(payload.get("container_name") or f"mucli-{name}").strip()
            mounts = [item for item in (payload.get("mounts") or []) if isinstance(item, dict)]
            egress_allow = [
                str(item).strip()
                for item in (payload.get("egress_allow") or DEFAULT_EGRESS_ALLOW)
                if str(item).strip()
            ]
            egress_deny = [
                str(item).strip()
                for item in (payload.get("egress_deny") or [])
                if str(item).strip()
            ]
            container_config = {
                "container_name": container_name,
                "dockerfile": payload.get("dockerfile") or None,
                "template_name": str(payload.get("template_name") or "") or None,
                "mounts": mounts,
                "egress_allow": egress_allow,
                "egress_deny": egress_deny,
            }
        data["container_config"] = container_config

    path = os.path.join(_config.HISTORY_DIR, "sessions", name)
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "session.json"), "w") as fh:
        json.dump(data, fh, indent=2)

    if container_config is not None:
        _set_container_creation_status(
            request,
            name,
            stage="queued",
            message="Container configuration saved; waiting for Docker…",
            reset_output=True,
        )

    if not activate:
        return {
            "ok": True,
            "name": name,
            "active": False,
            "session_type": session_type,
        }

    if container_config is not None:
        if background_container:
            tasks = getattr(request.app.state, "container_creation_tasks", None)
            if tasks is None:
                tasks = {}
                request.app.state.container_creation_tasks = tasks
            task = asyncio.create_task(
                _run_container_creation_job(
                    request,
                    name=name,
                    provider=provider,
                    model=model,
                    ollama_vars=ollama_vars,
                    container_config=container_config,
                    existing_container=existing_container if container_source == "existing" else None,
                ),
                name=f"mucli-container-create-{name}",
            )
            tasks[name] = task
            return JSONResponse(
                status_code=202,
                content={
                    "ok": True,
                    "name": name,
                    "active": False,
                    "session_type": session_type,
                    "creation_state": "queued",
                },
            )

        await _run_container_creation_job(
            request,
            name=name,
            provider=provider,
            model=model,
            ollama_vars=ollama_vars,
            container_config=container_config,
            existing_container=existing_container if container_source == "existing" else None,
        )
        status = _get_container_creation_status(request, name)
        if status.get("state") == "error":
            raise HTTPException(
                status_code=500,
                detail=status.get("detail") or "Container creation failed.",
            )
        ref = request.app.state.container_supervisor.container_for_session(name)
        return {
            "ok": True,
            "name": name,
            "active": True,
            "loaded": True,
            "session_type": session_type,
            "container": ref.to_dict(include_secret=False) if ref else None,
        }

    load_payload: Dict[str, Any] = {"provider": provider, "model": model}
    load_payload.update(ollama_vars)
    try:
        result = await load_session(name, request, payload=load_payload)
    except Exception:
        raise

    if workspace and session_type == "workspace":
        session = request.app.state.session_by_name(name)
        if session:
            session.folder_context.add_folder(workspace)
            session.session_manager.save_history(session.folder_context)

    return {
        **result,
        "session_type": session_type,
        "container": (
            request.app.state.container_supervisor.container_for_session(name).to_dict(include_secret=False)
            if session_type == "container"
            else None
        ),
    }

@router.post("/{name}/load")
async def load_session(name: str, request: Request, payload: Dict[str, Any] | None = None):
    """Load and focus a host session mirror; attach its worker when container-backed."""
    payload = payload or {}
    provider = str(payload.get("provider") or "").strip() or None
    model = str(payload.get("model") or "").strip() or None
    state = request.app.state

    if name in state.sessions:
        state.current_session_name = name
        session = state.sessions[name]
        return {
            "ok": True,
            "name": name,
            "active": True,
            "loaded": True,
            "session_type": normalize_session_type(session.variables.get("session_type")),
        }

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
        provider = provider or saved.get("provider")
        model = model or saved.get("model")

    variables = (existing or {}).get("variables") or {}
    session_type = normalize_session_type(variables.get("session_type"))
    container_config = (existing or {}).get("container_config") or {}
    container_ref = None
    if session_type == "container":
        config = container_config
        try:
            container_ref = await asyncio.to_thread(
                state.container_supervisor.create,
                container_name=str(config.get("container_name") or f"mucli-{name}"),
                session_name=name,
                dockerfile=config.get("dockerfile"),
                template_name=config.get("template_name"),
                mounts=config.get("mounts") or [],
                egress_allow=config.get("egress_allow") or DEFAULT_EGRESS_ALLOW,
                egress_deny=config.get("egress_deny") or [],
                supervisor_url=f"http://host.docker.internal:{state.port}",
            )
        except (ContainerRuntimeError, OSError, ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=500, detail=f"Container load failed: {exc}") from exc

    try:
        await asyncio.to_thread(state.load_session, name=name, provider=provider, model=model)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    session = state.session_by_name(name)
    ollama_vars = _ollama_seed_vars(payload) if provider == "ollama" else {}
    if ollama_vars:
        _apply_ollama_vars(session, ollama_vars)
    if session is not None:
        session.session_manager.container_config = dict(container_config or {})
        session.variables["session_type"] = session_type
        if session_type == "container":
            session.variables.update(
                {
                    "yolo": True,
                    "strict_mode": False,
                    "plan_mode": False,
                    "lazy_tools_enabled": False,
                    "security_allow_secret_paths": False,
                }
            )
            session.container_ref = container_ref
        session.session_manager.save_history(session.folder_context)
        session.sync_runtime_state()

    return {
        "ok": True,
        "name": name,
        "active": True,
        "loaded": True,
        "session_type": session_type,
        "container": container_ref.to_dict(include_secret=False) if container_ref else None,
    }

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
        state.container_supervisor.detach(cur, stop_if_idle=True)
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
    state.container_supervisor.detach(name, stop_if_idle=True)
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
    state.container_supervisor.detach(name, stop_if_idle=True)
    shutil.rmtree(session_dir, ignore_errors=True)
    return {"ok": True}
