"""Chat send + SSE event stream.

Multi-session: each chat send names the target session (default: the
currently focused one). Lock and busy event are per-session so two
sessions can run turns in parallel without blocking each other.
"""

from __future__ import annotations

import asyncio
import ctypes
import json
import threading
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

router = APIRouter()
events_router = APIRouter()

_agent_threads: Dict[str, int] = {}


def _resolve_session(request: Request, name: Optional[str]):
    """Resolve a session by name or fall back to the focused one.

    Returns the Session object or raises 412.
    """
    session = request.app.state.session_by_name(name)
    if session is None:
        raise HTTPException(
            status_code=412,
            detail=(
                f"Session {name!r} is not loaded."
                if name
                else "No session loaded. Load or create a session first."
            ),
        )
    return session


def _run_send(
    session,
    text: str,
    *,
    lock: threading.Lock,
    busy: threading.Event,
    session_name: str = "",
):
    _agent_threads[session_name] = threading.current_thread().ident
    busy.set()
    try:
        with lock:
            try:
                result = session.send_message(text)
            except KeyboardInterrupt:
                result = {"status": "interrupted", "error": "User interrupted execution."}
            except Exception as exc:
                result = {"status": "error", "error": str(exc)}
            try:
                session.session_manager.save_history(session.folder_context)
            except Exception:
                pass
            return result
    finally:
        busy.clear()
        _agent_threads.pop(session_name, None)


@router.get("/commands")
async def list_commands_endpoint():
    from mu.commands import list_commands

    specs = list_commands()
    return {
        "commands": [
            {"names": list(s.names), "help": s.help}
            for s in specs
        ]
    }


@router.get("/completions")
async def completions_endpoint(request: Request, kind: str = ""):
    """Return dynamic completion lists for subcommand arguments.

    Query param ``kind`` selects which list to return:
      sessions, features, tools, models, modes, variables, skills, docs
    """
    if kind == "sessions":
        import glob as _glob
        import os

        from utils.config import HISTORY_DIR

        sessions = []
        pattern = os.path.join(HISTORY_DIR, "sessions", "*", "session.json")
        for path in _glob.glob(pattern):
            sessions.append(os.path.basename(os.path.dirname(path)))
        return {"items": sorted(set(sessions))}

    if kind == "features":
        import glob as _glob
        import os

        from utils.config import HISTORY_DIR

        ids: set = set()
        for path in _glob.glob(
            os.path.join(HISTORY_DIR, "sessions", "*", "features", "*.json")
        ):
            try:
                import json as _json

                with open(path, "r", encoding="utf-8") as fh:
                    fid = str(_json.load(fh).get("feature_id", "")).strip()
                    if fid:
                        ids.add(fid)
            except Exception:
                continue
        return {"items": sorted(ids)}

    if kind == "tools":
        try:
            from mu.tools.descriptors import TOOLS

            names = sorted({t.name for t in TOOLS if getattr(t, "name", "")})
        except Exception:
            names = []
        return {"items": names}

    if kind == "models":
        try:
            from utils.config import KNOWN_MODELS

            return {"items": list(KNOWN_MODELS)}
        except Exception:
            return {"items": []}

    if kind == "modes":
        try:
            from utils.config import AGENT_MODE_METADATA

            return {"items": sorted(AGENT_MODE_METADATA.keys())}
        except Exception:
            return {"items": ["default"]}

    if kind == "variables":
        session = request.app.state.session_by_name()
        if session is None:
            return {"items": []}
        return {"items": sorted(session.variables.keys())}

    if kind == "skills":
        try:
            from mu.skills import discover_skills

            names = sorted({s.name for s in discover_skills([])})
        except Exception:
            names = []
        return {"items": names}

    if kind == "docs":
        try:
            from mu.commands.docs import list_doc_names

            return {"items": list_doc_names()}
        except Exception:
            return {"items": []}

    if kind == "memory_targets":
        try:
            from mu.commands.memory import LIST_TARGETS

            return {"items": list(LIST_TARGETS)}
        except Exception:
            return {"items": ["all", "task", "scratchpad",
                              "L1", "L1B", "L2", "L3", "L4", "L4B", "L5"]}

    if kind == "layer_ids":
        try:
            from mu.commands.variables import LAYER_BUDGET_VARS

            return {"items": list(LAYER_BUDGET_VARS.keys())}
        except Exception:
            return {"items": ["L1", "L1B", "L2", "L3", "L4", "L4B"]}

    return {"items": []}


