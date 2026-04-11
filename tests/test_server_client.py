import io
import json
from urllib import error

import pytest

from core.server_client import MuCLIServerClient, ServerClientError


class DummyResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class DummyStreamResponse:
    def __init__(self, lines):
        self._lines = [line.encode("utf-8") for line in lines]

    def __iter__(self):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_base_url_normalization():
    client = MuCLIServerClient("127.0.0.1:8765")
    assert client.base_url == "http://127.0.0.1:8765"


def test_message_posts_json(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["body"] = req.data.decode("utf-8")
        captured["client_id"] = req.headers.get("X-mucli-client-id")
        return DummyResponse({"ok": True, "assistant_text": "hello"})

    monkeypatch.setattr("core.server_client.request.urlopen", fake_urlopen)

    client = MuCLIServerClient("http://localhost:8765")
    result = client.message("ping")

    assert result["ok"] is True
    assert captured["url"].endswith("/api/message")
    assert captured["method"] == "POST"
    assert json.loads(captured["body"]) == {"text": "ping"}
    assert str(captured["client_id"]).startswith("client-")


def test_http_error_raises_server_client_error(monkeypatch):
    def fake_urlopen(req, timeout):
        raise error.HTTPError(
            url=req.full_url,
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=io.BytesIO(b'{"ok":false,"error":"boom"}'),
        )

    monkeypatch.setattr("core.server_client.request.urlopen", fake_urlopen)

    client = MuCLIServerClient("http://localhost:8765")
    with pytest.raises(ServerClientError) as exc:
        client.command("/help")

    assert "HTTP 400" in str(exc.value)


def test_capabilities_uses_expected_endpoint(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        return DummyResponse({"ok": True, "capabilities": {"api_version": "v1"}})

    monkeypatch.setattr("core.server_client.request.urlopen", fake_urlopen)

    client = MuCLIServerClient("http://localhost:8765")
    result = client.capabilities()

    assert result["ok"] is True
    assert captured["url"].endswith("/api/capabilities")


def test_tasks_uses_expected_endpoints(monkeypatch):
    captured = []

    def fake_urlopen(req, timeout):
        captured.append(req.full_url)
        if req.full_url.endswith("/api/tasks"):
            return DummyResponse({"ok": True, "tasks": []})
        return DummyResponse({"ok": True, "task": {"task_id": "abc"}})

    monkeypatch.setattr("core.server_client.request.urlopen", fake_urlopen)

    client = MuCLIServerClient("http://localhost:8765")
    list_result = client.tasks()
    single_result = client.task("abc")

    assert list_result["ok"] is True
    assert single_result["ok"] is True
    assert captured[0].endswith("/api/tasks")
    assert captured[1].endswith("/api/tasks/abc")


def test_approvals_and_resolution_endpoints(monkeypatch):
    captured = []

    def fake_urlopen(req, timeout):
        captured.append((req.full_url, req.get_method(), req.data))
        if req.full_url.endswith("/api/approvals"):
            return DummyResponse({"ok": True, "pending_approvals": []})
        return DummyResponse({"ok": True, "approval": {"approval_id": "a1"}})

    monkeypatch.setattr("core.server_client.request.urlopen", fake_urlopen)
    client = MuCLIServerClient("http://localhost:8765")

    approvals = client.approvals()
    resolved = client.resolve_approval("a1", "approve")

    assert approvals["ok"] is True
    assert resolved["ok"] is True
    assert captured[0][0].endswith("/api/approvals")
    assert captured[1][0].endswith("/api/approvals/resolve")
    assert json.loads(captured[1][2].decode("utf-8"))["approval_id"] == "a1"


def test_stream_events_parses_sse_payload(monkeypatch):
    def fake_urlopen(req, timeout):
        return DummyStreamResponse(
            [
                "event: trace.tool",
                'data: {"event":"trace.tool","payload":{"tool":"read_file"}}',
                "",
                'data: {"event":"trace.tool_result","payload":{"ok":true}}',
            ]
        )

    monkeypatch.setattr("core.server_client.request.urlopen", fake_urlopen)
    client = MuCLIServerClient("http://localhost:8765")

    events = list(client.stream_events(max_events=2))

    assert len(events) == 2
    assert events[0]["event"] == "trace.tool"
    assert events[1]["event"] == "trace.tool_result"


def test_arbiter_methods_use_expected_endpoints(monkeypatch):
    captured = []

    def fake_urlopen(req, timeout):
        captured.append((req.full_url, req.get_method(), req.data))
        if req.full_url.endswith("/api/arbiter"):
            return DummyResponse({"ok": True, "arbiter": {"lock_active": False}})
        if req.full_url.endswith("/api/arbiter/claim"):
            return DummyResponse({"ok": True, "arbiter": {"lock_active": True}})
        if req.full_url.endswith("/api/arbiter/observer"):
            return DummyResponse({"ok": True, "arbiter": {"lock_active": False}})
        return DummyResponse({"ok": True, "arbiter": {"lock_active": False}})

    monkeypatch.setattr("core.server_client.request.urlopen", fake_urlopen)
    client = MuCLIServerClient("http://localhost:8765")

    status = client.arbiter_status()
    claim = client.arbiter_claim(force=True)
    observer = client.arbiter_set_observer(True)
    release = client.arbiter_release()

    assert status["ok"] is True
    assert claim["ok"] is True
    assert observer["ok"] is True
    assert release["ok"] is True
    assert captured[0][0].endswith("/api/arbiter")
    assert captured[1][0].endswith("/api/arbiter/claim")
    assert captured[2][0].endswith("/api/arbiter/observer")
    assert captured[3][0].endswith("/api/arbiter/release")
