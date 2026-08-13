"""Explicit session resolution shared by all Mode OS routers."""

from __future__ import annotations

from typing import Any

from fastapi import Request


def mode_session(request: Request) -> Any:
    """Resolve the named session, falling back to the daemon focus."""
    query = getattr(request, "query_params", None) or {}
    name = str(query.get("session_name") or "").strip() or None
    resolver = request.app.state.session_by_name
    try:
        return resolver(name) if name else resolver()
    except TypeError:
        # Lightweight unit-test/third-party app shims may still expose the
        # original no-argument callable.
        return resolver()


def mode_session_lock(request: Request, session: Any = None):
    """Return the lock for the same explicit session used by the router."""
    query = getattr(request, "query_params", None) or {}
    name = str(query.get("session_name") or "").strip()
    if not name and session is not None:
        name = str(
            getattr(
                getattr(session, "session_manager", None),
                "current_session_name",
                "",
            )
            or ""
        )
    resolver = request.app.state.session_lock_for
    try:
        return resolver(name) if name else resolver()
    except TypeError:
        return resolver()


__all__ = ["mode_session", "mode_session_lock"]
