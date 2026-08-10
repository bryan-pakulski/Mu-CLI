from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import utils.config as config
from mu.gui.routers.session_history import get_authoritative_history


def _request_with_live_history(name: str, history: list[dict]):
    manager = SimpleNamespace(current_session_name=name, history=history)
    session = SimpleNamespace(session_manager=manager)
    state = SimpleNamespace(session_by_name=lambda requested=None: session)
    return SimpleNamespace(app=SimpleNamespace(state=state))


def _history(text: str):
    return [
        {
            "role": "user",
            "parts": [{"type": "text", "text": text}],
        },
        {
            "role": "assistant",
            "parts": [{"type": "text", "text": f"reply to {text}"}],
        },
    ]


def test_named_gui_history_uses_saved_session_when_live_copy_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HISTORY_DIR", str(tmp_path))
    name = "saved-session"
    session_dir = tmp_path / "sessions" / name
    session_dir.mkdir(parents=True)
    with (session_dir / "session.json").open("w", encoding="utf-8") as handle:
        json.dump({"history": _history("persisted prompt")}, handle)

    request = _request_with_live_history(name, [])
    payload = asyncio.run(
        get_authoritative_history(
            request,
            session_name=name,
            limit_turns=None,
            artifact_limit=None,
            before_index=None,
        )
    )

    assert payload["name"] == name
    assert payload["history_source"] == "durable_session"
    assert payload["total_turns"] == 2
    assert payload["turns"][0]["parts"][0]["text"] == "persisted prompt"
    assert payload["turns"][1]["parts"][0]["text"] == "reply to persisted prompt"


def test_unsaved_history_request_can_still_use_live_session(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HISTORY_DIR", str(tmp_path))
    name = "live-session"
    request = _request_with_live_history(name, _history("live prompt"))

    payload = asyncio.run(
        get_authoritative_history(
            request,
            session_name=name,
            limit_turns=None,
            artifact_limit=None,
            before_index=None,
        )
    )

    assert payload["history_source"] == "live_session"
    assert payload["total_turns"] == 2
    assert payload["turns"][0]["parts"][0]["text"] == "live prompt"
