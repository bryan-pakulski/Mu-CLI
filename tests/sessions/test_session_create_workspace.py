"""Creating a workspace session whose folder doesn't exist yet: with ``create_workspace`` (the
mobile New session sheet) the folder is created; without it the request still fails."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def http(tmp_path, monkeypatch):
    import utils.config
    monkeypatch.setattr(utils.config, "HISTORY_DIR", str(tmp_path / "home"))
    from mu.gui.app import create_app
    app = create_app(args=SimpleNamespace(workspace=None, session=None, provider=None, model=None),
                     build_session_fn=lambda **_: None, port=30394)
    return TestClient(app)


def _create(http, name, workspace, **extra):
    return http.post("/api/sessions", json={"name": name, "provider": "ollama", "model": "small", "activate": False,
                                            "session_type": "workspace", "workspace": workspace, **extra})


def test_missing_folder_is_created_when_asked(http, tmp_path):
    target = tmp_path / "src" / "new-project"
    response = _create(http, "fresh", str(target), create_workspace=True)
    assert response.status_code == 200, response.text
    assert target.is_dir()
    saved = json.loads((tmp_path / "home" / "sessions" / "fresh" / "session.json").read_text())
    assert saved["folder_context"]["folders"] == [str(target)]


def test_missing_folder_still_fails_without_the_flag(http, tmp_path):
    target = tmp_path / "nope"
    response = _create(http, "plain", str(target))
    assert response.status_code == 400 and "not a directory" in response.json()["detail"]
    assert not target.exists()


@pytest.mark.parametrize("make, message", [
    (lambda p: p.write_text("a file"), "not a directory"),   # exists but is not a folder
    (lambda p: None, "must be absolute"),                     # relative path
])
def test_unsafe_paths_are_never_created(http, tmp_path, make, message, monkeypatch):
    target = tmp_path / "thing"
    make(target)
    monkeypatch.chdir(tmp_path)
    path = str(target) if target.exists() else "relative/folder"
    response = _create(http, "bad", path, create_workspace=True)
    assert response.status_code == 400 and message in response.json()["detail"]
    assert not (tmp_path / "relative").exists()


def test_secret_paths_are_never_created(http, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))  # the denylist is home-relative (~/.ssh)
    target = tmp_path / ".ssh" / "work"
    response = _create(http, "secret", str(target), create_workspace=True)
    assert response.status_code == 400 and "not allowed" in response.json()["detail"]
    assert not target.exists()


def test_failed_creation_leaves_no_folder(http, tmp_path):
    # A non-workspace session never creates folders, even with the flag.
    target = tmp_path / "chat-folder"
    response = http.post("/api/sessions", json={"name": "c1", "provider": "ollama", "model": "small", "activate": False,
                                                "session_type": "container", "container_source": "bogus",
                                                "workspace": str(target), "create_workspace": True})
    assert response.status_code == 400
    assert not target.exists()
