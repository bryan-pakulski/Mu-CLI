"""Device pairing (documentation/plans/mobile_parity.md §7a): a non-loopback client signs in by
pairing; this machine is unchanged; only this machine can pair or revoke; bearer requests need no
CSRF token while browser requests still do."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from mu.gui import devices as dev
from mu.gui.devices import DeviceStore, PairingError

LAN = ("192.168.1.20", 50000)


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


# --------------------------------------------------------------------- the store

def test_a_code_pairs_once_and_the_token_is_stored_only_as_a_hash(tmp_path):
    store = DeviceStore(str(tmp_path / "devices.json"))
    code = store.start_pairing()["code"]
    token, device = store.pair(f"{code[:4]}-{code[4:].lower()}", "  Bryan's   Pixel ", client="192.168.1.20")
    assert device.name == "Bryan's Pixel" and store.authenticate(token).id == device.id
    with pytest.raises(PairingError):
        store.pair(code, "again")                                    # single use
    on_disk = (tmp_path / "devices.json").read_text()
    assert token not in on_disk and device.token_hash in on_disk
    assert oct((tmp_path / "devices.json").stat().st_mode & 0o777) == "0o600"
    reloaded = DeviceStore(str(tmp_path / "devices.json"))
    assert reloaded.authenticate(token).name == "Bryan's Pixel"     # survives a restart


def test_codes_expire_wrong_codes_say_nothing_and_attempts_are_rate_limited(tmp_path):
    clock = Clock()
    store = DeviceStore(str(tmp_path / "d.json"), clock=clock)
    code = store.start_pairing()["code"]
    clock.t += dev.CODE_TTL_S + 1
    with pytest.raises(PairingError) as expired:
        store.pair(code, "x")
    with pytest.raises(PairingError) as wrong:
        store.pair("ZZZZZZZZ", "x")
    assert str(expired.value) == str(wrong.value) and expired.value.status == 401   # indistinguishable
    fresh = store.start_pairing()["code"]
    for _ in range(dev.ATTEMPTS_PER_CLIENT):
        with pytest.raises(PairingError):
            store.pair("WRONGCOD", "x", client="10.0.0.9")
    with pytest.raises(PairingError) as limited:
        store.pair(fresh, "x", client="10.0.0.9")                   # even the right code waits
    assert limited.value.status == 429
    assert store.pair(fresh, "x", client="10.0.0.10")[1].name == "x"   # other clients aren't blocked
    clock.t += dev.ATTEMPT_WINDOW_S
    store.start_pairing()


def test_revoking_signs_the_device_out(tmp_path):
    store = DeviceStore(str(tmp_path / "d.json"))
    token, device = store.pair(store.start_pairing()["code"], "Phone")
    assert store.revoke("Phone").id == device.id
    assert store.authenticate(token) is None and not store.is_active(device.id)


@pytest.mark.parametrize("host,loopback", [("127.0.0.1", True), ("::1", True), ("127.0.0.53", True), ("testclient", True),
                                           ("192.168.1.20", False), ("10.0.0.1", False), ("fd00::1", False), ("8.8.8.8", False)])
def test_loopback(host, loopback):
    assert dev.is_loopback(host) is loopback


# --------------------------------------------------------------------- the gate

@pytest.fixture
def app(tmp_path, monkeypatch):
    from mu.gui.app import create_app
    from mu.gui.routers import orchestration as orch_router
    from orch_helpers import make_orch
    application = create_app(args=SimpleNamespace(provider=None, model=None), build_session_fn=lambda *a, **k: None)
    application.state.devices = DeviceStore(str(tmp_path / "devices.json"))
    application.state.orchestrator = make_orch(tmp_path / "orch")
    monkeypatch.setattr(orch_router, "_CURSOR", {"seq": -1})
    yield application
    application.state.orchestrator.stop()


def _pair(app) -> str:
    local = TestClient(app)
    csrf = local.get("/api/csrf").json()
    code = local.post("/api/devices/pairing", json={}, headers={csrf["header"]: csrf["token"]}).json()["code"]
    phone = TestClient(app, client=LAN)
    r = phone.post("/api/devices/pair", json={"code": code, "device_name": "Pixel"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_unpaired_network_clients_get_401_on_everything_but_health_and_pairing(app):
    phone = TestClient(app, client=LAN)
    for path in ("/api/sessions", "/api/jobs/vm/home", "/api/orchestration/overview", "/api/devices/self"):
        r = phone.get(path)
        assert r.status_code == 401 and r.json()["pairing_required"], path
    assert phone.get("/healthz").status_code == 200
    assert phone.post("/api/devices/pair", json={"code": "WRONGCOD"}).status_code == 401
    assert TestClient(app).get("/api/sessions").status_code == 200       # this machine: unchanged


def test_a_paired_phone_signs_in_with_its_token(app):
    token = _pair(app)
    phone = TestClient(app, client=LAN, headers={"Authorization": f"Bearer {token}"})
    assert phone.get("/api/sessions").status_code == 200
    assert phone.get("/api/devices/self").json()["device"]["name"] == "Pixel"
    assert TestClient(app, client=LAN, headers={"Authorization": "Bearer nope"}).get("/api/sessions").status_code == 401


def test_only_this_computer_pairs_lists_and_revokes(app):
    token = _pair(app)
    phone = TestClient(app, client=LAN, headers={"Authorization": f"Bearer {token}"})
    assert phone.post("/api/devices/pairing", json={}).status_code == 403          # a phone can't pair others
    assert phone.get("/api/devices").status_code == 403
    device_id = phone.get("/api/devices/self").json()["device"]["id"]
    assert phone.delete(f"/api/devices/{device_id}").status_code == 403
    local = TestClient(app)
    assert local.post("/api/devices/pairing", json={}).status_code == 403          # browsers need CSRF
    assert [d["name"] for d in local.get("/api/devices").json()["devices"]] == ["Pixel"]
    csrf = local.get("/api/csrf").json()
    assert local.delete(f"/api/devices/{device_id}", headers={csrf["header"]: csrf["token"]}).status_code == 200
    assert phone.get("/api/sessions").status_code == 401                            # revoked: signed out


def test_a_device_can_sign_itself_out(app):
    token = _pair(app)
    phone = TestClient(app, client=LAN, headers={"Authorization": f"Bearer {token}"})
    assert phone.delete("/api/devices/self").status_code == 200
    assert phone.get("/api/sessions").status_code == 401


def test_bearer_requests_need_no_csrf_but_browser_requests_still_do(app):
    token = _pair(app)
    phone = TestClient(app, client=LAN, headers={"Authorization": f"Bearer {token}"})
    r = phone.post("/api/orchestration/cmd", json={"type": "installStarterPack"})
    assert r.status_code == 200, r.text                                              # no CSRF header needed
    browser = TestClient(app)
    assert browser.post("/api/orchestration/cmd", json={"type": "installStarterPack"}).status_code == 403


def test_the_qr_carries_the_code(app):
    local = TestClient(app)
    csrf = local.get("/api/csrf").json()
    pairing = local.post("/api/devices/pairing", json={}, headers={csrf["header"]: csrf["token"]}).json()
    assert f"code={pairing['code']}" in pairing["payload"] and pairing["qr_svg"].startswith("<svg")
    assert pairing["display"] == f"{pairing['code'][:4]}-{pairing['code'][4:]}"


def test_streams_stop_for_a_revoked_device(app):
    token = _pair(app)
    store = app.state.devices
    device = store.authenticate(token)
    request = SimpleNamespace(state=SimpleNamespace(device=device), app=app)
    assert dev.still_paired(request)
    store.revoke(device.id)
    assert not dev.still_paired(request)
    assert dev.still_paired(SimpleNamespace(state=SimpleNamespace(), app=app))     # loopback streams unaffected


def test_unpaired_websockets_are_closed_before_accept(app):
    from starlette.websockets import WebSocketDisconnect
    phone = TestClient(app, client=LAN)
    with pytest.raises(WebSocketDisconnect) as closed:
        with phone.websocket_connect("/api/containers/dev/shell") as ws:
            ws.receive_text()
    assert closed.value.code == 1008
