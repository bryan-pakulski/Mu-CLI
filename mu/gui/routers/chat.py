"""Chat send + SSE event stream.

Multi-session: each chat send names the target session (default: the
currently focused one). Lock and busy event are per-session so two
sessions can run turns in parallel without blocking each other.
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

router = APIRouter()
events_router = APIRouter()


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
):
    busy.set()
    try:
        with lock:
            try:
                result = session.send_message(text)
            except Exception as exc:
                result = {"status": "error", "error": str(exc)}
            try:
                session.session_manager.save_history(session.folder_context)
            except Exception:
                pass
            return result
    finally:
        busy.clear()


@router.post("/send")
async def send_message(request: Request, payload: Dict[str, Any]):
    session_name = (payload.get("session_name") or "").strip() or None
    session = _resolve_session(request, session_name)
    name = session.session_manager.current_session_name

    busy = request.app.state.session_busy_for(name)
    if busy.is_set():
        raise HTTPException(
            status_code=409,
            detail=f"Session {name!r} already has a turn in flight.",
        )

    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

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

    lock = request.app.state.session_lock_for(name)

    def _run():
        return _run_send(session, text, lock=lock, busy=busy)

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
    interrupted = False
    for attr in ("interrupt", "request_interrupt", "cancel_current_turn"):
        method = getattr(session, attr, None)
        if callable(method):
            try:
                method()
                interrupted = True
                break
            except Exception:
                pass
    return {"ok": interrupted}


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
            yield {"event": "message", "data": json.dumps({"kind": "hello"})}
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
