"""FastAPI factory for the GUI server.

Multi-session model: ``app.state.sessions`` holds every loaded Session
keyed by name. Each session gets its own ``threading.Lock``,
``threading.Event`` (busy), and ``WebUI`` bridge — so two sessions can
have turns in flight simultaneously. ``app.state.current_session_name``
tracks which one the user is *focused* on (purely a UI hint; chat
sends explicitly name their session).

Backward-compat shim: ``app.state.session`` (and ``session_lock`` /
``busy``) remain as Python ``@property``-like accessors via
``__getattr__`` on a tiny holder, returning the current session's
view. Existing code paths that didn't know about multi-session keep
working against whichever session is focused.
"""

from __future__ import annotations

import asyncio
import copy
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.types import Scope

from .bus import EventBus
from .deps import require_session  # re-exported
from .prompts import PromptStore
from .routers import (
    chat,
    inspector,
    modes,
    prompts as prompts_router,
    providers as providers_router,
    sessions,
    teacher as teacher_router,
)
from .watcher import SessionWatcher
from .web_ui import WebUI

GUI_ROOT = Path(__file__).parent
TEMPLATES_DIR = GUI_ROOT / "templates"
STATIC_DIR = GUI_ROOT / "static"

__all__ = ["create_app", "require_session"]


class _NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        return response


# ---------------------------------------------------------------------------
# Session resolution helpers (shared by routers + watcher)


def session_by_name(app: FastAPI, name: Optional[str]):
    """Return the loaded Session for `name`, or the focused session
    when `name` is None/empty, or None if nothing matches."""
    sessions: Dict[str, Any] = app.state.sessions
    if name:
        return sessions.get(name)
    cur = app.state.current_session_name
    return sessions.get(cur) if cur else None


def session_lock_for(app: FastAPI, name: Optional[str]) -> threading.Lock:
    sessions = app.state.sessions
    target = name or app.state.current_session_name
    if target is None:
        # No session yet — return a dummy lock so callers don't blow up.
        return app.state._fallback_lock
    locks: Dict[str, threading.Lock] = app.state.session_locks
    return locks.setdefault(target, threading.Lock())


def session_busy_for(app: FastAPI, name: Optional[str]) -> threading.Event:
    target = name or app.state.current_session_name
    if target is None:
        return app.state._fallback_busy
    busys: Dict[str, threading.Event] = app.state.session_busy
    return busys.setdefault(target, threading.Event())


def web_ui_for(app: FastAPI, name: Optional[str]) -> Optional[WebUI]:
    target = name or app.state.current_session_name
    if target is None:
        return None
    uis: Dict[str, WebUI] = app.state.web_uis
    return uis.get(target)


# ---------------------------------------------------------------------------