@router.post("/send")
async def send_message(request: Request, payload: Dict[str, Any]):
    session_name = (payload.get("session_name") or "").strip() or None
    session = _resolve_session(request, session_name)
    name = session.session_manager.current_session_name

    busy = request.app.state.session_busy_for(name)
    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    # Commands are deliberately permitted while the model works: they are
    # operational controls (for example /status or /interrupt), whereas a
    # second natural-language turn would race the active agent loop.
    if busy.is_set() and not text.startswith("/"):
        raise HTTPException(
            status_code=409,
            detail=f"Session {name!r} already has a turn in flight.",
        )

    bus = request.app.state.bus
    # Echo the user's message to the per-session stream so the browser
    # can render it immediately without waiting for the agent loop.
    await bus.publish(
        {"kind": "user_message", "text": text, "session_name": name}
    )

    if text.startswith("/"):
        from mucli import handle_command

        lock = request.app.state.session_lock_for(name)

        def _run_cmd():
            with lock:
                return handle_command(session, text, allow_prompt=False)

        result = await asyncio.to_thread(_run_cmd)
        await bus.publish(
            {"kind": "command_result", "result": result, "session_name": name}
        )
        return {"accepted": True, "kind": "command", "session_name": name}

    session_type = str(session.variables.get("session_type", "workspace") or "workspace").lower()
    if session_type == "container":
        busy.set()

        async def _drive_container() -> None:
            try:
                response = await asyncio.to_thread(
                    request.app.state.container_supervisor.send_sync,
                    name,
                    text,
                    provider=session.provider.name,
                    model=session.provider.model_name,
                    agent_mode=str(session.variables.get("agent_mode", "default")),
                    system_instruction=session.system_instruction,
                    timeout=None,
                )
                result = response.get("result") if isinstance(response, dict) else None
                # The worker writes through the mounted session directory.
                # Refresh the host mirror before notifying panels/history.
                try:
                    session.session_manager._load_session(name)
                    session.sync_runtime_state()
                except Exception:
                    pass
                if isinstance(result, dict) and result.get("status") == "error":
                    await bus.publish(
                        {
                            "kind": "error",
                            "text": str(result.get("error") or "Container turn failed."),
                            "session_name": name,
                        }
                    )
                await bus.publish(
                    {
                        "kind": "turn_complete",
                        "result": _summarize_result(result),
                        "session_name": name,
                    }
                )
                await bus.publish(
                    {"kind": "history_refresh", "session_name": name}
                )
            except Exception as exc:
                await bus.publish(
                    {
                        "kind": "error",
                        "text": f"container send failed: {exc}",
                        "session_name": name,
                    }
                )
            finally:
                busy.clear()

        asyncio.create_task(_drive_container())
        return {"accepted": True, "kind": "container", "session_name": name}

    lock = request.app.state.session_lock_for(name)

    def _run():
        return _run_send(session, text, lock=lock, busy=busy, session_name=name)

    async def _drive():
        try:
            result = await asyncio.to_thread(_run)
            await bus.publish(
                {
                    "kind": "turn_complete",
                    "result": _summarize_result(result),
                    "session_name": name,
                }
            )
        except Exception as exc:
            await bus.publish(
                {"kind": "error", "text": f"send failed: {exc}", "session_name": name}
            )

    asyncio.create_task(_drive())
    return {"accepted": True, "kind": "chat", "session_name": name}


@router.post("/interrupt")
async def interrupt(request: Request, payload: Optional[Dict[str, Any]] = None):
    session_name = None
    if payload:
        session_name = (payload.get("session_name") or "").strip() or None
    session = _resolve_session(request, session_name)
    name = session.session_manager.current_session_name

    session_type = str(session.variables.get("session_type", "workspace") or "workspace").lower()
    if session_type == "container":
        try:
            result = await asyncio.to_thread(
                request.app.state.container_supervisor.interrupt, name
            )
            return result
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    tid = _agent_threads.get(name)
    if tid is None:
        return {"ok": False, "detail": "No turn in flight for this session."}

    res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
        ctypes.c_ulong(tid), ctypes.py_object(KeyboardInterrupt)
    )
    return {"ok": res == 1}


@router.get("/history/search")
async def search_history(
    request: Request,
    query: str = "",
    role: Optional[str] = None,
    tool_name: Optional[str] = None,
    max_results: int = 20,
    session_name: Optional[str] = None,
):
    """Search conversation history for matching messages.

    Read-only endpoint that calls session.search_history() and returns
    JSON results. Accepts query, role, tool_name, max_results params.
    """
    query = (query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")

    session = _resolve_session(request, session_name)
    sm = session.session_manager
    results = sm.search_history(
        query=query,
        role=role or None,
        tool_name=tool_name or None,
        max_results=max_results,
    )
    return results


def _summarize_result(result: Any) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return {"ok": False}
    return {
        "ok": result.get("ok", False),
        "status": result.get("status"),
        "tokens": result.get("session_totals") or result.get("tokens"),
        "error": result.get("error"),
    }


@events_router.get("/api/events")
async def stream_events(request: Request):
    bus = request.app.state.bus
    queue = bus.subscribe()

    async def generator():
        try:
            busy_names = [
                n for n, evt in request.app.state.session_busy.items()
                if evt.is_set()
            ]
            yield {"event": "message", "data": json.dumps({
                "kind": "hello",
                "busy": busy_names,
            })}
            for pending in request.app.state.prompts.pending():
                yield {
                    "event": "message",
                    "data": json.dumps(
                        {
                            "kind": "prompt",
                            "id": pending["id"],
                            "prompt": pending,
                            "session_name": pending.get("session_name"),
                        }
                    ),
                }
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": "{}"}
                    continue
                yield {"event": "message", "data": json.dumps(event)}
        finally:
            bus.unsubscribe(queue)

    return EventSourceResponse(generator())
