"""Container entrypoint: runs the ordinary MuCLI agent loop inside Docker."""
from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
import threading
import traceback
import uuid
from contextlib import AbstractContextManager
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

from mu.ui.base import BaseUI


logger = logging.getLogger("mucli.container.worker")


class _Status(AbstractContextManager):
    def __init__(self, ui: "WorkerBridgeUI", text: str):
        self.ui = ui
        self.text = text

    def __enter__(self):
        self.ui.publish({"kind": "status_start", "text": self.text})
        return self

    def __exit__(self, *_args):
        self.ui.publish({"kind": "status_end", "text": self.text})
        return False


class WorkerBridgeUI(BaseUI):
    """BaseUI implementation that forwards events to the host supervisor."""

    def __init__(self, session_name: str):
        self.session_name = session_name
        self.container_name = os.getenv("MUCLI_CONTAINER_NAME", "")
        self.supervisor_url = os.getenv("MUCLI_SUPERVISOR_URL", "").rstrip("/")
        self.token = os.getenv("MUCLI_WORKER_TOKEN", "")
        self.variables: dict[str, Any] = {}
        self.turn_id: str | None = None
        self._client = httpx.Client(timeout=10.0)

    def publish(self, event: dict[str, Any]) -> None:
        if not self.supervisor_url:
            return
        payload = {
            **event,
            "session_name": self.session_name,
            "container_name": self.container_name,
        }
        try:
            self._client.post(
                f"{self.supervisor_url}/api/container-worker/events",
                json=payload,
                headers={"X-MuCLI-Worker-Token": self.token},
            ).raise_for_status()
        except Exception:
            # A temporary GUI disconnect must not abort an agent turn.  Session
            # history remains authoritative and will be recovered on reconnect.
            pass

    def render_message(self, role, content, model_name=None):
        text = str(content or "")
        if role == "assistant":
            turn_id = uuid.uuid4().hex[:12]
            self.publish({"kind": "assistant_start", "turn_id": turn_id})
            self.publish({"kind": "assistant_delta", "turn_id": turn_id, "text": text})
            self.publish({"kind": "assistant_end", "turn_id": turn_id})
        elif role == "user":
            self.publish({"kind": "user_message", "text": text})
        else:
            self.publish({"kind": "info", "text": text, "role": role, "model": model_name})

    def get_input(self, *_args, **_kwargs):
        return ""

    def show_error(self, message):
        self.publish({"kind": "error", "text": str(message)})

    def show_info(self, message):
        self.publish({"kind": "info", "text": str(message)})

    def show_status(self, message):
        return _Status(self, str(message))

    def show_tool_result(self, result_str):
        self.publish({"kind": "tool_result", "text": str(result_str)})

    def stream_assistant_delta(self, text: str):
        if not text:
            return
        if self.turn_id is None:
            self.turn_id = uuid.uuid4().hex[:12]
            self.publish({"kind": "assistant_start", "turn_id": self.turn_id})
        self.publish({"kind": "assistant_delta", "turn_id": self.turn_id, "text": text})

    def stream_thinking_delta(self, text: str):
        if text:
            self.publish({"kind": "thinking_delta", "turn_id": self.turn_id, "text": text})

    def stream_tool_call(self, tool_name: str):
        self.publish({"kind": "tool_call", "turn_id": self.turn_id, "tool_name": tool_name})

    def stream_assistant_end(self):
        if self.turn_id is not None:
            self.publish({"kind": "assistant_end", "turn_id": self.turn_id})
            self.turn_id = None

    def set_variables(self, variables_dict):
        self.variables = dict(variables_dict or {})

    # Container sessions auto-approve modifying tools by design.
    def request_tool_approval(self, *_args, **_kwargs):
        return {"approved": True, "remember": True}

    def _ask_prompt(self, prompt: dict[str, Any], timeout: float = 600.0) -> Any:
        if not self.supervisor_url:
            return {"cancelled": True}
        payload = {
            "container_name": self.container_name,
            "session_name": self.session_name,
            "prompt": prompt,
            "timeout": timeout,
        }
        try:
            response = self._client.post(
                f"{self.supervisor_url}/api/container-worker/prompt",
                json=payload,
                headers={"X-MuCLI-Worker-Token": self.token},
                timeout=timeout + 15.0,
            )
            response.raise_for_status()
            value = response.json()
            return value.get("answer") if isinstance(value, dict) else {"cancelled": True}
        except Exception:
            return {"cancelled": True}

    def prompt(self, message, default=None):
        result = self._ask_prompt({
            "shape": "input",
            "message": str(message),
            "default": "" if default is None else str(default),
        })
        if isinstance(result, dict) and not result.get("cancelled"):
            return result.get("value", default)
        return default

    def confirm(self, message, default=True):
        result = self._ask_prompt({
            "shape": "confirm",
            "message": str(message),
            "default": bool(default),
        })
        if isinstance(result, dict) and not result.get("cancelled") and "value" in result:
            return bool(result["value"])
        return bool(default)

    def prompt_choices(self, message, choices, default=None):
        result = self._ask_prompt({
            "shape": "choices",
            "message": str(message),
            "choices": list(choices),
            "default": default,
        })
        if isinstance(result, dict) and not result.get("cancelled"):
            return result.get("value", default)
        return default

    def ask_user_choice(self, question, options, *, multi_select=False, description="", allow_other=False):
        result = self._ask_prompt({
            "shape": "choice",
            "question": str(question),
            "options": list(options),
            "multi_select": bool(multi_select),
            "description": str(description or ""),
            "allow_other": bool(allow_other),
        })
        if isinstance(result, dict) and not result.get("cancelled"):
            return {
                "selected": list(result.get("selected") or []),
                "other_text": str(result.get("other_text") or ""),
                "cancelled": False,
            }
        return {"selected": [], "other_text": "", "cancelled": True}

    def run_quiz(self, questions):
        result = self._ask_prompt({"shape": "quiz", "questions": list(questions or [])})
        if isinstance(result, dict) and not result.get("cancelled"):
            return dict(result.get("answers") or {})
        return {}

    def show_diff(self, filename, original_content, new_content):
        self.publish(
            {
                "kind": "diff",
                "filename": str(filename),
                "original": str(original_content or ""),
                "new": str(new_content or ""),
            }
        )


