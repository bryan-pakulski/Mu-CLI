"""Who may control containers from the GUI: this computer (loopback) or a paired device
(mu/gui/devices.py). Being on the local network is not enough: an unpaired client never gets a shell
or a lifecycle action, while a paired phone gets both."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from mu.gui.devices import DeviceGate, DeviceStore
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


@pytest.fixture
def app(tmp_path):
    application = FastAPI()
    application.state.container_supervisor = _Supervisor()
    application.state.devices = DeviceStore(str(tmp_path / "devices.json"))
    application.include_router(containers.router)
    application.add_middleware(DeviceGate)
    return application


def _token(app) -> str:
    store = app.state.devices
    return store.pair(store.start_pairing()["code"], "Pixel")[0]


@pytest.mark.parametrize("host", ["192.168.1.20", "10.0.0.7", "fd00::5", "1.1.1.1", "8.8.8.8"])
def test_the_shell_refuses_unpaired_network_clients_before_accepting(app, host):
    client = TestClient(app, client=(host, 50000))
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/api/containers/dev/shell") as ws:
            ws.receive_text()
    assert closed.value.code == 1008
    assert app.state.container_supervisor.started == []           # nothing started for a refused client


@pytest.mark.parametrize("host,paired", [("127.0.0.1", False), ("192.168.1.20", True)])
def test_the_shell_accepts_loopback_and_paired_devices(app, host, paired):
    headers = {"Authorization": f"Bearer {_token(app)}"} if paired else {}
    client = TestClient(app, client=(host, 50000))
    with client.websocket_connect("/api/containers/dev/shell", headers=headers) as ws:
        assert "Unable to open shell" in ws.receive_text()        # accepted; the stub has no Docker
    assert app.state.container_supervisor.started == ["dev"]


@pytest.mark.parametrize("host,paired,status", [("127.0.0.1", False, 200), ("192.168.1.20", False, 401),
                                                 ("192.168.1.20", True, 200), ("8.8.8.8", False, 401)])
def test_lifecycle_apis_serve_this_computer_and_paired_devices(app, host, paired, status):
    headers = {"Authorization": f"Bearer {_token(app)}"} if paired else {}
    response = TestClient(app, client=(host, 50000), headers=headers).get("/api/containers")
    assert response.status_code == status, response.text
