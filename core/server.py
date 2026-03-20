import json
import queue
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from core.approval import build_approval_plan
from core.tools import (
    TOOLS,
    get_tool_definition,
    get_tool_descriptor,
    serialize_tool_descriptor,
)
from providers.ollama import OllamaProvider
from utils.logger import logger
from utils.config import validate_and_cast


class HeadlessUI:
    def __init__(self, auto_approve: bool = False):
        self.auto_approve = auto_approve
        self.task_manager = None
        self.approval_manager = None

    def render_message(self, role, content, model_name=None):
        self._publish_trace(
            "trace.message",
            {
                "role": role,
                "content": content,
                "model_name": model_name,
            },
        )
        logger.debug("HeadlessUI render_message role=%s model=%s", role, model_name)

    def get_input(self, session_name, staged_files):
        return ""

    def set_variables(self, variables_dict):
        return None

    def confirm(self, message, default=True):
        return bool(default) if self.auto_approve else False

    def prompt_choices(self, message, choices, default=None):
        if self.auto_approve:
            return default or (choices[0] if choices else "")
        if "n" in choices:
            return "n"
        return default or (choices[0] if choices else "")

    def prompt(self, message, default=None):
        return default or ""

    def show_error(self, message):
        self._publish_trace("trace.error", {"message": str(message)})
        logger.error(message)

    def show_info(self, message):
        self._publish_trace("trace.info", {"message": str(message)})
        logger.info(message)

    def show_diff(self, filename, original_content, new_content):
        logger.debug("Headless diff requested for %s", filename)

    @contextmanager
    def show_status(self, message):
        logger.debug(message)
        yield None

    def show_tool_result(self, result_str):
        self._publish_trace(
            "trace.tool_result",
            {
                "preview": str(result_str)[:200],
                "result": str(result_str),
            },
        )
        logger.info("Tool result: %s", str(result_str)[:200])

    def bind_runtime(self, task_manager, approval_manager):
        self.task_manager = task_manager
        self.approval_manager = approval_manager

    def _publish_trace(self, event_name: str, payload: dict):
        if not self.task_manager or not self.task_manager.event_hub:
            return
        task_id = self.task_manager.current_task_id()
        if not task_id:
            return
        self.task_manager.event_hub.publish(event_name, payload, task_id=task_id)

    def emit_tool_trace(
        self, tool_name: str, tool_args: dict, raw_result, visible_result
    ):
        self._publish_trace(
            "trace.tool",
            {
                "tool_name": tool_name,
                "tool_args": tool_args,
                "raw_result": raw_result,
                "visible_result": visible_result,
            },
        )

    def request_tool_approval(
        self,
        *,
        tool_name,
        tool_args,
        display_args,
        count_info,
        can_approve,
        modifications,
        preview_error,
        error_code,
        prompt_text,
        choices,
        default,
    ):
        if self.auto_approve:
            return default or (choices[0] if choices else "y"), None
        if not self.approval_manager:
            return ("n" if "n" in choices else default or ""), None
        return self.approval_manager.request_approval(
            tool_name=tool_name,
            tool_args=tool_args,
            display_args=display_args,
            count_info=count_info,
            can_approve=can_approve,
            modifications=modifications,
            preview_error=preview_error,
            error_code=error_code,
            prompt_text=prompt_text,
            choices=choices,
            default=default,
        )


class EventHub:
    def __init__(self):
        self.lock = Lock()
        self.subscribers = {}

    def subscribe(self, task_id: str | None = None):
        subscriber_id = uuid4().hex
        event_queue = queue.Queue()
        with self.lock:
            self.subscribers[subscriber_id] = {
                "queue": event_queue,
                "task_id": task_id,
            }
        return subscriber_id, event_queue

    def unsubscribe(self, subscriber_id: str):
        with self.lock:
            self.subscribers.pop(subscriber_id, None)

    def publish(self, event: str, payload: dict, task_id: str | None = None):
        envelope = {
            "id": uuid4().hex,
            "event": event,
            "task_id": task_id,
            "timestamp": time.time(),
            "payload": payload,
        }
        with self.lock:
            subscribers = list(self.subscribers.values())
        for subscriber in subscribers:
            if subscriber["task_id"] and subscriber["task_id"] != task_id:
                continue
            subscriber["queue"].put(envelope)