class SendRequest(BaseModel):
    session_name: str
    text: str
    provider: str
    model: str
    agent_mode: str = "default"
    system_instruction: str = "You are a helpful assistant."


class InterruptRequest(BaseModel):
    session_name: str


app = FastAPI(title="MuCLI container worker", docs_url=None, redoc_url=None)
_sessions: dict[str, Any] = {}
_locks: dict[str, threading.Lock] = {}
_busy: dict[str, threading.Event] = {}
_threads: dict[str, int] = {}


def _authorize(token: str | None) -> None:
    expected = os.getenv("MUCLI_WORKER_TOKEN", "")
    if not expected or not token or not __import__("hmac").compare_digest(expected, token):
        raise HTTPException(status_code=401, detail="invalid worker token")


def _build_session(request: SendRequest):
    existing = _sessions.get(request.session_name)
    if existing is not None:
        return existing
    from mucli import build_session

    ui = WorkerBridgeUI(request.session_name)
    try:
        configured_workspaces = json.loads(os.getenv("MUCLI_WORKSPACES", "[\"/workspace\"]"))
    except (TypeError, ValueError):
        configured_workspaces = ["/workspace"]
    workspaces = [
        str(path) for path in configured_workspaces
        if isinstance(path, str) and os.path.isdir(path)
    ] or ["/workspace"]
    args = argparse.Namespace(
        session=request.session_name,
        provider=request.provider,
        model=request.model,
        provider_prevalidated=True,
        session_type="container",
        system=request.system_instruction,
        debug=False,
        workspace=workspaces,
        yolo=True,
        system_file=None,
        mode_prompt=[],
    )
    session = build_session(args, ui, allow_prompt=False)
    session.variables["session_type"] = "container"
    session.variables["agent_mode"] = request.agent_mode or "default"
    session.variables["yolo"] = True
    session.variables["strict_mode"] = False
    session.session_manager.save_history(session.folder_context)
    session.sync_runtime_state()
    _sessions[request.session_name] = session
    _locks[request.session_name] = threading.Lock()
    _busy[request.session_name] = threading.Event()
    return session


