"""Jira link settings ($MUCLI_HOME/integrations/jira.json) + board/sprint/ticket browsing API."""

from __future__ import annotations

import os
import stat

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mu.gui.routers import jobs as jobs_router
from mu.integrations import jira
from mu.jobs import JobService, JobStore

SECRET = "pat-secret-value-123"
BASE = "https://jira.example.com"


def _issue(key="PROJ-1", summary="Fix login", status="To Do"):
    return {"key": key, "fields": {"summary": summary, "description": "d",
                                   "status": {"name": status, "statusCategory": {"key": "new"}},
                                   "issuetype": {"name": "Bug"}, "assignee": {"displayName": "Bry"}}}


class Transport:
    def __init__(self):
        self.calls = []

    def __call__(self, method, url, headers, params, body):
        self.calls.append((method, url, dict(headers), params))
        path = url[len(BASE):]
        if path.endswith("/myself"):
            return {"displayName": "Bry", "accountId": "acc"}
        if path == "/rest/agile/1.0/board":
            return {"values": [{"id": 7, "name": "Team board", "type": "scrum"}], "isLast": True}
        if path == "/rest/agile/1.0/board/7/sprint":
            return {"values": [{"id": 70, "name": "Sprint 1", "state": "active"}], "isLast": True}
        if path in ("/rest/agile/1.0/sprint/70/issue", "/rest/agile/1.0/board/7/issue"):
            return {"issues": [_issue(), _issue("PROJ-2", "Second")]}
        if path.endswith("/search"):
            return {"issues": [_issue()]}
        if "/issue/" in path and method == "GET":
            return _issue(path.rsplit("/", 1)[-1])
        raise AssertionError(url)


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    for var in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "JIRA_PAT", "JIRA_WRITEBACK"):
        monkeypatch.delenv(var, raising=False)
    transport = Transport()
    monkeypatch.setattr(jira, "_httpx_transport", lambda timeout: transport)
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    app = FastAPI()
    app.include_router(jobs_router.router, prefix="/api/jobs")
    app.state.job_service = service
    with TestClient(app) as client:
        yield client, service, transport, tmp_path


def _link(client):
    resp = client.put("/api/jobs/jira/settings", json={"base_url": BASE + "/", "pat": SECRET})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_settings_saved_to_mucli_home_and_never_echo_token(api):
    client, _, _, tmp_path = api
    assert client.get("/api/jobs/jira/settings").json()["configured"] is False
    body = _link(client)
    assert body["configured"] is True and body["pat_set"] is True and body["mode"] == "bearer"
    assert SECRET not in str(body)
    path = tmp_path / "home" / "integrations" / "jira.json"
    assert path.exists() and SECRET in path.read_text()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    got = client.get("/api/jobs/jira/settings")
    assert SECRET not in got.text and got.json()["base_url"] == BASE


def test_blank_token_keeps_stored_and_delete_unlinks(api):
    client, *_ = api
    _link(client)
    client.put("/api/jobs/jira/settings", json={"base_url": BASE, "pat": ""})
    assert client.get("/api/jobs/jira/settings").json()["pat_set"] is True
    client.put("/api/jobs/jira/settings", json={"preferences": {"boards": [{"id": 7, "name": "Team board"}]}})
    cleared = client.delete("/api/jobs/jira/settings").json()
    assert cleared["configured"] is False and cleared["pat_set"] is False
    assert cleared["preferences"]["boards"][0]["id"] == 7  # preferences survive unlink


def test_bad_base_url_rejected(api):
    client, *_ = api
    assert client.put("/api/jobs/jira/settings", json={"base_url": "ftp://x"}).status_code == 400


def test_unlinked_browse_is_400(api):
    client, *_ = api
    assert client.get("/api/jobs/jira/boards").status_code == 400


def test_test_link_boards_sprints(api):
    client, _, transport, _ = api
    _link(client)
    test = client.post("/api/jobs/jira/test").json()
    assert test["ok"] is True and test["user"]["display_name"] == "Bry" if "display_name" in test["user"] else test["ok"]
    assert client.get("/api/jobs/jira/boards").json()["boards"][0]["id"] == 7
    assert client.get("/api/jobs/jira/boards/7/sprints").json()["sprints"][0]["id"] == 70
    assert all(c[2]["Authorization"] == f"Bearer {SECRET}" for c in transport.calls)


def test_tickets_show_linked_job_and_status(api):
    client, service, _, tmp_path = api
    _link(client)
    ws = tmp_path / "ws"
    ws.mkdir()
    imported = client.post("/api/jobs/jira/import", json={
        "keys": ["PROJ-1"],
        "defaults": {"workspace_root": str(ws),
                     "execution": {"provider": "ollama", "model": "m", "session_type": "workspace"}},
    })
    assert imported.status_code == 200, imported.text
    job_id = imported.json()["created"][0]["job_id"]
    tickets = client.get("/api/jobs/jira/tickets", params={"board_id": 7, "sprint_id": 70}).json()["tickets"]
    by_key = {t["key"]: t for t in tickets}
    assert by_key["PROJ-1"]["job"]["job_id"] == job_id
    assert by_key["PROJ-1"]["job"]["status"] == service.get(job_id).status.value
    assert by_key["PROJ-1"]["status"] == "To Do"
    assert by_key["PROJ-2"]["job"] is None
    assert by_key["PROJ-1"]["url"] == f"{BASE}/browse/PROJ-1"


def test_tickets_need_board_or_jql(api):
    client, *_ = api
    _link(client)
    assert client.get("/api/jobs/jira/tickets").status_code == 400
    assert client.get("/api/jobs/jira/tickets", params={"jql": "project = PROJ"}).status_code == 200


def test_env_overrides_file(api, monkeypatch):
    client, *_ = api
    _link(client)
    monkeypatch.setenv("JIRA_BASE_URL", "https://env.example.com")
    body = client.get("/api/jobs/jira/settings").json()
    assert body["base_url"] == "https://env.example.com" and body["sources"]["base_url"] == "env"