@dataclass
class ApprovalRequest:
    approval_id: str
    task_id: str
    tool_name: str
    tool_args: dict
    display_args: dict
    count_info: str
    can_approve: bool
    modifications: list[dict]
    preview_error: str | None
    error_code: str | None
    prompt_text: str
    choices: list[str]
    default: str
    created_at: float = field(default_factory=time.time)
    event: threading.Event = field(default_factory=threading.Event)
    resolved: bool = False
    decision: str | None = None
    reason: str | None = None

    def to_payload(self) -> dict:
        return {
            "approval_id": self.approval_id,
            "task_id": self.task_id,
            "tool_name": self.tool_name,
            "tool_args": self.tool_args,
            "display_args": self.display_args,
            "count_info": self.count_info,
            "can_approve": self.can_approve,
            "modifications": self.modifications,
            "preview_error": self.preview_error,
            "error_code": self.error_code,
            "prompt_text": self.prompt_text,
            "choices": self.choices,
            "default": self.default,
            "created_at": self.created_at,
            "resolved": self.resolved,
            "decision": self.decision,
            "reason": self.reason,
        }


class TaskManager:
    def __init__(self, session, session_lock: Lock, event_hub: EventHub | None = None):
        self.session = session
        self.session_lock = session_lock
        self.event_hub = event_hub
        self.lock = Lock()
        self.tasks = {}
        self.threads = {}
        self.task_local = threading.local()

    def create_task(self, task_type: str, payload: dict) -> str:
        task_id = uuid4().hex
        with self.lock:
            self.tasks[task_id] = {
                "task_id": task_id,
                "type": task_type,
                "payload": payload,
                "status": "pending",
                "result": None,
                "error": None,
                "created_at": time.time(),
                "updated_at": time.time(),
                "approval_id": None,
            }
        if self.event_hub:
            self.event_hub.publish(
                "task.created",
                {"task_id": task_id, "type": task_type, "payload": payload},
                task_id=task_id,
            )
        return task_id

    def update_task(self, task_id: str, **changes):
        with self.lock:
            task = self.tasks[task_id]
            task.update(changes)
            task["updated_at"] = time.time()

    def set_running(self, task_id: str):
        self.update_task(task_id, status="running", error=None)
        if self.event_hub:
            self.event_hub.publish(
                "task.running",
                {"task": self.get_task(task_id)},
                task_id=task_id,
            )

    def set_waiting_for_approval(self, task_id: str, approval_id: str):
        self.update_task(
            task_id,
            status="awaiting_approval",
            approval_id=approval_id,
        )
        if self.event_hub:
            self.event_hub.publish(
                "task.awaiting_approval",
                {"task": self.get_task(task_id), "approval_id": approval_id},
                task_id=task_id,
            )

    def clear_waiting_for_approval(self, task_id: str):
        self.update_task(task_id, status="running", approval_id=None)
        if self.event_hub:
            self.event_hub.publish(
                "task.running",
                {"task": self.get_task(task_id)},
                task_id=task_id,
            )

    def complete_task(self, task_id: str, result: dict):
        self.update_task(task_id, status="completed", result=result, approval_id=None)
        if self.event_hub:
            self.event_hub.publish(
                "task.completed",
                {"task": self.get_task(task_id), "result": result},
                task_id=task_id,
            )

    def fail_task(self, task_id: str, error: str):
        self.update_task(task_id, status="error", error=error, approval_id=None)
        if self.event_hub:
            self.event_hub.publish(
                "task.error",
                {"task": self.get_task(task_id), "error": error},
                task_id=task_id,
            )

    def get_task(self, task_id: str) -> dict | None:
        with self.lock:
            task = self.tasks.get(task_id)
            return dict(task) if task else None

    def list_tasks(self) -> list[dict]:
        with self.lock:
            return [dict(task) for task in self.tasks.values()]

    def start_message_task(self, text: str, approval_manager) -> dict:
        task_id = self.create_task("message", {"text": text})

        def runner():
            self.bind_task(task_id)
            approval_manager.bind_task(task_id)
            self.set_running(task_id)
            try:
                with self.session_lock:
                    result = self.session.send_message(text)
                self.complete_task(task_id, result)
            except Exception as exc:
                logger.error("Message task %s failed: %s", task_id, exc, exc_info=True)
                self.fail_task(task_id, str(exc))
            finally:
                approval_manager.unbind_task()
                self.unbind_task()

        thread = threading.Thread(target=runner, daemon=True)
        with self.lock:
            self.threads[task_id] = thread
        thread.start()
        return self.get_task(task_id)

    def start_tool_task(
        self,
        tool_name: str,
        tool_args: dict,
        approval_manager,
        structured: bool = True,
    ) -> dict:
        task_id = self.create_task(
            "tool",
            {
                "tool_name": tool_name,
                "tool_args": tool_args,
                "structured": structured,
            },
        )

        def runner():
            self.bind_task(task_id)
            approval_manager.bind_task(task_id)
            self.set_running(task_id)
            try:
                with self.session_lock:
                    result = execute_server_tool(self.session, tool_name, tool_args)
                    if structured:
                        response_payload = self.session._build_structured_tool_result(
                            tool_name,
                            tool_args,
                            result,
                        )
                    else:
                        response_payload = {
                            "tool_name": tool_name,
                            "tool_args": tool_args,
                            "raw": result,
                        }
                if self.event_hub:
                    self.event_hub.publish(
                        "tool.executed",
                        {
                            "tool_name": tool_name,
                            "tool_args": tool_args,
                            "result": response_payload,
                            "session_name": self.session.session_manager.current_session_name,
                        },
                        task_id=task_id,
                    )
                self.complete_task(task_id, {"ok": True, "result": response_payload})
            except Exception as exc:
                logger.error("Tool task %s failed: %s", task_id, exc, exc_info=True)
                self.fail_task(task_id, str(exc))
            finally:
                approval_manager.unbind_task()
                self.unbind_task()

        thread = threading.Thread(target=runner, daemon=True)
        with self.lock:
            self.threads[task_id] = thread
        thread.start()
        return self.get_task(task_id)

    def wait_for_task(self, task_id: str, timeout: float | None = None) -> dict | None:
        with self.lock:
            thread = self.threads.get(task_id)
        if thread:
            thread.join(timeout)
        return self.get_task(task_id)

    def wait_for_task_state(
        self,
        task_id: str,
        terminal_states: set[str],
        timeout: float = 30.0,
        poll_interval: float = 0.05,
    ) -> dict | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            task = self.get_task(task_id)
            if not task or task["status"] in terminal_states:
                return task
            time.sleep(poll_interval)
        return self.get_task(task_id)

    def bind_task(self, task_id: str):
        self.task_local.task_id = task_id

    def unbind_task(self):
        self.task_local.task_id = None

    def current_task_id(self) -> str | None:
        return getattr(self.task_local, "task_id", None)