def _run_turn(session, request: SendRequest) -> None:
    name = request.session_name
    busy = _busy[name]
    ui = session.ui
    busy.set()
    _threads[name] = threading.current_thread().ident or 0
    try:
        with _locks[name]:
            result = session.send_message(request.text)
            session.session_manager.save_history(session.folder_context)
        ui.publish(
            {
                "kind": "turn_complete",
                "result": {
                    "ok": bool(isinstance(result, dict) and result.get("ok", True)),
                    "status": result.get("status") if isinstance(result, dict) else None,
                    "error": result.get("error") if isinstance(result, dict) else None,
                },
            }
        )
    except KeyboardInterrupt:
        ui.publish({"kind": "turn_complete", "result": {"ok": False, "status": "interrupted"}})
    except Exception as exc:
        ui.publish({"kind": "error", "text": f"container turn failed: {exc}"})
        ui.publish({"kind": "turn_complete", "result": {"ok": False, "status": "error", "error": str(exc)}})
    finally:
        busy.clear()
        _threads.pop(name, None)


@app.get("/health")
def health(x_mucli_worker_token: str | None = Header(default=None)):
    _authorize(x_mucli_worker_token)
    return {
        "ok": True,
        "container_name": os.getenv("MUCLI_CONTAINER_NAME", ""),
        "sessions": sorted(_sessions),
        "busy": sorted(name for name, event in _busy.items() if event.is_set()),
    }


def _assistant_text_since(session, start_index: int) -> str:
    history = getattr(session.session_manager, "history", []) or []
    for message in reversed(history[start_index:]):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        chunks = [
            str(part.get("text") or "")
            for part in (message.get("parts") or [])
            if isinstance(part, dict) and part.get("type") == "text" and part.get("text")
        ]
        if chunks:
            return "".join(chunks)
    return ""


@app.post("/send-sync")
def send_sync(request: SendRequest, x_mucli_worker_token: str | None = Header(default=None)):
    _authorize(x_mucli_worker_token)
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="text is required")
    try:
        session = _build_session(request)
    except Exception as exc:
        logger.exception("failed to initialise container session %s", request.session_name)
        raise HTTPException(
            status_code=500,
            detail=f"worker session initialisation failed: {type(exc).__name__}: {exc}",
        ) from exc
    if _busy[request.session_name].is_set():
        raise HTTPException(status_code=409, detail="session already has a turn in flight")
    name = request.session_name
    busy = _busy[name]
    busy.set()
    _threads[name] = threading.current_thread().ident or 0
    start_index = len(session.session_manager.history)
    try:
        with _locks[name]:
            result = session.send_message(request.text)
            session.session_manager.save_history(session.folder_context)
        return jsonable_encoder({
            "ok": bool(not isinstance(result, dict) or result.get("status") != "error"),
            "session_name": name,
            "assistant_text": _assistant_text_since(session, start_index),
            "result": result if isinstance(result, dict) else {"status": "complete"},
        })
    except KeyboardInterrupt:
        return {"ok": False, "session_name": name, "assistant_text": "", "result": {"status": "interrupted"}}
    except Exception as exc:
        logger.exception("container turn failed for %s", request.session_name)
        detail = f"worker turn failed: {type(exc).__name__}: {exc}"
        logger.debug("worker traceback:\n%s", traceback.format_exc())
        raise HTTPException(status_code=500, detail=detail) from exc
    finally:
        busy.clear()
        _threads.pop(name, None)


@app.post("/send")
def send(request: SendRequest, x_mucli_worker_token: str | None = Header(default=None)):
    _authorize(x_mucli_worker_token)
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="text is required")
    try:
        session = _build_session(request)
    except Exception as exc:
        logger.exception("failed to initialise container session %s", request.session_name)
        raise HTTPException(
            status_code=500,
            detail=f"worker session initialisation failed: {type(exc).__name__}: {exc}",
        ) from exc
    if _busy[request.session_name].is_set():
        raise HTTPException(status_code=409, detail="session already has a turn in flight")
    thread = threading.Thread(target=_run_turn, args=(session, request), daemon=True)
    thread.start()
    return {"accepted": True, "session_name": request.session_name}


@app.post("/interrupt")
def interrupt(request: InterruptRequest, x_mucli_worker_token: str | None = Header(default=None)):
    _authorize(x_mucli_worker_token)
    thread_id = _threads.get(request.session_name)
    if not thread_id:
        return {"ok": False, "detail": "No turn in flight."}
    result = ctypes.pythonapi.PyThreadState_SetAsyncExc(
        ctypes.c_ulong(thread_id), ctypes.py_object(KeyboardInterrupt)
    )
    return {"ok": result == 1}


def main() -> None:
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("MUCLI_WORKER_PORT", "30312")),
        log_level=os.getenv("MUCLI_WORKER_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
