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
import logging
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional

_logger = logging.getLogger(__name__)

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.types import Scope

from .bus import EventBus
from .deps import require_session  # re-exported
from .memory_snapshot import LIVE_RESOLUTION, build_memory_snapshot
from .prompts import PromptStore
from .routers import (
    audio as audio_router,
    chat,
    debug as debug_router,
    feature as feature_router,
    files as files_router,
    inspector,
    loop as loop_router,
    memory as memory_router,
    modes,
    prompts as prompts_router,
    providers as providers_router,
    research as research_router,
    security as security_router,
    sessions,
    skills as skills_router,
    system_prompts as system_prompts_router,
    teacher as teacher_router,
    traces as traces_router,
)
from .watcher import SessionWatcher
from .web_ui import WebUI

# Hook registry + context for the live memory-snapshot push. Imported
# lazily-safe at module load (mu.agent.hooks has no heavy deps).
from mu.agent.hooks import HookContext, default_registry

_MEMORY_HOOK_NAME = "gui_memory_snapshot"
_SUBAGENT_HOOK_NAME = "gui_subagent_snapshot"


def _register_memory_snapshot_hook() -> None:
    """Register a ``pre_provider_call`` hook that pushes a live context
    snapshot to the GUI per iteration so the Memory Map panel updates in
    real time while a turn runs.

    Idempotent: no-ops if the hook is already registered (tests may call
    ``create_app`` more than once). The handler skips any session whose
    UI isn't a :class:`WebUI`, so CLI runs are unaffected. Fires on the
    agent thread, where ``HookContext`` already carries the fully
    assembled system prompt + messages about to go to the provider.
    """
    if any(spec.name == _MEMORY_HOOK_NAME for spec in default_registry.list("pre_provider_call")):
        return

    def _snapshot(ctx: HookContext):
        ui = getattr(ctx.session, "ui", None)
        if not isinstance(ui, WebUI):
            return None
        try:
            # Keep the Memory Map's headline total on the exact same
            # pre-request estimate that the trace records for this iteration.
            # Layer estimates omit message framing and transient prompt text.
            # Include tool schemas — the trace's request_token_estimate adds
            # `_estimate_tools_tokens(tools)`, so the Memory Map must too or
            # its headline undercounts the trace by the whole tool-schema
            # cost (thousands of tokens in agentic mode).
            from mu.agent.loop_body import (
                _estimate_messages_tokens,
                _estimate_tools_tokens,
            )
            from utils.token_estimator import estimate_tokens

            request_tokens = (
                estimate_tokens(ctx.system_prompt or "")
                + _estimate_messages_tokens(ctx.messages or [])
                + _estimate_tools_tokens(ctx.tools or [])
            )
            ctx.session._memory_map_request_token_estimate = int(request_tokens)
            snap = build_memory_snapshot(
                ctx.session,
                cols=LIVE_RESOLUTION,
                rows=LIVE_RESOLUTION,
                request_token_estimate=request_tokens,
            )
        except Exception as exc:  # defensive — must never break a turn
            _logger.warning("memory snapshot hook failed: %s", exc)
            return None
        ui._publish({"kind": "context_snapshot", **snap})
        return None

    default_registry.register("pre_provider_call", name=_MEMORY_HOOK_NAME)(_snapshot)


def _register_subagent_snapshot_hook() -> None:
    """Register a ``pre_provider_call`` hook that pushes a live sub-agent
    snapshot to the GUI each parent iteration while sub-agents are running,
    so the chat-feed status panel reconciles progress / context / tokens
    even if a granular ``subagent_progress`` event was missed.

    Idempotent. Skips non-WebUI sessions (CLI/TUI unaffected) and sessions
    with no active sub-agent registry. Fires on the parent agent thread.
    """
    if any(spec.name == _SUBAGENT_HOOK_NAME for spec in default_registry.list("pre_provider_call")):
        return

    def _snapshot(ctx: HookContext):
        ui = getattr(ctx.session, "ui", None)
        if not isinstance(ui, WebUI):
            return None
        registry = getattr(ctx.session, "_subagent_registry", None)
        if registry is None:
            return None
        try:
            children = registry.snapshot_all()
        except Exception as exc:  # defensive — must never break a turn
            _logger.warning("subagent snapshot hook failed: %s", exc)
            return None
        if not children:
            return None
        active = sum(1 for c in children if c.get("status") == "running")
        stuck = sum(1 for c in children if c.get("stuck"))
        stall = sum(1 for c in children if c.get("stall"))
        ui._publish(
            {
                "kind": "subagent_snapshot",
                "children": children,
                "active": active,
                "stuck": stuck,
                "stall": stall,
            }
        )
        return None

    default_registry.register("pre_provider_call", name=_SUBAGENT_HOOK_NAME)(_snapshot)

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
    # Ensure Jinja2 auto-reloads templates when files change on disk
    # (prevents stale template serving during development).
    templates.env.auto_reload = True
    app.state.templates = templates

    app.mount("/static", _NoCacheStaticFiles(directory=str(STATIC_DIR)), name="static")

    app.include_router(sessions.router, prefix="/api/sessions", tags=["sessions"])
    app.include_router(providers_router.router, prefix="/api/providers", tags=["providers"])
    app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
    app.include_router(modes.router, prefix="/api/modes", tags=["modes"])
    app.include_router(prompts_router.router, prefix="/api/prompts", tags=["prompts"])
    app.include_router(
        system_prompts_router.router,
        prefix="/api/system-prompts",
        tags=["system-prompts"],
    )
    app.include_router(inspector.router, prefix="/api", tags=["inspector"])
    app.include_router(teacher_router.router, prefix="/api/teacher", tags=["teacher"])
    app.include_router(feature_router.router, prefix="/api/feature", tags=["feature"])
    app.include_router(research_router.router, prefix="/api/research", tags=["research"])
    app.include_router(security_router.router, prefix="/api/security", tags=["security"])
    app.include_router(loop_router.router, prefix="/api/loop", tags=["loop"])
    app.include_router(debug_router.router, prefix="/api/debug", tags=["debug"])
    app.include_router(memory_router.router, prefix="/api/memory", tags=["memory"])
    app.include_router(files_router.router, prefix="/api/files", tags=["files"])
    app.include_router(skills_router.router, prefix="/api/skills", tags=["skills"])
    app.include_router(audio_router.router, prefix="/api/audio", tags=["audio"])
    app.include_router(
        traces_router.router, prefix="/api/traces", tags=["traces"]
    )
    app.include_router(chat.events_router, tags=["events"])

    # Live Memory Map: push a context snapshot per provider iteration so
    # the panel updates in real time while a turn runs. Idempotent.
    _register_memory_snapshot_hook()
    # Live sub-agent status: push a registry snapshot per parent iteration
    # while sub-agents run, so the chat-feed panel reconciles state. Idempotent.
    _register_subagent_snapshot_hook()

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

    @app.get("/trace", response_class=HTMLResponse)
    async def trace_analyzer(request: Request):
        # Full-page Trace Analyzer dashboard (separate from the chat SPA).
        # Renders its own layout + Alpine store; data is fetched from
        # /api/traces/* by trace.js.
        return templates.TemplateResponse(
            request,
            "trace.html",
            {
                "session_name": "",
                "agent_mode": "default",
                "provider": "",
                "model": "",
                "session_active": False,
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
