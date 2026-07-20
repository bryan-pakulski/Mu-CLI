"""GUI wiring for first-class local/cloud Ollama selection.

Covers the provider discovery + switch endpoints (mode/key threading) and
the static wiring of the local/cloud toggle + API-key field in the
settings popout and the welcome/new-session modal.
"""

import asyncio
import os
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mu.gui.routers import providers as providers_mod


APP_JS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "mu", "gui", "static", "js", "app.js"
)
CHAT_HTML_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "mu", "gui", "templates", "fragments", "chat.html",
)
WELCOME_HTML_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "mu", "gui", "templates", "fragments", "welcome.html",
)
CSS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "mu", "gui", "static", "css", "app.css"
)


# ----------------------------------------- discovery override priority (unit)


class _FakeQueryParams(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _fake_request(query=None, session_vars=None):
    state = SimpleNamespace()
    if session_vars is not None:
        sess = SimpleNamespace(variables=session_vars)
        state.session_by_name = lambda name=None: sess
    else:
        state.session_by_name = lambda name=None: None
    return SimpleNamespace(
        query_params=_FakeQueryParams(query or {}),
        app=SimpleNamespace(state=state),
    )


def test_discovery_overrides_query_param_wins_over_session():
    req = _fake_request(
        query={"ollama_mode": "cloud", "ollama_api_key": "qkey"},
        session_vars={"ollama_mode": "local", "ollama_api_key": "sesskey"},
    )
    out = providers_mod._ollama_discovery_overrides(req)
    assert out == {"ollama_mode": "cloud", "ollama_api_key": "qkey"}


def test_discovery_overrides_falls_back_to_session_vars():
    req = _fake_request(
        query={},
        session_vars={"ollama_mode": "cloud", "ollama_api_key": "sesskey", "ollama_host": ""},
    )
    out = providers_mod._ollama_discovery_overrides(req)
    assert out["ollama_mode"] == "cloud"
    assert out["ollama_api_key"] == "sesskey"


def test_discovery_overrides_empty_when_nothing_set():
    req = _fake_request(query={}, session_vars=None)
    assert providers_mod._ollama_discovery_overrides(req) == {}


def test_current_provider_reports_environment_key_presence(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "environment-secret")
    session = SimpleNamespace(
        variables={"ollama_api_key": ""},
        session_manager=SimpleNamespace(provider_config={"provider": "ollama", "model": "cloud-model"}),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(session_by_name=lambda: session)))
    result = asyncio.run(providers_mod.current_provider(request))

    assert result["ollama_api_key_set"] is True
    assert "environment-secret" not in str(result)


def test_discovery_overrides_ignores_empty_session_values():
    """Empty/None session variable values must not pollute the overrides
    (so a fresh local session with ollama_mode='auto' doesn't force a
    mode on discovery — the provider resolves from env)."""
    req = _fake_request(
        query={},
        session_vars={"ollama_mode": "auto", "ollama_host": "", "ollama_api_key": ""},
    )
    out = providers_mod._ollama_discovery_overrides(req)
    # 'auto' is truthy and would be forwarded, but empty host/key drop.
    assert out.get("ollama_api_key") is None
    assert out.get("ollama_host") is None


# ----------------------------------------- endpoint threading (TestClient)


def _make_providers_app(session_vars=None):
    app = FastAPI()
    app.state.sessions = {}
    app.state.session_locks = {}
    app.state.current_session_name = None
    app.state._fallback_lock = threading.Lock()
    from mu.gui.app import session_by_name

    if session_vars is not None:
        sess = SimpleNamespace(variables=dict(session_vars))
        app.state.sessions["s1"] = sess
        app.state.current_session_name = "s1"
    app.state.session_by_name = lambda name=None: session_by_name(app, name)
    app.include_router(providers_mod.router, prefix="/api/providers")
    return app


def test_list_models_threads_query_overrides_to_safe_init(monkeypatch):
    captured = {}

    def fake_safe_init(name, model_name="", ollama_host=None, ollama_mode=None, ollama_api_key=None):
        captured.update(name=name, ollama_host=ollama_host, ollama_mode=ollama_mode, ollama_api_key=ollama_api_key)
        stub = SimpleNamespace(
            get_available_models=lambda: ["llama3", "qwen2"],
        )
        return stub

    monkeypatch.setattr(providers_mod, "_safe_init", fake_safe_init)
    app = _make_providers_app()
    client = TestClient(app)
    r = client.get("/api/providers/ollama/models", params={"ollama_mode": "cloud", "ollama_api_key": "k"})
    assert r.status_code == 200
    assert r.json()["models"] == ["llama3", "qwen2"]
    assert captured["ollama_mode"] == "cloud"
    assert captured["ollama_api_key"] == "k"


