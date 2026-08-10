"""Authoritative saved-session history endpoint for the web GUI.

Opening a saved session must not depend on the freshly reconstructed in-memory
Session already containing its transcript.  The session JSON is the durable
source of truth, so named history requests render directly from that saved
history.  Unscoped/current-session requests continue to use the live in-memory
path in ``sessions.get_history``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, Optional

from fastapi import APIRouter, Query, Request

from . import sessions as sessions_router


router = APIRouter()


def _saved_history_session(name: str):
    """Return a minimal session facade backed by the durable session JSON."""
    data = sessions_router._read_session_data(name)
    if data is None:
        return None

    if isinstance(data, list):
        history = data
    elif isinstance(data, dict):
        history = data.get("history", [])
    else:
        history = []

    if not isinstance(history, list):
        history = []

    manager = SimpleNamespace(
        current_session_name=name,
        history=history,
    )
    return SimpleNamespace(session_manager=manager)


def _request_for_saved_session(session):
    """Build the tiny request facade consumed by sessions.get_history()."""
    state = SimpleNamespace(session_by_name=lambda _name=None: session)
    return SimpleNamespace(app=SimpleNamespace(state=state))


@router.get("/current/history")
async def get_authoritative_history(
    request: Request,
    session_name: Optional[str] = None,
    limit_turns: Optional[int] = Query(default=None, ge=1, le=500),
    artifact_limit: Optional[int] = Query(default=None, ge=0, le=100),
    before_index: Optional[int] = Query(default=None, ge=0),
) -> Dict[str, Any]:
    """Return persisted history for a named saved session.

    The GUI always supplies ``session_name`` once focus is known.  Reading that
    transcript from disk avoids a race where a just-loaded in-memory Session is
    visible to the API before its history has been hydrated.  The existing
    formatter remains authoritative for timeline shape, tool traces,
    attachments and visualization placement.
    """
    if session_name:
        saved_session = _saved_history_session(session_name)
        if saved_session is not None:
            payload = await sessions_router.get_history(
                _request_for_saved_session(saved_session),
                session_name=session_name,
                limit_turns=limit_turns,
                artifact_limit=artifact_limit,
                before_index=before_index,
            )
            payload["history_source"] = "durable_session"
            return payload

    payload = await sessions_router.get_history(
        request,
        session_name=session_name,
        limit_turns=limit_turns,
        artifact_limit=artifact_limit,
        before_index=before_index,
    )
    payload["history_source"] = "live_session"
    return payload
