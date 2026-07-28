from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from mu.gui.routers import sessions


class _FakeManager:
    def __init__(self):
        self.container_config = None
        self.saved = False

    def save_history(self, _folder_context):
        self.saved = True


class _FakeSession:
    def __init__(self):
        self.session_manager = _FakeManager()
        self.variables = {}
        self.folder_context = SimpleNamespace(folders=[])
        self.container_ref = None
        self.synced = False

    def sync_runtime_state(self):
        self.synced = True


class _FakeRef:
    name = "mucli-demo"

    def to_dict(self, *, include_secret=True):
        return {"name": self.name, "include_secret": include_secret}


def test_create_container_session_persists_configuration_without_starting(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions._config, "HISTORY_DIR", str(tmp_path))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    result = asyncio.run(
        sessions.create_session(
            request,
            {
                "name": "demo",
                "provider": "openai",
                "model": "gpt-test",
                "session_type": "container",
                "activate": False,
                "container_name": "demo-worker",
                "mounts": [
                    {
                        "host_path": "/tmp",
                        "container_path": "/mnt/tmp",
                        "mode": "ro",
                    }
                ],
                "egress_allow": ["api.openai.com"],
            },
        )
    )

    assert result["session_type"] == "container"
    path = tmp_path / "sessions" / "demo" / "session.json"
    payload = json.loads(path.read_text())
    assert payload["variables"]["session_type"] == "container"
    assert payload["variables"]["yolo"] is True
    assert payload["container_config"]["container_name"] == "demo-worker"
    assert payload["container_config"]["mounts"][0]["mode"] == "ro"


def test_load_workspace_session_uses_empty_container_configuration(monkeypatch):
    session = _FakeSession()
    state = SimpleNamespace(
        sessions={},
        current_session_name=None,
        port=30311,
        container_supervisor=SimpleNamespace(),
    )

    def load_session(*, name, provider, model):
        assert (name, provider, model) == ("demo", "openai", "gpt-test")
        state.sessions[name] = session

    state.load_session = load_session
    state.session_by_name = lambda name: state.sessions.get(name)
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    monkeypatch.setattr(
        sessions,
        "_read_session_data",
        lambda _name: {
            "provider_config": {"provider": "openai", "model": "gpt-test"},
            "variables": {"session_type": "workspace"},
        },
    )

    result = asyncio.run(sessions.load_session("demo", request, payload={}))

    assert result["session_type"] == "workspace"
    assert session.session_manager.container_config == {}
    assert session.session_manager.saved is True
    assert session.synced is True


def test_load_container_session_restores_container_configuration(monkeypatch):
    session = _FakeSession()
    ref = _FakeRef()
    recorded = {}

    def create(**kwargs):
        recorded.update(kwargs)
        return ref

    state = SimpleNamespace(
        sessions={},
        current_session_name=None,
        port=30311,
        container_supervisor=SimpleNamespace(create=create),
    )

    def load_session(*, name, provider, model):
        state.sessions[name] = session

    state.load_session = load_session
    state.session_by_name = lambda name: state.sessions.get(name)
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    config = {
        "container_name": "demo-worker",
        "dockerfile": "FROM ubuntu:24.04",
        "template_name": "python-tools",
        "mounts": [],
        "egress_allow": ["api.openai.com"],
        "egress_deny": [],
    }
    monkeypatch.setattr(
        sessions,
        "_read_session_data",
        lambda _name: {
            "provider_config": {"provider": "openai", "model": "gpt-test"},
            "variables": {"session_type": "container"},
            "container_config": config,
        },
    )

    result = asyncio.run(sessions.load_session("demo", request, payload={}))

    assert recorded["container_name"] == "demo-worker"
    assert recorded["session_name"] == "demo"
    assert recorded["template_name"] == "python-tools"
    assert session.session_manager.container_config == config
    assert session.container_ref is ref
    assert result["container"]["name"] == "mucli-demo"


