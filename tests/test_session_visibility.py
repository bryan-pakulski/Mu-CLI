from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from mu.gui.routers import sessions
from mu.gui.routers.session_visibility import list_user_sessions
from mu.session.visibility import is_durable_job_session, is_user_visible_session


class _Flag:
    def is_set(self):
        return False


def _write_session(root, name: str, variables: dict | None = None):
    session_dir = root / "sessions" / name
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "session.json").write_text(
        json.dumps({"variables": variables or {"session_type": "workspace"}}),
        encoding="utf-8",
    )


def test_durable_job_visibility_uses_marker_with_narrow_legacy_fallback():
    assert is_durable_job_session("normal", {"variables": {"durable_job_id": "abc"}})
    assert is_durable_job_session("job-0123456789abcdefabcd", {})
    assert not is_durable_job_session("job-notes", {})
    assert is_user_visible_session("job-notes", {})


def test_sessions_router_has_one_user_visible_list_endpoint():
    root_gets = [
        route
        for route in sessions.router.routes
        if getattr(route, "path", "") in {"", "/"}
        and "GET" in (getattr(route, "methods", set()) or set())
    ]
    assert len(root_gets) == 1
    assert root_gets[0].endpoint is list_user_sessions


def test_session_api_hides_durable_job_execution_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.config.HISTORY_DIR", str(tmp_path))
    _write_session(tmp_path, "customer-work")
    _write_session(
        tmp_path,
        "job-0123456789abcdefabcd",
        {
            "session_type": "workspace",
            "durable_job_id": "0123456789abcdefabcd9999",
        },
    )
    # Marker wins even when the logical name does not use the legacy pattern.
    _write_session(
        tmp_path,
        "internal-runtime",
        {"session_type": "workspace", "internal_session_kind": "durable_job"},
    )

    state = SimpleNamespace(
        current_session_name="customer-work",
        sessions={},
        session_busy={
            "customer-work": _Flag(),
            "job-0123456789abcdefabcd": _Flag(),
        },
    )
    request = SimpleNamespace(app=SimpleNamespace(state=state))

    payload = asyncio.run(list_user_sessions(request))

    assert payload["current"] == "customer-work"
    assert [item["name"] for item in payload["sessions"]] == ["customer-work"]
    assert "job-0123456789abcdefabcd" not in payload["loaded"]
    assert "job-0123456789abcdefabcd" not in payload["busy"]


def test_hidden_internal_current_session_is_not_exposed_as_user_active(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.config.HISTORY_DIR", str(tmp_path))
    hidden = "job-0123456789abcdefabcd"
    _write_session(
        tmp_path,
        hidden,
        {"session_type": "workspace", "durable_job_id": "0123456789abcdefabcd"},
    )
    state = SimpleNamespace(
        current_session_name=hidden,
        sessions={},
        session_busy={},
    )
    request = SimpleNamespace(app=SimpleNamespace(state=state))

    payload = asyncio.run(list_user_sessions(request))

    assert payload["current"] is None
    assert payload["active"] is False
    assert payload["sessions"] == []
