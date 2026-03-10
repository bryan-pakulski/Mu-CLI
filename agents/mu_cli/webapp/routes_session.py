from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from flask import jsonify, request

from mu_cli.webapp.contracts import ContractValidationError, parse_session_action_request


@dataclass(slots=True)
class SessionRouteDeps:
    load_session: Callable[[Any, str], bool]
    delete_session: Callable[[Any, str], bool]
    persist: Callable[[Any], None]
    condense_session_context: Callable[..., dict[str, Any]]
    mutate_for_new_session: Callable[[Any, dict[str, Any], str], None]
    mutate_for_clear: Callable[[Any, bool], None]


def register_session_routes(app, runtime: Any, deps: SessionRouteDeps) -> None:
    @app.post("/api/session")
    def session_action():
        try:
            req = parse_session_action_request(request.get_json(force=True))
        except ContractValidationError as exc:
            return jsonify({"error": str(exc)}), 400
        payload = req.payload
        action = req.action
        name = req.name

        if action == "status":
            return jsonify({"session": runtime.session_name})

        if action == "list":
            return jsonify({"sessions": runtime.session_store.list_sessions()})

        if action == "new":
            if not name:
                return jsonify({"error": "name required"}), 400
            deps.mutate_for_new_session(runtime, payload, name)
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
            deleted = deps.delete_session(runtime, name)
            if not deleted:
                return jsonify({"error": "session not found"}), 404
            return jsonify({"ok": True})

        if action == "clear":
            target = name or runtime.session_name
            if target != runtime.session_name:
                loaded = deps.load_session(runtime, target)
                if not loaded:
                    return jsonify({"error": "session not found"}), 404

            deps.mutate_for_clear(runtime, True)
            deps.persist(runtime)
            return jsonify({"ok": True, "session": runtime.session_name})

        if action == "condense":
            w = payload.get("window")
            result = deps.condense_session_context(runtime, window_size=int(w) if w is not None else runtime.condense_window)
            deps.persist(runtime)
            return jsonify(result)

        return jsonify({"error": "unsupported action"}), 400
