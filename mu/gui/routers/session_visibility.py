"""User-facing session discovery without durable-job execution sessions."""

from __future__ import annotations

import os
from typing import Any, Dict

from fastapi import APIRouter, Request

from mu.session.visibility import is_user_visible_session

from . import sessions as legacy


router = APIRouter()


def _saved_data(name: str) -> Dict[str, Any]:
    value = legacy._read_session_data(name)
    return value if isinstance(value, dict) else {}


def _loaded_data(state, name: str) -> Dict[str, Any]:
    session = state.sessions.get(name)
    if session is None:
        return _saved_data(name)
    variables = getattr(session, "variables", None)
    return {"variables": dict(variables)} if isinstance(variables, dict) else _saved_data(name)


def _visible(state, name: str) -> bool:
    return is_user_visible_session(name, _loaded_data(state, name))


@router.get("/")
async def list_user_sessions(request: Request):
    """Return only conversations intended for the normal Sessions UI.

    Durable engineering-job sessions remain on disk and loadable by exact name
    for resume/trace purposes; they are simply excluded from discovery.
    """

    state = request.app.state
    paths = []
    for path in legacy._session_dirs():
        name = os.path.basename(os.path.dirname(path))
        if is_user_visible_session(name, _saved_data(name)):
            paths.append(path)

    loaded = {name for name in state.sessions.keys() if _visible(state, name)}
    busy = {name for name in legacy._busy_session_names(request) if _visible(state, name)}
    current_name = state.current_session_name
    current = current_name if current_name and _visible(state, current_name) else None

    return {
        "current": current,
        "active": current is not None,
        "loaded": sorted(loaded),
        "busy": sorted(busy),
        "sessions": [
            legacy._summarize(
                path,
                current=current,
                loaded=loaded,
                busy_names=busy,
            )
            for path in paths
        ],
    }


__all__ = ["router", "list_user_sessions"]