class ApprovalManager:
    def __init__(self, task_manager: TaskManager, event_hub: EventHub | None = None):
        self.task_manager = task_manager
        self.event_hub = event_hub
        self.lock = Lock()
        self.pending = {}
        self.task_local = threading.local()

    def bind_task(self, task_id: str):
        self.task_local.task_id = task_id

    def unbind_task(self):
        self.task_local.task_id = None

    def _current_task_id(self) -> str:
        task_id = getattr(self.task_local, "task_id", None)
        if not task_id:
            raise RuntimeError("Approval requested outside of a tracked task.")
        return task_id

    def request_approval(self, **kwargs):
        task_id = self._current_task_id()
        approval = ApprovalRequest(
            approval_id=uuid4().hex,
            task_id=task_id,
            **kwargs,
        )
        with self.lock:
            self.pending[approval.approval_id] = approval
        if self.event_hub:
            self.event_hub.publish(
                "approval.requested",
                approval.to_payload(),
                task_id=task_id,
            )
        self.task_manager.set_waiting_for_approval(task_id, approval.approval_id)
        approval.event.wait()
        self.task_manager.clear_waiting_for_approval(task_id)
        return approval.decision or approval.default, approval.reason

    def list_pending(self) -> list[dict]:
        with self.lock:
            return [approval.to_payload() for approval in self.pending.values()]

    def get_pending(self, approval_id: str) -> dict | None:
        with self.lock:
            approval = self.pending.get(approval_id)
            return approval.to_payload() if approval else None

    def resolve(
        self, approval_id: str, decision: str, reason: str | None = None
    ) -> dict:
        with self.lock:
            approval = self.pending.get(approval_id)
            if not approval:
                raise KeyError(f"Unknown approval_id: {approval_id}")
            approval.decision = decision
            approval.reason = reason
            approval.resolved = True
            payload = approval.to_payload()
            del self.pending[approval_id]
        if self.event_hub:
            self.event_hub.publish(
                "approval.resolved",
                payload,
                task_id=payload["task_id"],
            )
        approval.event.set()
        return payload


