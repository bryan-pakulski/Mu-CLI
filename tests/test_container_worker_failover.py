"""Worker → supervisor reachability: gateway failover for --internal bridges."""

from __future__ import annotations

import httpx
import pytest

from mu.container.worker import (
    WorkerBridgeUI,
    _default_gateway,
    supervisor_candidates,
)

ROUTE_SAMPLE = (
    "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
    "eth0\t000012AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0\n"
    "eth0\t00000000\t010012AC\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
)


def test_default_gateway_parses_little_endian_route_entry():
    assert _default_gateway(ROUTE_SAMPLE) == "172.18.0.1"
    # A docker --internal bridge has NO default route, only the on-link
    # subnet entry; the host answers at network+1 (verified live: 172.18.0.0/16
    # → 172.18.0.1).
    assert _default_gateway("\n".join(ROUTE_SAMPLE.splitlines()[:2])) == "172.18.0.1"
    # Default route wins over the on-link derivation when both exist.
    assert _default_gateway(
        "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\n"
        "eth0\t000012AC\t00000000\t0001\t0\t0\t0\t0000FFFF\n"
        "eth0\t00000000\t0A0012AC\t0003\t0\t0\t0\t00000000\n"
    ) == "172.18.0.10"
    assert _default_gateway("") == ""
    assert _default_gateway("garbage\nx\ty\tzz\n") == ""


def test_supervisor_candidates_configured_first_then_gateway():
    assert supervisor_candidates("http://host.docker.internal:30311", "172.18.0.1") == [
        "http://host.docker.internal:30311",
        "http://172.18.0.1:30311",
    ]
    # Already pointing at the gateway → no duplicate.
    assert supervisor_candidates("http://172.18.0.1:30311/", "172.18.0.1") == [
        "http://172.18.0.1:30311"
    ]
    assert supervisor_candidates("", "172.18.0.1") == []
    assert supervisor_candidates("http://h:1", "") == ["http://h:1"]


class _Response:
    def __init__(self, status_code: int = 200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"ok": True}

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://x/")
            raise httpx.HTTPStatusError("boom", request=request, response=httpx.Response(self.status_code, request=request))

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, unreachable_hosts: set[str], status_code: int = 200):
        self.unreachable_hosts = unreachable_hosts
        self.status_code = status_code
        self.calls: list[str] = []

    def _send(self, method, url, **kwargs):
        self.calls.append(url)
        host = httpx.URL(url).host
        if host in self.unreachable_hosts:
            raise httpx.ConnectError("[Errno 101] Network is unreachable", request=httpx.Request(method, url))
        return _Response(self.status_code)

    def post(self, url, **kwargs):
        return self._send("POST", url, **kwargs)

    def get(self, url, **kwargs):
        return self._send("GET", url, **kwargs)


def _ui(monkeypatch) -> WorkerBridgeUI:
    monkeypatch.setenv("MUCLI_SUPERVISOR_URL", "http://host.docker.internal:30311")
    monkeypatch.setenv("MUCLI_WORKER_TOKEN", "tok")
    monkeypatch.setattr("mu.container.worker._read_default_gateway", lambda *a, **k: "172.18.0.1")
    return WorkerBridgeUI("demo")


def test_request_fails_over_to_gateway_on_connect_error_and_pins(monkeypatch):
    ui = _ui(monkeypatch)
    fake = _FakeClient({"host.docker.internal"})
    ui._client = fake

    response = ui._request("POST", "/api/container-worker/events", json={})
    assert response.status_code == 200
    assert fake.calls == [
        "http://host.docker.internal:30311/api/container-worker/events",
        "http://172.18.0.1:30311/api/container-worker/events",
    ]
    # Pinned: the next call goes straight to the gateway.
    assert ui.supervisor_url == "http://172.18.0.1:30311"
    ui._request("POST", "/api/container-worker/events", json={})
    assert fake.calls[-1] == "http://172.18.0.1:30311/api/container-worker/events"
    assert len(fake.calls) == 3


def test_request_raises_connect_error_when_every_candidate_is_unreachable(monkeypatch):
    ui = _ui(monkeypatch)
    ui._client = _FakeClient({"host.docker.internal", "172.18.0.1"})
    with pytest.raises(httpx.ConnectError):
        ui._request("POST", "/api/container-worker/events", json={})
    assert ui.supervisor_url == "http://host.docker.internal:30311"


def test_http_errors_are_not_retried_against_fallback(monkeypatch):
    ui = _ui(monkeypatch)
    fake = _FakeClient(set(), status_code=401)
    ui._client = fake
    response = ui._request("POST", "/api/container-worker/events", json={})
    with pytest.raises(httpx.HTTPStatusError):
        response.raise_for_status()
    # Host answered (even with an error) → exactly one attempt, no pin change.
    assert fake.calls == ["http://host.docker.internal:30311/api/container-worker/events"]
    assert ui.supervisor_url == "http://host.docker.internal:30311"


def test_publish_raw_uses_failover_path(monkeypatch):
    ui = _ui(monkeypatch)
    fake = _FakeClient({"host.docker.internal"})
    ui._client = fake
    ui._publish_raw({"kind": "status_update", "text": "hi"})
    assert fake.calls[-1].startswith("http://172.18.0.1:30311/api/container-worker/events")


def test_publish_raw_warns_exactly_once_when_supervisor_unreachable(monkeypatch, caplog):
    import logging

    ui = _ui(monkeypatch)
    ui._client = _FakeClient(unreachable_hosts={"host.docker.internal", "172.18.0.1"})
    with caplog.at_level(logging.DEBUG, logger="mucli.container.worker"):
        for _ in range(3):
            ui._publish_raw({"kind": "assistant_delta", "text": "x"})
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "container supervisor event delivery failed" in warnings[0].getMessage()
    assert "host.docker.internal" in warnings[0].getMessage()
    assert ui._warned_unreachable is True
    debugs = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert len(debugs) >= 2  # subsequent failures demoted to DEBUG


def test_publish_artifact_connect_error_is_actionable(monkeypatch):
    ui = _ui(monkeypatch)
    ui._client = _FakeClient(unreachable_hosts={"host.docker.internal", "172.18.0.1"})
    with pytest.raises(RuntimeError) as excinfo:
        ui.publish_artifact(name="viz.html", content="<html></html>", mime_type="text/html",
                            kind="visualization", display="inline")
    message = str(excinfo.value)
    assert "container supervisor unreachable at http://host.docker.internal:30311" in message
    assert "172.18.0.1" in message
    assert "reload the container session" in message
    assert isinstance(excinfo.value.__cause__, httpx.ConnectError)