def test_list_models_falls_back_to_session_vars(monkeypatch):
    captured = {}

    def fake_safe_init(name, model_name="", ollama_host=None, ollama_mode=None, ollama_api_key=None):
        captured.update(ollama_mode=ollama_mode, ollama_api_key=ollama_api_key)
        return SimpleNamespace(get_available_models=lambda: ["m1"])

    monkeypatch.setattr(providers_mod, "_safe_init", fake_safe_init)
    app = _make_providers_app(
        session_vars={"ollama_mode": "cloud", "ollama_api_key": "sesskey", "ollama_host": ""}
    )
    client = TestClient(app)
    r = client.get("/api/providers/ollama/models")
    assert r.status_code == 200
    assert captured["ollama_mode"] == "cloud"
    assert captured["ollama_api_key"] == "sesskey"


def test_switch_carries_mode_and_key_and_persists(monkeypatch):
    """POST /switch with ollama_mode/ollama_api_key seeds them onto the
    session variables, syncs the provider, and persists."""
    saved = {}

    def fake_safe_init(name, model_name="", ollama_host=None, ollama_mode=None, ollama_api_key=None):
        return SimpleNamespace(name=name, model_name=model_name)

    monkeypatch.setattr(providers_mod, "_safe_init", fake_safe_init)

    sync_calls = []
    monkeypatch.setattr(
        "mucli.sync_provider_settings", lambda session: sync_calls.append(session), raising=False
    )

    sess = SimpleNamespace(
        provider=SimpleNamespace(name="ollama", model_name="old"),
        variables={"ollama_mode": "auto", "ollama_host": "", "ollama_api_key": ""},
        folder_context=SimpleNamespace(folders=[]),
    )
    sess.session_manager = SimpleNamespace(
        provider_config={"provider": "ollama", "model": "old"},
        save_history=lambda fc=None: saved.setdefault("saved", True),
    )

    app = _make_providers_app()
    app.state.sessions["s1"] = sess
    app.state.current_session_name = "s1"

    client = TestClient(app)
    r = client.post(
        "/api/providers/switch",
        json={
            "provider": "ollama",
            "model": "llama3",
            "ollama_mode": "cloud",
            "ollama_api_key": "newkey",
        },
    )
    assert r.status_code == 200
    assert sess.variables["ollama_mode"] == "cloud"
    assert sess.variables["ollama_api_key"] == "newkey"
    assert sync_calls, "sync_provider_settings should have been called"
    assert saved.get("saved") is True


# ----------------------------------------- static-content wiring


def test_inspector_store_has_ollama_mode_and_key_state():
    with open(APP_JS_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    assert "ollamaMode" in content
    assert "ollamaApiKey" in content
    assert "ollamaKeySet" in content
    assert "onOllamaModeChange" in content
    assert "onOllamaKeyChange" in content
    assert "_loadOllamaState" in content
    assert "_ollamaDiscoveryParams" in content


def test_chat_settings_has_local_cloud_toggle_and_key_field():
    with open(CHAT_HTML_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    assert "ollama-mode-toggle" in content
    assert "ollama-mode-btn" in content
    assert "$store.inspector.onOllamaModeChange('local')" in content
    assert "$store.inspector.onOllamaModeChange('cloud')" in content
    # Key field shown only for cloud.
    assert "ollama-key-input" in content
    assert "$store.inspector.onOllamaKeyChange()" in content
    # The toggle row is gated on ollama being the active provider.
    assert "$store.inspector.currentProvider === 'ollama'" in content


def test_welcome_modal_has_local_cloud_toggle_and_key_field():
    with open(WELCOME_HTML_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    assert "form.ollamaMode" in content
    assert "setOllamaMode" in content
    assert "form.ollamaApiKey" in content
    assert "ollama_mode" in content  # sent in createSession body
    assert "ollama_api_key" in content
    assert "ollamaKeySet" in content
    assert "key available — type to replace" in content


def test_css_has_toggle_styles():
    with open(CSS_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    assert ".ollama-mode-toggle" in content
    assert ".ollama-mode-btn" in content
    assert ".ollama-mode-btn.active" in content
    assert ".ollama-key-input" in content
