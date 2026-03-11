from __future__ import annotations

import json
import queue
import threading
from dataclasses import asdict, dataclass
from typing import Any, Callable

from flask import Response, stream_with_context

from mu_cli.core.types import Message, ToolCall


@dataclass(slots=True)
class ChatTurnDeps:
    run_turn_with_uploaded_context: Callable[[Any, str], Any]
    turn_report: Callable[[Any, str, str], dict[str, Any]]
    record_turn: Callable[[Any, dict[str, Any]], None]
    condense_session_context: Callable[..., dict[str, Any]]
    persist: Callable[[Any], None]


@dataclass(slots=True)
class ChatTurnResult:
    reply: dict[str, Any]
    report: dict[str, Any]
    traces: list[str]


def execute_chat_turn(runtime: Any, text: str, deps: ChatTurnDeps) -> ChatTurnResult:
    reply = deps.run_turn_with_uploaded_context(runtime, text)
    report = deps.turn_report(runtime, text, reply.content)
    deps.record_turn(runtime, report)
    if runtime.condense_enabled:
        deps.condense_session_context(runtime, window_size=runtime.condense_window)
    deps.persist(runtime)
    return ChatTurnResult(reply=asdict(reply), report=report, traces=runtime.traces[-50:])


@dataclass(slots=True)
class ChatStreamDeps:
    iter_chunks: Callable[[str], Any]
    load_session: Callable[[Any, str], bool]


@dataclass(slots=True)
class ChatStreamingService:
    turn_deps: ChatTurnDeps
    stream_deps: ChatStreamDeps

    def stream_chat(self, runtime: Any, text: str, session_name: str) -> Response:
        events: queue.Queue[dict[str, Any]] = queue.Queue()
        done = threading.Event()
        saw_stream_chunks = False

        original_model_response = runtime.agent.on_model_response
        original_model_stream = getattr(runtime.agent, "on_model_stream", None)
        original_tool_run = runtime.agent.on_tool_run

        def stream_model_response(message: Message, calls: list[ToolCall]) -> None:
            nonlocal saw_stream_chunks
            if original_model_response is not None:
                original_model_response(message, calls)
            for call in calls:
                events.put({"type": "trace", "line": f"tool-request: id={call.call_id} name={call.name} args={call.args}"})
            if message.content and not saw_stream_chunks:
                for chunk in self.stream_deps.iter_chunks(message.content):
                    events.put({"type": "assistant_chunk", "chunk": chunk})


        def stream_model_chunk(payload: dict[str, Any]) -> None:
            nonlocal saw_stream_chunks
            kind = str(payload.get("kind", ""))
            chunk = str(payload.get("chunk", ""))
            if not chunk:
                return
            saw_stream_chunks = True
            if kind == "thinking_output":
                events.put({"type": "thinking_chunk", "tag": "thinking output", "chunk": chunk})
            else:
                events.put({"type": "assistant_chunk", "chunk": chunk})

        def stream_tool_run(name: str, args: dict, ok: bool, output: str) -> None:
            if original_tool_run is not None:
                original_tool_run(name, args, ok, output)
            events.put({"type": "trace", "line": f"tool-run: name={name} ok={ok} args={args} output={output[:200]}"})

        def run_turn() -> None:
            runtime.agent.on_model_response = stream_model_response
            runtime.agent.on_model_stream = stream_model_chunk
            runtime.agent.on_tool_run = stream_tool_run
            try:
                if runtime.session_name != session_name:
                    self.stream_deps.load_session(runtime, session_name)
                result = execute_chat_turn(runtime, text, self.turn_deps)
                events.put({"type": "report", "report": result.report})
                events.put({"type": "done", "reply": result.reply, "traces": result.traces})
            except Exception as exc:  # pragma: no cover
                events.put({"type": "error", "error": str(exc)})
            finally:
                runtime.agent.on_model_response = original_model_response
                runtime.agent.on_model_stream = original_model_stream
                runtime.agent.on_tool_run = original_tool_run
                done.set()

        thread = threading.Thread(target=run_turn, daemon=True)
        thread.start()

        @stream_with_context
        def generate():
            while not done.is_set() or not events.empty():
                try:
                    item = events.get(timeout=0.1)
                except queue.Empty:
                    continue
                yield json.dumps(item) + "\n"

        return Response(generate(), mimetype="application/x-ndjson")
