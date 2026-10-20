"""The fleet chat on the orchestration page: an orchestration session that never takes the main
chat's focus, never appears in session lists, and is temporary (starting a new one deletes the
old one and any other leftover fleet chat)."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from mu.session.visibility import is_fleet_chat_session, is_user_visible_session

from orch_helpers import make_orch


@pytest.mark.parametrize("name,data,hidden", [
    ("fleet-chat-20261020-133431", {}, True),
    ("fleet-chat-20261020-133431-2", {}, True),
    ("anything", {"variables": {"internal_session_kind": "fleet_chat"}}, True),
    ("fleet-chat-notes", {}, False),            # a user's own session with a similar name stays visible
    ("my-session", {"variables": {"session_type": "orchestration"}}, False),   # studio chats stay listed
])
def test_fleet_chats_are_kept_out_of_session_lists(name, data, hidden):
    assert is_fleet_chat_session(name, data) is hidden
    assert is_user_visible_session(name, data) is (not hidden)


def _session_dir(name: str) -> Path:
    from utils import config
    path = Path(config.HISTORY_DIR) / "sessions" / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "session.json").write_text(json.dumps({"variables": {}, "revision": 1}))
    return path


@pytest.fixture
def chat_app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from mu.gui.app import create_app
    from mu.gui.routers import orchestration as router
    from mu.gui.routers import sessions as sessions_router
    orch = make_orch(tmp_path / "o")
    app = create_app(args=SimpleNamespace(provider=None, model=None), build_session_fn=lambda *a, **k: None)
    app.state.orchestrator = orch
    monkeypatch.setattr(router, "_CURSOR", {"seq": -1})
    created, loads = [], []

    async def fake_create(request, payload):
        created.append(dict(payload))
        _session_dir(payload["name"])
        return {"ok": True}
    monkeypatch.setattr(sessions_router, "create_session", fake_create)
    monkeypatch.setattr(router, "_recent_session_model", lambda: ("", ""))

    def load(**kw):
        loads.append(kw)
        session = SimpleNamespace(variables={}, provider=SimpleNamespace(name="anthropic", model_name="m-1"))
        app.state.sessions[kw["name"]] = session
        return session
    app.state.load_session = load
    client = TestClient(app)
    headers = {"X-MuCLI-CSRF": client.get("/api/orchestration/csrf").json()["token"]}
    yield SimpleNamespace(app=app, client=client, headers=headers, orch=orch, created=created, loads=loads)
    orch.stop()


def _start(chat_app, **body):
    r = chat_app.client.post("/api/orchestration/chat/session", json={"provider": "anthropic", "model": "m-1", **body},
                             headers=chat_app.headers)
    assert r.status_code == 200, r.text
    return r.json()["session_name"]


def test_a_new_fleet_chat_deletes_the_old_one_and_leftovers_but_never_user_sessions(chat_app):
    leftover = _session_dir("fleet-chat-20260101-000000")
    mine = _session_dir("fleet-chat-notes")                      # the user's own, despite the name
    first = _start(chat_app)
    assert chat_app.created[0]["session_type"] == "orchestration" and chat_app.created[0]["activate"] is False
    assert chat_app.app.state.sessions[first].variables["internal_session_kind"] == "fleet_chat"
    assert all(kw["focus"] is False for kw in chat_app.loads)    # the main chat keeps its session
    assert not leftover.exists() and mine.exists()
    assert _start(chat_app) == first                               # reopening keeps the conversation
    second = _start(chat_app, fresh=True)
    assert second != first and chat_app.orch.store.get_setting("chat_session") == second
    from utils import config
    assert not (Path(config.HISTORY_DIR) / "sessions" / first).exists()   # the old chat is gone, unloaded first
    assert first not in chat_app.app.state.sessions
    assert (Path(config.HISTORY_DIR) / "sessions" / second).exists() and mine.exists()


def test_a_fleet_chat_with_a_turn_in_flight_is_left_for_later(chat_app):
    first = _start(chat_app)
    chat_app.app.state.session_busy_for(first).set()               # a turn is running
    second = _start(chat_app, fresh=True)
    from utils import config
    assert (Path(config.HISTORY_DIR) / "sessions" / first).exists()   # not deleted mid-turn
    chat_app.app.state.session_busy_for(first).clear()
    assert _start(chat_app) == second
    assert not (Path(config.HISTORY_DIR) / "sessions" / first).exists()   # the next pass removes it
