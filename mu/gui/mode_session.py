"""Explicit session resolution shared by all Mode OS routers."""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Any, Iterator

from fastapi import HTTPException, Request

# Set while a surface edit runs beside an in-flight turn: the turn owns the
# session document and its next save persists the edit, so ``save`` skips.
_TURN_SAVES = contextvars.ContextVar("mode_edit_turn_saves", default=False)


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


def _session_name(request: Request, session: Any) -> str:
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
    return name


def _resolve(resolver, name: str):
    try:
        return resolver(name) if name else resolver()
    except TypeError:
        # Lightweight unit-test/third-party app shims may still expose the
        # original no-argument callable.
        return resolver()


def turn_in_flight(request: Request, session: Any = None) -> bool:
    resolver = getattr(request.app.state, "session_busy_for", None)
    if resolver is None:
        return False
    return _resolve(resolver, _session_name(request, session)).is_set()


@contextmanager
def mode_session_lock(request: Request, session: Any = None, *, exclusive: bool = False) -> Iterator[None]:
    """Serialize a surface edit with the session's turns.

    A turn holds the session lock for its whole run, which can be hours, and
    the user must still be able to pause, resolve a blocker or attest while it
    works. So beside an in-flight turn an edit does not wait: it lands in the
    shared in-memory mode state (each record has its own lock) and the turn's
    next save persists it. ``exclusive`` edits (switching the feature or report
    the agent works on) are refused with 409 instead. Callers are sync
    handlers, so waiting here never stalls the event loop.
    """
    if turn_in_flight(request, session):
        if exclusive:
            raise HTTPException(status_code=409,
                                detail="A turn is running in this session; try again when it finishes.")
        token = _TURN_SAVES.set(True)
        try:
            yield
        finally:
            _TURN_SAVES.reset(token)
        return
    with _resolve(request.app.state.session_lock_for, _session_name(request, session)):
        yield


def turn_saves() -> bool:
    """True inside an edit made beside an in-flight turn (the turn persists it)."""
    return _TURN_SAVES.get()


__all__ = ["mode_session", "mode_session_lock", "turn_in_flight", "turn_saves"]
