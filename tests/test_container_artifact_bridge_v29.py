import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mu.artifact import ArtifactRegistry
from mu.gui.routers import containers as containers_router
from mu.session import tools_glue
from mu.tools.artifact.handlers import upload_artifact_tool


class _Bus:
    def __init__(self):
        self.events = []

    async def publish(self, event):
        self.events.append(dict(event))


class _Supervisor:
    def validate_token(self, container_name, token):
        return container_name == "mucli-demo" and token == "secret"

    def container_for_session(self, session_name):
        if session_name == "demo":
            return SimpleNamespace(name="mucli-demo")
        return None


def test_worker_artifact_is_persisted_in_host_registry(tmp_path, monkeypatch):
    history = tmp_path / ".mucli"
    session_dir = history / "sessions" / "demo"
    session_dir.mkdir(parents=True)
    monkeypatch.setattr(containers_router._config, "HISTORY_DIR", str(history))

    app = FastAPI()
    app.state.container_supervisor = _Supervisor()
    app.state.bus = _Bus()
    app.include_router(containers_router.router)

    client = TestClient(app)
    response = client.post(
        "/api/container-worker/artifacts",
        params={
            "session_name": "demo",
            "container_name": "mucli-demo",
            "name": "report.md",
            "mime_type": "text/markdown",
        },
        headers={"X-MuCLI-Worker-Token": "secret"},
        content=b"# report\n",
    )
    assert response.status_code == 200, response.text
    artifact = response.json()["artifact"]
    assert artifact["name"] == "report.md"
    assert ArtifactRegistry(str(session_dir)).list()[0]["artifact_id"] == artifact["artifact_id"]
    assert app.state.bus.events == [
        {
            "kind": "artifact_created",
            "artifact": artifact,
            "session_name": "demo",
        }
    ]


def test_container_upload_tool_uses_host_bridge():
    descriptor = {
        "artifact_id": "abc123",
        "name": "environment-report.md",
        "size": 8,
        "mime_type": "text/markdown",
        "download_url": "/download",
    }

    class UI:
        def __init__(self):
            self.calls = []

        def publish_artifact(self, **kwargs):
            self.calls.append(kwargs)
            return descriptor

    ui = UI()
    session = SimpleNamespace(
        variables={"session_type": "container"},
        ui=ui,
    )
    context = SimpleNamespace(
        session=session,
        variables=session.variables,
        ui=ui,
    )
    result = json.loads(
        upload_artifact_tool(
            {
                "name": "environment-report.md",
                "content": "contents",
                "mime_type": "text/markdown",
            },
            context,
        )
    )
    assert result["ok"] is True
    assert result["artifacts"] == [descriptor]
    assert ui.calls[0]["content"] == "contents"


def test_worker_bridge_source_forwards_visualization_timeline_anchor():
    root = Path(__file__).resolve().parents[1]
    source = (root / "mu/container/worker.py").read_text(encoding="utf-8")
    endpoint = (root / "mu/gui/routers/containers.py").read_text(encoding="utf-8")

    for field in (
        "timeline_turn_id",
        "timeline_history_index",
        "timeline_part_index",
    ):
        assert f'params["{field}"]' in source
        assert f"{field}={field}" in endpoint


def test_structured_tool_result_preserves_artifacts(monkeypatch):
    artifact = {"artifact_id": "abc123", "name": "report.md", "size": 3}
    envelope = {
        "ok": True,
        "error_code": None,
        "message": "Artifact ready: report.md",
        "data": {},
        "artifact": artifact,
        "artifacts": [artifact],
        "telemetry": {"tool_name": "upload_artifact"},
    }

    class Session:
        variables = {}

        @staticmethod
        def _unwrap_tool_envelope(raw):
            return json.loads(raw), "Artifact ready: report.md"

    monkeypatch.setattr(tools_glue, "_apply_observation_transform", lambda *args: None)
    result = tools_glue.build_structured_tool_result(
        Session(),
        "upload_artifact",
        {"name": "report.md", "content": "abc"},
        json.dumps(envelope),
    )
    assert result["artifacts"] == [artifact]
    assert result["data"]["artifact"] == artifact


def test_web_artifacts_live_in_tools_panel():
    root = Path(__file__).resolve().parents[1]
    config = (root / "utils/config.py").read_text(encoding="utf-8")
    index = (root / "mu/gui/templates/index.html").read_text(encoding="utf-8")
    chat = (root / "mu/gui/templates/fragments/chat.html").read_text(encoding="utf-8")
    panel = (root / "mu/gui/templates/fragments/artifacts_panel.html").read_text(encoding="utf-8")
    app_js = (root / "mu/gui/static/js/app.js").read_text(encoding="utf-8")

    assert '"name": "artifacts"' in config
    assert 'fragments/artifacts_panel.html' in index
    assert 'class="artifact-panel"' not in chat
    assert "data-mode=\"artifacts\"" in panel
    assert '"files", "artifacts"' in app_js
