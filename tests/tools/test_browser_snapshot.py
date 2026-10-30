"""browser_snapshot: the agent sees the UI it builds. Inside a sandbox (container session worker or
job stage) it reaches the sandbox's own localhost; on the host it never does. Device sizes, dark
mode, element capture and interaction steps on local pages; job stages keep the screenshot as evidence."""

from __future__ import annotations

import functools
import http.server
import json
import struct
import threading

import pytest

from mu.tools.agent import browser
from mu.tools.agent.browser import _options, _ssrf_check_hostname, browser_snapshot

PAGE = """<!doctype html><html><head><title>Counter</title><style>
body { background: white; color: black; } @media (prefers-color-scheme: dark) { body { background: black; color: white; } }
#card { width: 200px; height: 100px; }
</style></head><body>
<div id="card">card</div>
<p id="scheme"></p><p id="out">idle</p>
<input id="name"><button id="go" onclick="document.getElementById('out').textContent = 'hello ' + document.getElementById('name').value">Go</button>
<script>document.getElementById('scheme').textContent = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
setTimeout(() => { const d = document.createElement('div'); d.id = 'late'; d.textContent = 'late content'; document.body.append(d); }, 300);</script>
</body></html>"""


def _png_size(path: str) -> tuple[int, int]:
    with open(path, "rb") as handle:
        head = handle.read(24)
    return struct.unpack(">II", head[16:24])


@pytest.fixture
def site(tmp_path):
    (tmp_path / "index.html").write_text(PAGE)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/index.html"
    server.shutdown()


@pytest.fixture
def sandbox(monkeypatch):
    monkeypatch.delenv("MUCLI_JOB_STAGE", raising=False)
    monkeypatch.setenv("MUCLI_CONTAINER_MODE", "1")


@pytest.fixture(autouse=True)
def _host(monkeypatch, tmp_path):
    for key in ("MUCLI_CONTAINER_MODE", "MUCLI_JOB_STAGE", "HTTP_PROXY", "HTTPS_PROXY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))


def _artifact_path(result) -> str:
    from mu.artifact.registry import ArtifactRegistry
    import os
    registry = ArtifactRegistry(os.path.join(os.environ["MUCLI_HOME"], "sessions", "tool-snapshots"))
    return registry.resolve_path(result["data"]["artifact_id"])


def test_the_host_never_reaches_localhost(site):
    result = browser_snapshot({"url": site}, None)
    assert not result["ok"] and "non-public" in result["message"]


def test_the_sandbox_reaches_its_own_dev_server_but_not_the_network_around_it():
    assert _ssrf_check_hostname("127.0.0.1", allow_loopback=True) is None
    assert _ssrf_check_hostname("localhost", allow_loopback=True) is None
    for blocked in ("10.1.2.3", "192.168.1.5", "169.254.169.254", "metadata.google.internal"):
        assert _ssrf_check_hostname(blocked, allow_loopback=True), blocked
    assert _ssrf_check_hostname("127.0.0.1") and _ssrf_check_hostname("localhost")


def test_mobile_dark_capture_after_interaction_steps(site, sandbox):
    result = browser_snapshot({"url": site, "device": "mobile", "color_scheme": "dark", "full_page": False,
                               "wait_for": "#late", "actions": [
                                   {"fill": "#name", "text": "mu"}, {"click": "#go"}, {"wait_ms": 50}]}, None)
    assert result["ok"], result
    data = result["data"]
    assert data["device"] == "mobile" and data["viewport"] == {"width": 390, "height": 844}
    assert data["actions_run"] == 3
    assert "hello mu" in data["page_text"] and "dark" in data["page_text"] and "late content" in data["page_text"]
    width, height = _png_size(_artifact_path(result))
    assert width == 390 * 3 and abs(height - 844 * 3) <= 3  # scale factor 3 (Chromium rounds mobile heights)


def test_element_capture_and_explicit_viewport(site, sandbox):
    result = browser_snapshot({"url": site, "selector": "#card", "width": 1000, "height": 700}, None)
    assert result["ok"], result
    assert result["data"]["viewport"] == {"width": 1000, "height": 700}
    assert _png_size(_artifact_path(result)) == (200, 100)


def test_a_failed_step_says_which_one(site, sandbox):
    result = browser_snapshot({"url": site, "actions": [{"click": "#go"}, {"click": "#missing"}]}, None)
    assert not result["ok"] and "action 2 (click) failed" in result["message"]


def test_steps_are_refused_on_public_pages():
    _, err = _options({"actions": [{"click": "a"}]}, local=False)
    assert "local targets" in err
    for bad, message in (({"device": "watch"}, "device"), ({"width": 10}, "between"),
                         ({"color_scheme": "sepia"}, "light or dark"), ({"actions": [{"click": "a", "hover": "b"}]}, "exactly one"),
                         ({"actions": [{}] * 21}, "at most")):
        assert message in _options(bad, local=True)[1]
    options, _ = _options({"actions": [{"wait_ms": 999999}]}, local=True)
    assert options["steps"] == [{"wait_ms": 10_000}]


def test_job_stages_keep_the_screenshot_as_evidence(site, monkeypatch, tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    monkeypatch.setattr(browser, "OUTPUT_ROOT", str(output))
    monkeypatch.setenv("MUCLI_JOB_STAGE", "implement")
    first = browser_snapshot({"url": site, "name": "home"}, None)
    second = browser_snapshot({"url": site, "name": "home", "device": "tablet"}, None)
    assert first["ok"] and second["ok"]
    assert first["data"]["evidence"] == "home.png" and second["data"]["evidence"] == "home-2.png"
    index = json.loads((output / "evidence" / "index.json").read_text())
    assert [e["file"] for e in index] == ["home.png", "home-2.png"]
    assert "Counter" in index[1]["caption"] and "tablet" in index[1]["caption"]
