"""GUI must surface an unreadable session.json, never demote it to workspace."""

from __future__ import annotations

import asyncio
import json
import os

import pytest
from fastapi import HTTPException

from mu.gui.routers import chat, sessions
from mu.session.manager import SessionManager, SessionUnreadableError


def _doc(home, name, variables):
    d = home / "sessions" / name
    d.mkdir(parents=True)
    p = d / "session.json"
    p.write_text(json.dumps({"history": [], "variables": variables, "revision": 3}))
    return p


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permissions")
def test_read_session_data_unreadable_is_503_not_none(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions._config, "HISTORY_DIR", str(tmp_path))
    p = _doc(tmp_path, "boxed", {"session_type": "container"})
    before = p.read_bytes()
    p.chmod(0)
    try:
        with pytest.raises(HTTPException) as ei:
            sessions._read_session_data("boxed")
    finally:
        p.chmod(0o600)
    assert ei.value.status_code == 503
    assert "permissions" in ei.value.detail
    assert "chown" in ei.value.detail
    assert p.read_bytes() == before


def test_read_session_data_missing_is_still_none(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions._config, "HISTORY_DIR", str(tmp_path))
    assert sessions._read_session_data("nope") is None


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permissions")
def test_reload_after_container_turn_keeps_container_type(tmp_path, monkeypatch):
    """chat.py post-turn reload: unreadable doc must not demote in-memory type."""
    monkeypatch.setattr("utils.config.HISTORY_DIR", str(tmp_path))
    p = _doc(tmp_path, "boxed", {"session_type": "container", "yolo": True})
    sm = SessionManager(session_name="boxed")
    assert sm.variables["session_type"] == "container"
    sm.container_config = {"container_name": "mucli-boxed"}
    p.chmod(0)
    try:
        with pytest.raises(SessionUnreadableError):
            sm._load_session("boxed")
    finally:
        p.chmod(0o600)
    # in-memory routing state survives; a later save refuses rather than clobbers
    assert sm.variables["session_type"] == "container"
    assert sm.variables["yolo"] is True
    assert sm.container_config == {"container_name": "mucli-boxed"}
    with pytest.raises(SessionUnreadableError):
        sm.save_history()


def test_publish_unreadable_session_emits_error_event():
    events = []

    class Bus:
        async def publish(self, ev):
            events.append(ev)

    exc = SessionUnreadableError(13, "nope", "/x/session.json")
    asyncio.run(chat._publish_unreadable_session(Bus(), "boxed", exc))
    assert len(events) == 1
    ev = events[0]
    assert ev["kind"] == "error" and ev["session_name"] == "boxed"
    assert "permissions" in ev["text"] and "not be saved over" in ev["text"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permissions")
def test_summarize_flags_unreadable_card(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions._config, "HISTORY_DIR", str(tmp_path))
    p = _doc(tmp_path, "boxed", {"session_type": "container"})
    p.chmod(0)
    try:
        card = sessions._summarize(str(p), current=None, loaded=set(), busy_names=set())
    finally:
        p.chmod(0o600)
    assert card["name"] == "boxed"
    assert card["unreadable"] is True
