"""A command the user runs from the GUI (`/status`) is not a chat turn: the server never echoes it as a
user message or starts a model turn; clients get ``command_started`` then ``command_result``."""

import threading
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mu.gui.routers import chat as chat_router


class _Bus:
    def __init__(self):
        self.events = []

    async def publish(self, event):
        self.events.append(event)


def _client(monkeypatch):
    import mucli

    session = SimpleNamespace(
        session_manager=SimpleNamespace(current_session_name="alpha"),
        variables={"session_type": "workspace"},
        stage_attachment_ids=lambda ids: None,
    )
    turns = []
    monkeypatch.setattr(mucli, "handle_command", lambda s, text, allow_prompt=True: {"ok": True, "command": "/status", "message": "idle", "data": {}})

    async def _launch(app, s, text, attachments=None):
        turns.append(text)
        return {"accepted": True, "kind": "turn"}

    monkeypatch.setattr(chat_router, "launch_turn", _launch)
    app = FastAPI()
    app.state.bus = _Bus()
    busy = threading.Event()
    app.state.session_busy_for = lambda name=None: busy
    app.state.session_lock_for = lambda name=None: threading.Lock()
    app.state.session_by_name = lambda name=None: session
    app.include_router(chat_router.router, prefix="/api/chat")
    return TestClient(app), app.state.bus, turns, busy


def test_a_command_is_announced_as_a_command_and_never_starts_a_turn(monkeypatch):
    client, bus, turns, busy = _client(monkeypatch)
    busy.set()  # a command may run while the agent works
    resp = client.post("/api/chat/send", json={"text": "/status", "session_name": "alpha"})
    assert resp.status_code == 200 and resp.json()["kind"] == "command"
    assert [e["kind"] for e in bus.events] == ["command_started", "command_result"]
    assert bus.events[0]["text"] == bus.events[1]["text"] == "/status"
    assert turns == []


def test_chat_text_still_echoes_the_user_message_and_starts_a_turn(monkeypatch):
    client, bus, turns, _ = _client(monkeypatch)
    client.post("/api/chat/send", json={"text": "hello", "session_name": "alpha"})
    assert [e["kind"] for e in bus.events] == ["user_message"]
    assert turns == ["hello"]
