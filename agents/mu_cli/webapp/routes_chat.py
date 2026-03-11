from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from flask import jsonify, request
from mu_cli.webapp.jobs import JobDeps, decide_plan, get_job, list_jobs, request_kill, start_job
from mu_cli.webapp.contracts import (
    ContractValidationError,
    parse_chat_request,
    parse_job_kill_request,
    parse_job_plan_request,
)
from mu_cli.webapp.services_chat import ChatStreamDeps, ChatStreamingService, ChatTurnDeps, execute_chat_turn


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
    record_telemetry_action: Callable[[Any, str], None]


def register_chat_routes(app, runtime: Any, deps: ChatRouteDeps) -> None:
    job_deps = JobDeps(start_background_turn=deps.start_background_turn)

    @app.post("/api/chat")
    def chat():
        try:
            req = parse_chat_request(request.get_json(force=True), route="/api/chat")
        except ContractValidationError as exc:
            return jsonify({"error": str(exc)}), 400

        deps.record_telemetry_action(runtime, "chat_turn")
        result = execute_chat_turn(
            runtime,
            req.text,
            ChatTurnDeps(
                run_turn_with_uploaded_context=deps.run_turn_with_uploaded_context,
                turn_report=deps.turn_report,
                record_turn=deps.record_turn,
                condense_session_context=deps.condense_session_context,
                persist=deps.persist,
            ),
        )
        return jsonify({"reply": result.reply, "report": result.report, "traces": result.traces})

    @app.post("/api/chat/background")
    def chat_background():
        try:
            req = parse_chat_request(request.get_json(force=True), route="/api/chat/background")
        except ContractValidationError as exc:
            return jsonify({"error": str(exc)}), 400
        deps.record_telemetry_action(runtime, "chat_stream")
        session_name = req.session or runtime.session_name
        deps.record_telemetry_action(runtime, "chat_background_start")
        job_id = start_job(runtime, session_name, req.text, job_deps)
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
        try:
            req = parse_job_kill_request(request.get_json(silent=True))
        except ContractValidationError as exc:
            return jsonify({"error": str(exc)}), 400
        deps.record_telemetry_action(runtime, "job_kill_request")
        code, payload = request_kill(runtime, job_id, req.reason)
        return jsonify(payload), code

    @app.post("/api/jobs/<job_id>/plan")
    def decide_job_plan(job_id: str):
        try:
            req = parse_job_plan_request(request.get_json(force=True))
        except ContractValidationError as exc:
            return jsonify({"error": str(exc)}), 400
        deps.record_telemetry_action(runtime, "job_plan_decision")
        code, out = decide_plan(runtime, job_id, req.decision, req.revised_plan)
        return jsonify(out), code

    @app.post("/api/chat/stream")
    def chat_stream():
        try:
            req = parse_chat_request(request.get_json(force=True), route="/api/chat/stream")
        except ContractValidationError as exc:
            return jsonify({"error": str(exc)}), 400
        deps.record_telemetry_action(runtime, "chat_stream")
        session_name = req.session or runtime.session_name
        service = ChatStreamingService(
            turn_deps=ChatTurnDeps(
                run_turn_with_uploaded_context=deps.run_turn_with_uploaded_context,
                turn_report=deps.turn_report,
                record_turn=deps.record_turn,
                condense_session_context=deps.condense_session_context,
                persist=deps.persist,
            ),
            stream_deps=ChatStreamDeps(iter_chunks=deps.iter_chunks, load_session=deps.load_session),
        )
        return service.stream_chat(runtime, req.text, session_name)
