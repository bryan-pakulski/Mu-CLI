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


def _write_saved_history(tmp_path, name: str, history: list[dict]):
    session_dir = tmp_path / "sessions" / name
    session_dir.mkdir(parents=True, exist_ok=True)
    with (session_dir / "session.json").open("w", encoding="utf-8") as handle:
        json.dump({"history": history}, handle)


def test_named_gui_history_uses_saved_session_when_live_copy_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HISTORY_DIR", str(tmp_path))
    name = "saved-session"
    _write_saved_history(tmp_path, name, _history("persisted prompt"))

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
    assert payload["history_recovered"] is True
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
    assert payload["history_recovered"] is False
    assert payload["total_turns"] == 2
    assert payload["turns"][0]["parts"][0]["text"] == "live prompt"


def test_newer_live_history_wins_over_older_saved_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HISTORY_DIR", str(tmp_path))
    name = "newer-live-session"
    saved = _history("older persisted prompt")
    live = [*saved, *_history("new live prompt")]
    _write_saved_history(tmp_path, name, saved)
    request = _request_with_live_history(name, live)

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
    assert payload["history_recovered"] is False
    assert payload["total_turns"] == 4
    assert payload["turns"][-2]["parts"][0]["text"] == "new live prompt"
