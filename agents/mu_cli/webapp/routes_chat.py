from __future__ import annotations

import json
import queue
import threading
from dataclasses import asdict, dataclass
from typing import Any, Callable

from flask import Response, jsonify, request, stream_with_context

from mu_cli.core.types import Message, ToolCall
from mu_cli.webapp.jobs import JobDeps, decide_plan, get_job, list_jobs, request_kill, start_job


@dataclass(slots=True)
class ChatRouteDeps:
    run_turn_with_uploaded_context: Callable[[Any, str], Any]
    turn_report: Callable[[Any, str, str], dict[str, Any]]
    record_turn: Callable[[Any, dict[str, Any]], None]
    condense_session_context: Callable[..., dict[str, Any]]
    persist: Callable[[Any], None]
    start_background_turn: Callable[[Any, str, str], str]
    iter_chunks: Callable[[str], Any]
    load_session: Callable[[Any, str], bool]


def register_chat_routes(app, runtime: Any, deps: ChatRouteDeps) -> None:
    job_deps = JobDeps(start_background_turn=deps.start_background_turn)

    @app.post("/api/chat")
    def chat():
        payload = request.get_json(force=True)
        text = str(payload.get("text", "")).strip()
        if not text:
            return jsonify({"error": "text is required"}), 400

        reply = deps.run_turn_with_uploaded_context(runtime, text)
        report = deps.turn_report(runtime, text, reply.content)
        deps.record_turn(runtime, report)
        if runtime.condense_enabled:
            deps.condense_session_context(runtime, window_size=runtime.condense_window)
        deps.persist(runtime)
        return jsonify({"reply": asdict(reply), "report": report, "traces": runtime.traces[-50:]})

    @app.post("/api/chat/background")
    def chat_background():
        payload = request.get_json(force=True)
        text = str(payload.get("text", "")).strip()
        if not text:
            return jsonify({"error": "text is required"}), 400
        session_name = str(payload.get("session", runtime.session_name)).strip() or runtime.session_name
        job_id = start_job(runtime, session_name, text, job_deps)
        return jsonify({"ok": True, "job_id": job_id, "session": session_name})

    @app.get("/api/jobs")
    def list_jobs_route():
        return jsonify({"jobs": list_jobs(runtime)})

    @app.get("/api/jobs/<job_id>")
    def get_job_route(job_id: str):
        job = get_job(runtime, job_id)
        if job is None:
            return jsonify({"error": "job not found"}), 404
        return jsonify(job)

    @app.post("/api/jobs/<job_id>/kill")
    def kill_job(job_id: str):
        reason = str((request.get_json(silent=True) or {}).get("reason", "")).strip() or "user requested stop"
        code, payload = request_kill(runtime, job_id, reason)
        return jsonify(payload), code

    @app.post("/api/jobs/<job_id>/plan")
    def decide_job_plan(job_id: str):
        payload = request.get_json(force=True)
        decision = str(payload.get("decision", "")).strip().lower()
        revised_plan = str(payload.get("revised_plan", "")).strip()
        code, out = decide_plan(runtime, job_id, decision, revised_plan)
        return jsonify(out), code

    @app.post("/api/chat/stream")
    def chat_stream():
        payload = request.get_json(force=True)
        text = str(payload.get("text", "")).strip()
        if not text:
            return jsonify({"error": "text is required"}), 400
        session_name = str(payload.get("session", runtime.session_name)).strip() or runtime.session_name

        events: queue.Queue[dict] = queue.Queue()
        done = threading.Event()

        original_model_response = runtime.agent.on_model_response
        original_tool_run = runtime.agent.on_tool_run

        def stream_model_response(message: Message, calls: list[ToolCall]) -> None:
            if original_model_response is not None:
                original_model_response(message, calls)
            for call in calls:
                events.put({"type": "trace", "line": f"tool-request: id={call.call_id} name={call.name} args={call.args}"})
            if message.content:
                for chunk in deps.iter_chunks(message.content):
                    events.put({"type": "assistant_chunk", "chunk": chunk})

        def stream_tool_run(name: str, args: dict, ok: bool, output: str) -> None:
            if original_tool_run is not None:
                original_tool_run(name, args, ok, output)
            events.put({"type": "trace", "line": f"tool-run: name={name} ok={ok} args={args} output={output[:200]}"})

        def run_turn() -> None:
            runtime.agent.on_model_response = stream_model_response
            runtime.agent.on_tool_run = stream_tool_run
            try:
                if runtime.session_name != session_name:
                    deps.load_session(runtime, session_name)
                reply = deps.run_turn_with_uploaded_context(runtime, text)
                report = deps.turn_report(runtime, text, reply.content)
                deps.record_turn(runtime, report)
                if runtime.condense_enabled:
                    deps.condense_session_context(runtime, window_size=runtime.condense_window)
                deps.persist(runtime)
                events.put({"type": "report", "report": report})
                events.put({"type": "done", "reply": asdict(reply), "traces": runtime.traces[-50:]})
            except Exception as exc:  # pragma: no cover
                events.put({"type": "error", "error": str(exc)})
            finally:
                runtime.agent.on_model_response = original_model_response
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