def build_state_payload(session) -> dict:
    session.sync_runtime_state()
    return {
        "session_name": session.session_manager.current_session_name,
        "provider": session.provider.name,
        "model": session.provider.model_name,
        "thinking": session.thinking,
        "agentic": session.agentic,
        "folders": list(session.folder_context.folders),
        "staged_files": list(session.staged_files),
        "disabled_tools": list(session.disabled_tools),
        "variables": dict(session.variables),
        "history_length": len(session.session_manager.history),
        "token_counts": dict(session.session_manager.token_counts),
        "available_tools": [
            {
                **serialize_tool_descriptor(tool.name),
                "enabled": tool.name not in session.disabled_tools,
            }
            for tool in TOOLS
        ],
    }


def build_history_payload(session, limit: int | None = None) -> dict:
    history = session.session_manager.history
    if limit is not None and limit >= 0:
        history = history[-limit:]
    return {
        "session_name": session.session_manager.current_session_name,
        "history": history,
        "history_length": len(session.session_manager.history),
    }


def build_sessions_payload(session) -> dict:
    return {
        "current_session_name": session.session_manager.current_session_name,
        "sessions": session.session_manager.get_session_list(),
    }


def build_runtime_payload(session) -> dict:
    session.sync_runtime_state()
    sync_live_provider_settings(session)
    return {
        "session_name": session.session_manager.current_session_name,
        "provider": session.provider.name,
        "model": session.provider.model_name,
        "system_instruction": session.system_instruction,
        "thinking": session.thinking,
        "agentic": session.agentic,
        "disabled_tools": list(session.disabled_tools),
        "variables": dict(session.variables),
    }


def build_workspace_payload(session) -> dict:
    session.sync_runtime_state()
    return {
        "folders": list(session.folder_context.folders),
        "tracked_files": session.folder_context.get_file_list(),
        "tracked_file_count": len(session.folder_context.get_file_list()),
    }


def build_staged_files_payload(session) -> dict:
    return {
        "staged_files": list(session.staged_files),
        "staged_file_count": len(session.staged_files),
    }


def publish_server_event(state: dict, event_name: str, payload: dict):
    state["event_hub"].publish(event_name, payload, task_id=payload.get("task_id"))


def sync_live_provider_settings(session):
    if isinstance(session.provider, OllamaProvider):
        session.provider.host = session.variables.get(
            "ollama_host", "http://localhost:11434"
        )


