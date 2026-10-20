"""The session list says which sessions need the person (a pending prompt), so every client can
mark them ◆ — not only for the session it has open."""

from __future__ import annotations

import json
import os
import threading
import types

from fastapi import FastAPI
from fastapi.testclient import TestClient

import utils.config as config
from mu.gui.prompts import PromptStore
import mu.gui.routers  # noqa: F401  (registers the visibility-filtered list route)
from mu.gui.routers import sessions


def _app(tmp_path, monkeypatch, names):
    monkeypatch.setattr(config, "HISTORY_DIR", str(tmp_path))
    for name in names:
        folder = tmp_path / "sessions" / name
        folder.mkdir(parents=True)
        (folder / "session.json").write_text(json.dumps({"revision": 1, "variables": {}, "history": []}))
    app = FastAPI()
    app.include_router(sessions.router, prefix="/api/sessions")
    app.state.current_session_name = names[0]
    app.state.sessions = {n: object() for n in names}
    app.state.session_busy = {n: threading.Event() for n in names}
    app.state.bus = types.SimpleNamespace(outcomes={})
    app.state.prompts = PromptStore()
    return app


def test_sessions_with_a_pending_prompt_are_awaiting_input(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, ["alpha", "beta", "gamma"])
    app.state.prompts.open({"kind": "tool_approval", "session_name": "beta"})
    body = TestClient(app).get("/api/sessions").json()
    flags = {s["name"]: s["awaiting_input"] for s in body["sessions"]}
    assert flags == {"alpha": False, "beta": True, "gamma": False}
    assert body["awaiting_input"] == ["beta"]


def test_answering_the_prompt_clears_the_flag(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, ["alpha", "beta"])
    prompt_id, _ = app.state.prompts.open({"kind": "choice", "session_name": "alpha"})
    client = TestClient(app)
    assert client.get("/api/sessions").json()["awaiting_input"] == ["alpha"]
    app.state.prompts.answer(prompt_id, {"value": "yes"})
    body = client.get("/api/sessions").json()
    assert body["awaiting_input"] == []
    assert not any(s["awaiting_input"] for s in body["sessions"])
