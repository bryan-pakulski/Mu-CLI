from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from flask import jsonify, request


@dataclass(slots=True)
class SessionRouteDeps:
    get_models: Callable[[str, str | None], list[str]]
    provider_api_key: Callable[[Any], str | None]
    load_session: Callable[[Any, str], bool]
    attach_workspace_if_available: Callable[[Any], None]
    initialize_fresh_session_state: Callable[[Any], None]
    initialize_fresh_session_state_reset_summary: Callable[[Any], None]
    persist: Callable[[Any], None]
    condense_session_context: Callable[..., dict[str, Any]]


def register_session_routes(app, runtime: Any, deps: SessionRouteDeps) -> None:
    @app.post("/api/session")
    def session_action():
        payload = request.get_json(force=True)
        action = str(payload.get("action", "")).strip()
        name = str(payload.get("name", "")).strip()

        if action == "status":
            return jsonify({"session": runtime.session_name})

        if action == "list":
            return jsonify({"sessions": runtime.session_store.list_sessions()})

        if action == "new":
            if not name:
                return jsonify({"error": "name required"}), 400

            runtime.provider = str(payload.get("provider", runtime.provider))
            selected_model = str(payload.get("model", runtime.model))
            if "openai_api_key" in payload:
                runtime.openai_api_key = payload.get("openai_api_key") or None
            if "google_api_key" in payload:
                runtime.google_api_key = payload.get("google_api_key") or None
            available = deps.get_models(runtime.provider, deps.provider_api_key(runtime))
            runtime.model = selected_model if selected_model in available else (available[0] if available else runtime.model)
            runtime.agentic_planning = bool(payload.get("agentic_planning", runtime.agentic_planning))
            runtime.research_mode = bool(payload.get("research_mode", runtime.research_mode))
            runtime.approval_mode = str(payload.get("approval_mode", runtime.approval_mode))
            runtime.max_runtime_seconds = int(payload.get("max_runtime_seconds", runtime.max_runtime_seconds) or runtime.max_runtime_seconds)
            runtime.condense_enabled = bool(payload.get("condense_enabled", runtime.condense_enabled))
            runtime.condense_window = int(payload.get("condense_window", runtime.condense_window) or runtime.condense_window)
            enabled_skills = payload.get("enabled_skills")
            if isinstance(enabled_skills, list):
                runtime.enabled_skills = [str(item).strip() for item in enabled_skills if str(item).strip()]
            else:
                runtime.enabled_skills = []

            workspace = payload.get("workspace")
            runtime.workspace_path = str(workspace).strip() if workspace else None
            runtime.workspace_store.snapshot = None
            deps.attach_workspace_if_available(runtime)

            runtime.session_name = name
            runtime.session_store.use(name)
            deps.initialize_fresh_session_state(runtime)
            deps.persist(runtime)
            return jsonify({"ok": True, "session": name})

        if action in {"load", "switch"}:
            if not name:
                return jsonify({"error": "name required"}), 400
            loaded = deps.load_session(runtime, name)
            if not loaded:
                return jsonify({"error": "session not found"}), 404
            return jsonify({"ok": True, "session": name})

        if action == "delete":
            if not name:
                return jsonify({"error": "name required"}), 400
            if name == runtime.session_name:
                return jsonify({"error": "cannot delete active session"}), 400
            deleted = runtime.session_store.delete(name)
            if not deleted:
                return jsonify({"error": "session not found"}), 404
            return jsonify({"ok": True})

        if action == "clear":
            target = name or runtime.session_name
            if target != runtime.session_name:
                loaded = deps.load_session(runtime, target)
                if not loaded:
                    return jsonify({"error": "session not found"}), 404

            deps.attach_workspace_if_available(runtime)
            deps.initialize_fresh_session_state_reset_summary(runtime)
            deps.persist(runtime)
            return jsonify({"ok": True, "session": runtime.session_name})

        if action == "condense":
            w = payload.get("window")
            result = deps.condense_session_context(runtime, window_size=int(w) if w is not None else runtime.condense_window)
            deps.persist(runtime)
            return jsonify(result)

        return jsonify({"error": "unsupported action"}), 400
