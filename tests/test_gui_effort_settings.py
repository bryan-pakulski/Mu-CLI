"""Effort and the provider speed settings are grouped, carry their allowed values, and are
validated by the settings API (GUI + mobile)."""
import asyncio
import threading
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from mu.gui.routers import inspector, providers
from utils.config import DEFAULT_VARIABLES


def _request(session):
    lock = threading.Lock()
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(session_by_name=lambda *a: session,
                                                                     session_lock_for=lambda *a: lock)))


def _session():
    return SimpleNamespace(variables=dict(DEFAULT_VARIABLES),
                           session_manager=SimpleNamespace(save_history=lambda *a: None,
                                                           provider_config={"provider": "ollama", "model": "qwen3.8:27b"}),
                           folder_context=None)


def test_speed_settings_are_grouped_with_their_options():
    data = asyncio.run(inspector.list_variables(_request(_session())))
    groups = {g["name"]: {v["key"]: v for v in g["variables"]} for g in data["groups"]}
    assert groups["speed & effort"]["reasoning_effort"]["options"][:3] == ["", "none", "minimal"]
    assert "ollama_num_ctx_default" in groups["ollama"] and "ollama_side_model" in groups["ollama"]
    assert groups["openai"]["openai_api"]["options"] == ["auto", "chat", "responses"]
    assert "reasoning_effort" not in groups.get("other", {})


def test_options_are_validated_on_set():
    session = _session()
    result = asyncio.run(inspector.set_variable("reasoning_effort", _request(session), {"value": "low"}, session=session))
    assert result["value"] == "low" and session.variables["reasoning_effort"] == "low"
    with pytest.raises(HTTPException) as info:
        asyncio.run(inspector.set_variable("reasoning_effort", _request(session), {"value": "turbo"}, session=session))
    assert info.value.status_code == 400


def test_current_provider_reports_effort():
    session = _session()
    session.variables["reasoning_effort"] = "high"
    data = asyncio.run(providers.current_provider(_request(session)))
    assert data["effort"] == "high" and "max" in data["effort_levels"]