def create_app(
    *,
    args: Any,
    build_session_fn: Callable,
    port: int = 30311,
) -> FastAPI:
    app = FastAPI(title="mucli", version="1.0", docs_url=None, redoc_url=None)

    bus = EventBus()
    prompts = PromptStore()

    # ---- multi-session state ------------------------------------------
    app.state.sessions: Dict[str, Any] = {}
    app.state.session_locks: Dict[str, threading.Lock] = {}
    app.state.session_busy: Dict[str, threading.Event] = {}
    app.state.web_uis: Dict[str, WebUI] = {}
    app.state.current_session_name: Optional[str] = None
    # Fallbacks used when no session is active (so routers don't blow up).
    app.state._fallback_lock = threading.Lock()
    app.state._fallback_busy = threading.Event()

    # ---- shared infra -------------------------------------------------
    app.state.bus = bus
    app.state.prompts = prompts
    app.state.port = port
    app.state.args = args
    app.state.build_session_fn = build_session_fn
    app.state.load_session = lambda **kw: _load_session(app, **kw)
    app.state.unload_session = lambda **kw: _unload_session(app, **kw)
    app.state.watcher = SessionWatcher(app)

    # Resolver helpers exposed on app.state so routers don't have to
    # import the module-level functions.
    app.state.session_by_name = lambda name=None: session_by_name(app, name)
    app.state.session_lock_for = lambda name=None: session_lock_for(app, name)
    app.state.session_busy_for = lambda name=None: session_busy_for(app, name)
    app.state.web_ui_for = lambda name=None: web_ui_for(app, name)

    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    app.state.templates = templates

    app.mount("/static", _NoCacheStaticFiles(directory=str(STATIC_DIR)), name="static")

    app.include_router(sessions.router, prefix="/api/sessions", tags=["sessions"])
    app.include_router(providers_router.router, prefix="/api/providers", tags=["providers"])
    app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
    app.include_router(modes.router, prefix="/api/modes", tags=["modes"])
    app.include_router(prompts_router.router, prefix="/api/prompts", tags=["prompts"])
    app.include_router(inspector.router, prefix="/api", tags=["inspector"])
    app.include_router(teacher_router.router, prefix="/api/teacher", tags=["teacher"])
    app.include_router(chat.events_router, tags=["events"])

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        session = session_by_name(app, None)
        sm = session.session_manager if session else None
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "session_name": sm.current_session_name if sm else "",
                "agent_mode": session.variables.get("agent_mode", "default") if session else "default",
                "provider": session.provider.name if session and session.provider else "",
                "model": session.provider.model_name if session and session.provider else "",
                "session_active": session is not None,
            },
        )

    @app.get("/healthz")
    async def healthz():
        return {
            "ok": True,
            "session_active": app.state.current_session_name is not None,
            "loaded_sessions": list(app.state.sessions.keys()),
        }

    @app.on_event("startup")
    async def _bind_loop():
        bus.bind_loop(asyncio.get_running_loop())
        app.state.watcher.start()

    @app.on_event("shutdown")
    async def _stop_watcher():
        app.state.watcher.stop()

    return app


def _load_session(
    app: FastAPI,
    *,
    name: str,
    provider: Optional[str] = None,
    model: Optional[str] = None,
):
    """Build & install a Session into ``app.state.sessions[name]`` and
    focus on it. Idempotent: a session already loaded with the same name
    just gets focused (no rebuild)."""
    if name in app.state.sessions:
        app.state.current_session_name = name
        return app.state.sessions[name]

    # Each session gets its own WebUI bridge so events are attributable.
    bus = app.state.bus
    prompts = app.state.prompts
    web_ui = WebUI(bus, prompts, session_name=name)
    app.state.web_uis[name] = web_ui

    args = copy.copy(app.state.args)
    args.session = name
    if provider is not None:
        args.provider = provider
    if model is not None:
        args.model = model
    session = app.state.build_session_fn(args, web_ui, allow_prompt=False)
    session.ui = web_ui
    session.session_manager.ui = web_ui

    app.state.sessions[name] = session
    app.state.session_locks.setdefault(name, threading.Lock())
    app.state.session_busy.setdefault(name, threading.Event())
    app.state.current_session_name = name
    return session


def _unload_session(
    app: FastAPI,
    *,
    name: Optional[str] = None,
) -> bool:
    """Drop a session from the in-memory cache. If `name` is None, the
    focused session is unloaded. Returns True if something was unloaded.
    The session's data on disk is untouched."""
    target = name or app.state.current_session_name
    if not target or target not in app.state.sessions:
        return False
    session = app.state.sessions.pop(target)
    app.state.session_locks.pop(target, None)
    app.state.session_busy.pop(target, None)
    app.state.web_uis.pop(target, None)
    try:
        session.session_manager.save_history(session.folder_context)
    except Exception:
        pass
    if app.state.current_session_name == target:
        # Focus falls back to whichever session is still resident, or None.
        remaining = list(app.state.sessions.keys())
        app.state.current_session_name = remaining[-1] if remaining else None
    return True