def test_container_creation_status_round_trip():
    import threading

    state = SimpleNamespace(
        container_creation_status={},
        container_creation_lock=threading.Lock(),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=state))

    sessions._set_container_creation_status(
        request,
        "demo",
        stage="building_image",
        message="Building image…",
    )
    result = asyncio.run(sessions.container_creation_status("demo", request))

    assert result["state"] == "running"
    assert result["stage"] == "building_image"
    assert result["message"] == "Building image…"


def test_create_container_session_reports_progress(tmp_path, monkeypatch):
    import threading

    monkeypatch.setattr(sessions._config, "HISTORY_DIR", str(tmp_path))
    ref = _FakeRef()
    recorded_stages = []

    def create(**kwargs):
        progress = kwargs["progress"]
        progress("building_image", "Building image…")
        recorded_stages.append("building_image")
        progress("starting_worker", "Starting worker…")
        recorded_stages.append("starting_worker")
        return ref

    supervisor = SimpleNamespace(
        create=create,
        container_for_session=lambda _name: ref,
    )
    state = SimpleNamespace(
        port=30311,
        container_supervisor=supervisor,
        container_creation_status={},
        container_creation_lock=threading.Lock(),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=state))

    async def fake_load_session(name, request, payload=None):
        assert name == "demo"
        assert payload == {"provider": "openai", "model": "gpt-test"}
        return {"ok": True, "name": name, "active": True}

    monkeypatch.setattr(sessions, "load_session", fake_load_session)

    result = asyncio.run(
        sessions.create_session(
            request,
            {
                "name": "demo",
                "provider": "openai",
                "model": "gpt-test",
                "session_type": "container",
                "container_name": "demo-worker",
            },
        )
    )

    assert recorded_stages == ["building_image", "starting_worker"]
    assert result["session_type"] == "container"
    assert state.container_creation_status["demo"]["state"] == "ready"
    assert state.container_creation_status["demo"]["stage"] == "ready"


def test_creation_status_returns_incremental_output():
    import threading

    state = SimpleNamespace(
        container_creation_status={},
        container_creation_lock=threading.Lock(),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    sessions._set_container_creation_status(
        request,
        "demo",
        stage="building_image",
        message="Building image…",
        reset_output=True,
    )
    sessions._append_container_creation_output(request, "demo", "stdout", "step one")
    sessions._append_container_creation_output(request, "demo", "stderr", "step two")

    full = asyncio.run(sessions.container_creation_status("demo", request, after=0))
    incremental = asyncio.run(sessions.container_creation_status("demo", request, after=1))

    assert [item["text"] for item in full["logs"]] == ["step one", "step two"]
    assert [item["text"] for item in incremental["logs"]] == ["step two"]


def test_background_container_creation_returns_before_build_finishes(tmp_path, monkeypatch):
    import threading

    monkeypatch.setattr(sessions._config, "HISTORY_DIR", str(tmp_path))
    ref = _FakeRef()
    completed = threading.Event()

    def create(**kwargs):
        kwargs["progress"]("building_image", "Building image…")
        kwargs["output"]("stdout", "docker build output")
        completed.set()
        return ref

    supervisor = SimpleNamespace(
        create=create,
        container_for_session=lambda _name: ref,
    )
    state = SimpleNamespace(
        port=30311,
        container_supervisor=supervisor,
        container_creation_status={},
        container_creation_lock=threading.Lock(),
        container_creation_tasks={},
    )
    request = SimpleNamespace(app=SimpleNamespace(state=state))

    async def fake_load_session(name, request, payload=None):
        return {"ok": True, "name": name, "active": True}

    monkeypatch.setattr(sessions, "load_session", fake_load_session)

    async def exercise():
        response = await sessions.create_session(
            request,
            {
                "name": "demo",
                "provider": "openai",
                "model": "gpt-test",
                "session_type": "container",
                "container_name": "demo-worker",
                "background_container": True,
            },
        )
        assert response.status_code == 202
        for _ in range(100):
            status = sessions._get_container_creation_status(request, "demo")
            if status["state"] in {"ready", "error"}:
                return status
            await asyncio.sleep(0.01)
        raise AssertionError("background creation did not finish")

    status = asyncio.run(exercise())
    assert completed.is_set()
    assert status["state"] == "ready"
    assert any(item["text"] == "docker build output" for item in status["logs"])
