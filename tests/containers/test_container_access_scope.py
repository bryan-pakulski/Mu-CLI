"""Who may control containers from the GUI: the interactive shell is loopback-only (a client on
the LAN must never get a bash inside a container), while lifecycle APIs also serve private
networks (the mobile client reaches the GUI over the LAN) and refuse public addresses."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from mu.gui.routers import containers


class _Supervisor:
    def __init__(self):
        self.started = []
        self.template_registry = SimpleNamespace(list_templates=lambda: [])

    def list_environments(self):
        return []

    def start(self, name):
        self.started.append(name)
        raise RuntimeError("no docker in this test")


def _client(host: str):
    app = FastAPI()
    app.state.container_supervisor = _Supervisor()
    app.include_router(containers.router)
    return TestClient(app, client=(host, 50000)), app.state.container_supervisor


@pytest.mark.parametrize("host", ["192.168.1.20", "10.0.0.7", "fd00::5", "1.1.1.1", "8.8.8.8"])
def test_the_shell_refuses_every_non_loopback_client_before_accepting(host):
    client, supervisor = _client(host)
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/api/containers/dev/shell") as ws:
            ws.receive_text()
    assert closed.value.code == 1008
    assert supervisor.started == []                       # nothing was started for a refused client


def test_the_shell_accepts_loopback():
    client, supervisor = _client("127.0.0.1")
    with client.websocket_connect("/api/containers/dev/shell") as ws:
        assert "Unable to open shell" in ws.receive_text()  # accepted; the stub has no Docker
    assert supervisor.started == ["dev"]


@pytest.mark.parametrize("host,allowed", [("127.0.0.1", True), ("192.168.1.20", True), ("fd00::5", True),
                                          ("1.1.1.1", False), ("8.8.8.8", False)])
def test_lifecycle_apis_serve_loopback_and_private_networks_only(host, allowed):
    client, _ = _client(host)
    response = client.get("/api/containers")
    assert (response.status_code != 403) is allowed, response.text
