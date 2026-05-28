"""FastAPI dependency helpers for GUI routers.

`require_session` accepts an optional `session_name` query parameter so
routers can target a specific session in the multi-session daemon. It
falls back to the focused session when the param is omitted, which
preserves all existing single-session call sites.
"""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Query, Request


def require_session(
    request: Request,
    session_name: Optional[str] = Query(default=None),
):
    """412 when no session matches.

    If ``session_name`` is given, looks it up in ``app.state.sessions``.
    Otherwise returns whichever session is currently focused.
    """
    session = request.app.state.session_by_name(session_name)
    if session is None:
        raise HTTPException(
            status_code=412,
            detail=(
                f"Session {session_name!r} is not loaded."
                if session_name
                else "No session loaded. Load or create a session first."
            ),
        )
    return session
