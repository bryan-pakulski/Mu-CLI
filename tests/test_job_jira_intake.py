"""Durable job v2 task 6: Jira intake (client, import, API, TUI)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mu.commands import job as job_cmd
from mu.gui.routers import jobs as jobs_router
from mu.integrations import jira
from mu.jobs import JobService, JobStore

EXECUTION = {"provider": "ollama", "model": "m", "session_type": "workspace"}
SECRET = "sekret-token-value-123"


def _issue(key="PROJ-1", summary="Fix login"):
    return {
        "key": key,
        "fields": {
            "summary": summary,
            "description": "Users cannot log in.\n\nAcceptance criteria:\n- login works\n- error shown on bad password",
            "status": {"name": "To Do"},
            "issuetype": {"name": "Bug"},
            "labels": ["auth"],
        },
    }


class FakeTransport:
    def __init__(self, issues):
        self.issues = {i["key"]: i for i in issues}
        self.calls = []

    def __call__(self, method, url, headers, params, body):
        self.calls.append((method, url, dict(headers), params, body))
        if method == "GET" and "/issue/" in url:
            return self.issues[url.rsplit("/", 1)[-1]]
        if method == "GET" and url.endswith("/search"):
            return {"issues": list(self.issues.values())}
        if method == "POST":
            return {"id": "1"}
        raise AssertionError(url)


def _env(**extra):
    env = {"JIRA_BASE_URL": "https://jira.example.com", "JIRA_PAT": SECRET}
    env.update(extra)
    return env


def _service(tmp_path):
    return JobService(JobStore(str(tmp_path / "jobs.sqlite3")))


def _defaults(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return {"workspace_root": str(ws), "execution": dict(EXECUTION)}


def test_config_modes_and_auth_headers():
    cloud = jira.JiraConfig.from_env({"JIRA_BASE_URL": "https://a.atlassian.net/", "JIRA_EMAIL": "e@x", "JIRA_API_TOKEN": "t"})
    assert cloud.mode == "basic" and cloud.auth_header()["Authorization"].startswith("Basic ")
    dc = jira.JiraConfig.from_env(_env())
    assert dc.mode == "bearer" and dc.auth_header() == {"Authorization": f"Bearer {SECRET}"}
    assert SECRET not in json.dumps(dc.public())
    assert dc.writeback is False
    with pytest.raises(jira.JiraError):
        jira.JiraConfig.from_env({"JIRA_BASE_URL": "https://x"}).validate()


def test_import_creates_job_with_metadata_and_no_secret(tmp_path):
    service = _service(tmp_path)
    transport = FakeTransport([_issue()])
    client = jira.JiraClient(jira.JiraConfig.from_env(_env()), transport=transport)
    result = jira.import_issues(service, client, keys=["PROJ-1"], defaults=_defaults(tmp_path))
    assert [c["key"] for c in result["created"]] == ["PROJ-1"] and not result["errors"]
    job = service.get(result["created"][0]["job_id"])
    assert job.title.startswith("PROJ-1")
    assert job.metadata["jira"]["key"] == "PROJ-1"
    assert job.metadata["jira"]["url"] == "https://jira.example.com/browse/PROJ-1"
    assert "login works" in job.acceptance_criteria
    assert SECRET not in json.dumps(job.to_dict(), default=str)
    # writeback is off by default: only GETs went out
    assert all(call[0] == "GET" for call in transport.calls)


def test_import_skips_issue_with_live_job(tmp_path):
    service = _service(tmp_path)
    client = jira.JiraClient(jira.JiraConfig.from_env(_env()), transport=FakeTransport([_issue()]))
    jira.import_issues(service, client, keys=["PROJ-1"], defaults=_defaults(tmp_path))
    again = jira.import_issues(service, client, keys=["PROJ-1"], defaults=_defaults(tmp_path))
    assert not again["created"] and again["skipped"][0]["key"] == "PROJ-1"


def test_writeback_opt_in_posts_comment(tmp_path):
    transport = FakeTransport([_issue()])
    client = jira.JiraClient(jira.JiraConfig.from_env(_env(JIRA_WRITEBACK="true")), transport=transport)
    jira.import_issues(_service(tmp_path), client, keys=["PROJ-1"], defaults=_defaults(tmp_path))
    assert any(call[0] == "POST" and call[1].endswith("/comment") for call in transport.calls)


def test_invalid_key_rejected():
    client = jira.JiraClient(jira.JiraConfig.from_env(_env()), transport=FakeTransport([]))
    with pytest.raises(jira.JiraError):
        client.get_issue("not a key")


@pytest.fixture
def api(tmp_path, monkeypatch):
    service = _service(tmp_path)
    transport = FakeTransport([_issue(), _issue("PROJ-2", "Second")])
    for k, v in _env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(jira, "_httpx_transport", lambda timeout: transport)
    app = FastAPI()
    app.include_router(jobs_router.router, prefix="/api/jobs")
    app.state.job_service = service
    with TestClient(app) as client:
        yield client, service


def test_api_import_by_jql(api, tmp_path):
    client, service = api
    resp = client.post("/api/jobs/jira/import", json={"jql": "project = PROJ", "defaults": _defaults(tmp_path)})
    assert resp.status_code == 200, resp.text
    assert sorted(c["key"] for c in resp.json()["created"]) == ["PROJ-1", "PROJ-2"]
    assert SECRET not in resp.text


def test_api_rejects_credentials_in_body(api, tmp_path):
    client, _ = api
    resp = client.post("/api/jobs/jira/import", json={"keys": ["PROJ-1"], "api_token": "x"})
    assert resp.status_code == 400


def test_api_requires_keys_or_jql(api):
    client, _ = api
    assert client.post("/api/jobs/jira/import", json={}).status_code == 400


def test_tui_jira_pull(tmp_path, monkeypatch):
    service = _service(tmp_path)
    for k, v in _env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(jira, "_httpx_transport", lambda timeout: FakeTransport([_issue()]))
    monkeypatch.setattr(job_cmd, "get_default_job_service", lambda: service)
    monkeypatch.setattr(job_cmd, "_session_defaults", lambda session: _defaults(tmp_path))
    result = job_cmd.job_cmd(SimpleNamespace(ui=None), "jira pull PROJ-1")
    assert result.data["created"][0]["key"] == "PROJ-1"
    assert len(service.list()) == 1