def execute_server_tool(session, tool_name: str, tool_args: dict):
    if tool_name in session.disabled_tools:
        raise PermissionError(f"Tool '{tool_name}' is disabled for this session.")

    descriptor = get_tool_descriptor(tool_name)
    if not descriptor:
        raise ValueError(f"Unknown tool: {tool_name}")
    if descriptor.server_policy != "allowed":
        raise PermissionError(
            f"Tool '{tool_name}' is not available for direct server execution."
        )

    tool_def = get_tool_definition(tool_name)
    approval_plan = build_approval_plan(
        tool_name,
        tool_args,
        session.folder_context,
        strict_mode=False,
        yolo=session.variables.get("yolo", False),
    )
    if tool_def and approval_plan.requires_approval:
        choice, reason = session._request_tool_approval(
            approval_plan=approval_plan,
            display_args=tool_args,
            count_info="",
        )
        if choice == "n":
            return f"User denied direct tool call: {tool_name}"
        if choice == "e":
            return f"User denied direct tool call: {tool_name}. Reason: {reason}"

    return session._execute_tool_with_memory(tool_name, tool_args)


def serve(session, host: str, port: int, command_handler):
    session_lock = Lock()
    event_hub = EventHub()
    task_manager = TaskManager(session, session_lock, event_hub=event_hub)
    approval_manager = ApprovalManager(task_manager, event_hub=event_hub)
    if hasattr(session.ui, "bind_runtime"):
        session.ui.bind_runtime(task_manager, approval_manager)

    state = {
        "session": session,
        "session_lock": session_lock,
        "command_handler": command_handler,
        "task_manager": task_manager,
        "approval_manager": approval_manager,
        "event_hub": event_hub,
    }

    class MuCLIRequestHandler(BaseHTTPRequestHandler):
        server_version = "MuCLIServer/0.1"

        def _read_json(self):
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length else b"{}"
            if not raw:
                return {}
            try:
                return json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON body: {exc}") from exc

        def _send_json(self, status_code: int, payload: dict):
            body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.end_headers()
            self.wfile.write(body)

        def _not_found(self):
            self._send_json(404, {"ok": False, "error": "Endpoint not found."})

        def do_OPTIONS(self):
            self._send_json(204, {})

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/api/events":
                query = parse_qs(parsed.query)
                task_id = query.get("task_id", [None])[0]
                subscriber_id, event_queue = state["event_hub"].subscribe(
                    task_id=task_id
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                try:
                    initial_event = {
                        "id": uuid4().hex,
                        "event": "stream.open",
                        "task_id": task_id,
                        "timestamp": time.time(),
                        "payload": {"task_id": task_id},
                    }
                    self.wfile.write(
                        (
                            f"id: {initial_event['id']}\n"
                            f"event: {initial_event['event']}\n"
                            f"data: {json.dumps(initial_event)}\n\n"
                        ).encode("utf-8")
                    )
                    self.wfile.flush()

                    while True:
                        try:
                            event = event_queue.get(timeout=15)
                            self.wfile.write(
                                (
                                    f"id: {event['id']}\n"
                                    f"event: {event['event']}\n"
                                    f"data: {json.dumps(event)}\n\n"
                                ).encode("utf-8")
                            )
                        except queue.Empty:
                            self.wfile.write(b": keep-alive\n\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    logger.info("SSE client disconnected.")
                finally:
                    state["event_hub"].unsubscribe(subscriber_id)
                return

            if parsed.path == "/api/tasks":
                self._send_json(
                    200, {"ok": True, "tasks": state["task_manager"].list_tasks()}
                )
                return
            if parsed.path.startswith("/api/tasks/"):
                task_id = parsed.path.rsplit("/", 1)[-1]
                task = state["task_manager"].get_task(task_id)
                if not task:
                    self._send_json(404, {"ok": False, "error": "Task not found."})
                    return
                self._send_json(200, {"ok": True, "task": task})
                return
            if parsed.path == "/api/approvals":
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "pending_approvals": state["approval_manager"].list_pending(),
                    },
                )
                return
            if parsed.path.startswith("/api/approvals/"):
                approval_id = parsed.path.rsplit("/", 1)[-1]
                approval = state["approval_manager"].get_pending(approval_id)
                if not approval:
                    self._send_json(404, {"ok": False, "error": "Approval not found."})
                    return
                self._send_json(200, {"ok": True, "approval": approval})
                return

            with state["session_lock"]:
                session = state["session"]
                if parsed.path == "/health":
                    self._send_json(
                        200,
                        {
                            "ok": True,
                            "status": "ok",
                            "session_name": session.session_manager.current_session_name,
                        },
                    )
                    return
                if parsed.path == "/api/state":
                    self._send_json(
                        200, {"ok": True, "state": build_state_payload(session)}
                    )
                    return
                if parsed.path == "/api/tools":
                    self._send_json(
                        200,
                        {
                            "ok": True,
                            "tools": build_state_payload(session)["available_tools"],
                        },
                    )
                    return
                if parsed.path == "/api/history":
                    query = parse_qs(parsed.query)
                    limit = query.get("limit", [None])[0]
                    limit_value = int(limit) if limit is not None else None
                    self._send_json(
                        200,
                        {
                            "ok": True,
                            **build_history_payload(session, limit_value),
                        },
                    )
                    return
                if parsed.path == "/api/sessions":
                    self._send_json(
                        200, {"ok": True, **build_sessions_payload(session)}
                    )
                    return
                if parsed.path == "/api/runtime":
                    self._send_json(200, {"ok": True, **build_runtime_payload(session)})
                    return
                if parsed.path == "/api/workspaces":
                    self._send_json(
                        200, {"ok": True, **build_workspace_payload(session)}
                    )
                    return
                if parsed.path == "/api/staged-files":
                    self._send_json(
                        200, {"ok": True, **build_staged_files_payload(session)}
                    )
                    return

            self._not_found()

        def do_POST(self):
            parsed = urlparse(self.path)
            try:
                payload = self._read_json()
            except ValueError as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return

            if parsed.path == "/api/approvals/resolve":
                approval_id = str(payload.get("approval_id", "") or "").strip()
                decision = str(payload.get("decision", "") or "").strip().lower()
                reason = payload.get("reason")
                if not approval_id:
                    self._send_json(
                        400,
                        {"ok": False, "error": "Field 'approval_id' is required."},
                    )
                    return
                if decision not in {"y", "n", "e", "approve", "reject", "explain"}:
                    self._send_json(
                        400,
                        {
                            "ok": False,
                            "error": "Field 'decision' must be one of approve/reject/explain or y/n/e.",
                        },
                    )
                    return
                mapped_decision = {
                    "approve": "y",
                    "reject": "n",
                    "explain": "e",
                }.get(decision, decision)
                try:
                    approval = state["approval_manager"].resolve(
                        approval_id,
                        mapped_decision,
                        None if reason is None else str(reason),
                    )
                except KeyError as exc:
                    self._send_json(404, {"ok": False, "error": str(exc)})
                    return
                self._send_json(200, {"ok": True, "approval": approval})
                return

            if parsed.path == "/api/message":
                text = str(payload.get("text", "") or "")
                if not text.strip():
                    self._send_json(
                        400, {"ok": False, "error": "Field 'text' is required."}
                    )
                    return
                async_mode = bool(payload.get("async", False))
                task = state["task_manager"].start_message_task(
                    text, state["approval_manager"]
                )
                task_id = task["task_id"]
                if async_mode:
                    self._send_json(202, {"ok": True, "task": task})
                    return

                task = state["task_manager"].wait_for_task_state(
                    task_id,
                    {"completed", "error", "awaiting_approval"},
                )
                if not task:
                    self._send_json(500, {"ok": False, "error": "Task disappeared."})
                    return
                if task["status"] == "completed":
                    self._send_json(200, task["result"])
                else:
                    self._send_json(202, {"ok": True, "task": task})
                return

            if parsed.path == "/api/tool":
                tool_name = str(payload.get("tool_name", "") or "")
                tool_args = payload.get("tool_args", {}) or {}
                if not tool_name:
                    self._send_json(
                        400,
                        {
                            "ok": False,
                            "error": "Field 'tool_name' is required.",
                        },
                    )
                    return
                async_mode = bool(payload.get("async", False))
                task = state["task_manager"].start_tool_task(
                    tool_name,
                    tool_args,
                    state["approval_manager"],
                    structured=bool(payload.get("structured", True)),
                )
                task_id = task["task_id"]
                if async_mode:
                    self._send_json(202, {"ok": True, "task": task})
                    return

                task = state["task_manager"].wait_for_task_state(
                    task_id,
                    {"completed", "error", "awaiting_approval"},
                )
                if not task:
                    self._send_json(500, {"ok": False, "error": "Task disappeared."})
                    return
                if task["status"] == "completed":
                    self._send_json(200, task["result"])
                elif task["status"] == "error":
                    self._send_json(
                        500,
                        {
                            "ok": False,
                            "error": task["error"] or "Tool task failed.",
                            "task": task,
                        },
                    )
                else:
                    self._send_json(202, {"ok": True, "task": task})
                return

            with state["session_lock"]:
                session = state["session"]
                try:
                    if parsed.path == "/api/command":
                        command = str(payload.get("command", "") or "")
                        if not command.startswith("/"):
                            self._send_json(
                                400,
                                {
                                    "ok": False,
                                    "error": "Field 'command' must start with '/'.",
                                },
                            )
                            return
                        result = state["command_handler"](
                            session,
                            command,
                            allow_prompt=False,
                        )
                        publish_server_event(
                            state,
                            "command.completed",
                            {
                                "command": command,
                                "result": result,
                                "session_name": session.session_manager.current_session_name,
                            },
                        )
                        self._send_json(200, result)
                        return

                    if parsed.path == "/api/sessions/new":
                        name = str(payload.get("name", "") or "").strip() or None
                        session.session_manager.new_session(
                            name,
                            session.provider.name,
                            session.provider.model_name,
                        )
                        session.staged_files = []
                        session.sync_runtime_state()
                        sync_live_provider_settings(session)
                        publish_server_event(
                            state,
                            "session.created",
                            {
                                "session_name": session.session_manager.current_session_name,
                                "sessions": session.session_manager.get_session_list(),
                            },
                        )
                        self._send_json(
                            200,
                            {
                                "ok": True,
                                "message": "Started new session.",
                                **build_sessions_payload(session),
                                **build_runtime_payload(session),
                            },
                        )
                        return

                    if parsed.path == "/api/sessions/load":
                        name = str(payload.get("name", "") or "").strip()
                        if not name:
                            self._send_json(
                                400,
                                {
                                    "ok": False,
                                    "error": "Field 'name' is required.",
                                },
                            )
                            return
                        result = command_handler(
                            session,
                            f"/load {name}",
                            allow_prompt=False,
                        )
                        publish_server_event(
                            state,
                            "session.loaded",
                            {
                                "session_name": session.session_manager.current_session_name,
                                "sessions": session.session_manager.get_session_list(),
                            },
                        )
                        self._send_json(200, result)
                        return

                    if parsed.path == "/api/sessions/delete":
                        name = str(payload.get("name", "") or "").strip()
                        if not name:
                            self._send_json(
                                400,
                                {
                                    "ok": False,
                                    "error": "Field 'name' is required.",
                                },
                            )
                            return
                        session.session_manager.delete_session(name)
                        publish_server_event(
                            state,
                            "session.deleted",
                            {
                                "deleted_session_name": name,
                                "session_name": session.session_manager.current_session_name,
                                "sessions": session.session_manager.get_session_list(),
                            },
                        )
                        self._send_json(
                            200,
                            {
                                "ok": True,
                                "message": f"Deleted session request: {name}",
                                **build_sessions_payload(session),
                            },
                        )
                        return

                    if parsed.path == "/api/runtime":
                        if "provider" in payload:
                            self._send_json(
                                400,
                                {
                                    "ok": False,
                                    "error": "Changing provider is not yet supported by /api/runtime. Use /api/command with /provider.",
                                },
                            )
                            return

                        if "system_instruction" in payload:
                            session.system_instruction = str(
                                payload.get("system_instruction") or ""
                            )
                        if "thinking" in payload:
                            session.thinking = bool(payload.get("thinking"))
                        if "agentic" in payload:
                            session.agentic = bool(payload.get("agentic"))
                        if "model" in payload:
                            session.provider.model_name = str(
                                payload.get("model") or ""
                            )
                            session.session_manager.provider_config = {
                                "provider": session.provider.name,
                                "model": session.provider.model_name,
                            }
                        if "disabled_tools" in payload:
                            disabled_tools = payload.get("disabled_tools", []) or []
                            session.disabled_tools = [
                                str(tool) for tool in disabled_tools
                            ]
                        if "variables" in payload:
                            for key, value in dict(
                                payload.get("variables") or {}
                            ).items():
                                session.variables[key] = validate_and_cast(key, value)
                        sync_live_provider_settings(session)
                        session.session_manager.save_history(session.folder_context)
                        publish_server_event(
                            state,
                            "runtime.updated",
                            build_runtime_payload(session),
                        )
                        self._send_json(
                            200,
                            {
                                "ok": True,
                                "message": "Runtime updated.",
                                **build_runtime_payload(session),
                            },
                        )
                        return

                    if parsed.path == "/api/workspaces/add":
                        path = str(payload.get("path", "") or "").strip()
                        if not path:
                            self._send_json(
                                400,
                                {
                                    "ok": False,
                                    "error": "Field 'path' is required.",
                                },
                            )
                            return
                        result = command_handler(
                            session,
                            f"/folder {path}",
                            allow_prompt=False,
                        )
                        publish_server_event(
                            state,
                            "workspace.updated",
                            build_workspace_payload(session),
                        )
                        self._send_json(200, result)
                        return

                    if parsed.path == "/api/workspaces/remove":
                        path = str(payload.get("path", "") or "").strip()
                        if not path:
                            self._send_json(
                                400,
                                {
                                    "ok": False,
                                    "error": "Field 'path' is required.",
                                },
                            )
                            return
                        result = command_handler(
                            session,
                            f"/folder remove {path}",
                            allow_prompt=False,
                        )
                        publish_server_event(
                            state,
                            "workspace.updated",
                            build_workspace_payload(session),
                        )
                        self._send_json(200, result)
                        return

                    if parsed.path == "/api/staged-files/add":
                        path = str(payload.get("path", "") or "").strip()
                        if not path:
                            self._send_json(
                                400,
                                {
                                    "ok": False,
                                    "error": "Field 'path' is required.",
                                },
                            )
                            return
                        result = command_handler(
                            session,
                            f"/file {path}",
                            allow_prompt=False,
                        )
                        publish_server_event(
                            state,
                            "staged_files.updated",
                            build_staged_files_payload(session),
                        )
                        self._send_json(200, result)
                        return

                    if parsed.path == "/api/staged-files/clear":
                        session.clear_files()
                        publish_server_event(
                            state,
                            "staged_files.updated",
                            build_staged_files_payload(session),
                        )
                        self._send_json(
                            200,
                            {
                                "ok": True,
                                "message": "Staged files cleared.",
                                **build_staged_files_payload(session),
                            },
                        )
                        return
                except Exception as exc:
                    logger.error("Server request failed: %s", exc, exc_info=True)
                    self._send_json(500, {"ok": False, "error": str(exc)})
                    return

            self._not_found()

        def log_message(self, format, *args):
            logger.info("server " + format, *args)

    httpd = ThreadingHTTPServer((host, port), MuCLIRequestHandler)
    logger.info("Starting μCLI server on http://%s:%s", host, port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("μCLI server interrupted, shutting down.")
    finally:
        httpd.server_close()
