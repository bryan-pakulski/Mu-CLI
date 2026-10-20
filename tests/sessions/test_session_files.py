"""Session files (artifacts the agent publishes, files you upload): delete works from the GUI and
the phone even while the session's turn runs, and concurrent writers to one registry — the GUI
routers, the running session, a container worker — never lose each other's entries."""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from mu.artifact import ArtifactRegistry
from mu.attachment.registry import AttachmentRegistry


@pytest.fixture
def home(tmp_path, monkeypatch):
    session_dir = tmp_path / "sessions" / "s1"
    session_dir.mkdir(parents=True)
    monkeypatch.setattr("mu.gui.routers.artifacts.HISTORY_DIR", str(tmp_path))
    monkeypatch.setattr("mu.gui.routers.attachments.HISTORY_DIR", str(tmp_path))
    return session_dir


@pytest.fixture
def client(home):
    from mu.gui.app import create_app

    app = create_app(args=SimpleNamespace(workspace=None, session=None, provider=None, model=None),
                     build_session_fn=lambda **_: None, port=30395)
    return app, TestClient(app)


def test_concurrent_registry_objects_keep_every_entry(home):
    """Each writer builds its own registry object, as the routers, the session and a worker do."""
    errors = []

    def publish(prefix):
        try:
            for i in range(20):
                ArtifactRegistry(str(home)).add(f"{prefix}-{i}.txt", content=f"{prefix} {i}", mime_type="text/plain")
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=publish, args=(name,)) for name in ("agent", "worker", "gui")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(ArtifactRegistry(str(home)).list()) == 60


def test_artifact_delete_works_during_an_active_turn(client, home):
    app, http = client
    artifact = ArtifactRegistry(str(home)).add("report.txt", content="hello", mime_type="text/plain")
    app.state.session_busy_for("s1").set()  # the session's turn is running
    response = http.delete(f"/api/sessions/s1/artifacts/{artifact['artifact_id']}")
    assert response.status_code == 200, response.text
    assert http.get("/api/sessions/s1/artifacts").json()["artifacts"] == []
    # Deleting again is a clear 404, not a silent success.
    assert http.delete(f"/api/sessions/s1/artifacts/{artifact['artifact_id']}").status_code == 404


def test_upload_delete_works_during_an_active_turn(client, home, tmp_path):
    app, http = client
    source = tmp_path / "notes.md"
    source.write_text("# notes")
    item = AttachmentRegistry(str(home)).add("notes.md", str(source), "text/markdown")
    app.state.session_busy_for("s1").set()
    response = http.delete(f"/api/sessions/s1/attachments/{item['attachment_id']}")
    assert response.status_code == 200, response.text
    assert http.get("/api/sessions/s1/attachments").json()["attachments"] == []
