"""Jira browsing: pagination, done-hidden default, status filter, backlog, board suggestions."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mu.gui.routers import jobs as jobs_router
from mu.integrations import jira
from mu.jobs import JobService, JobStore

BASE = "https://jira.example.com"
CATS = {"To Do": "new", "Blocked": "indeterminate", "In Progress": "indeterminate", "Done": "done"}


def _issue(n, status):
    return {"key": f"PROJ-{n}", "fields": {
        "summary": f"t{n}", "status": {"name": status, "statusCategory": {"key": CATS[status]}},
        "project": {"key": "PROJ"}}}


# 120 board issues: first 60 Done (crowd page 1), then To Do / Blocked / In Progress.
BOARD = ([_issue(i, "Done") for i in range(60)]
         + [_issue(60 + i, ("To Do", "Blocked", "In Progress")[i % 3]) for i in range(60)])


def _matches(issue, jql):
    status = issue["fields"]["status"]
    if "statusCategory != Done" in (jql or "") and status["statusCategory"]["key"] == "done":
        return False
    if "status in (" in (jql or ""):
        wanted = jql.split("status in (", 1)[1].split(")", 1)[0]
        return f'"{status["name"]}"' in wanted
    return True


class Transport:
    def __init__(self):
        self.calls = []

    def __call__(self, method, url, headers, params, body):
        params = dict(params or {})
        self.calls.append((url[len(BASE):], params))
        path = url[len(BASE):]
        if path == "/rest/agile/1.0/board":
            boards = [{"id": i, "name": f"Board {i}", "type": "scrum",
                       "location": {"projectKey": "OTHER" if i % 2 else "PROJ"}} for i in range(1, 61)]
            start, size = int(params.get("startAt", 0)), int(params.get("maxResults", 50))
            page = boards[start:start + size]
            return {"values": page, "total": len(boards), "isLast": start + size >= len(boards)}
        if path in ("/rest/agile/1.0/board/7/issue", "/rest/agile/1.0/board/7/backlog"):
            pool = BOARD if path.endswith("/issue") else BOARD[60:70]
            hits = [i for i in pool if _matches(i, params.get("jql"))]
            start, size = int(params.get("startAt", 0)), min(int(params.get("maxResults", 50)), 50)
            return {"issues": hits[start:start + size], "total": len(hits)}
        if path.endswith("/search"):  # DC JQL search (my projects)
            return {"issues": [_issue(70, "To Do")], "total": 1}
        raise AssertionError(url)


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    for var in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "JIRA_PAT", "JIRA_WRITEBACK"):
        monkeypatch.delenv(var, raising=False)
    transport = Transport()
    monkeypatch.setattr(jira, "_httpx_transport", lambda timeout: transport)
    app = FastAPI()
    app.include_router(jobs_router.router, prefix="/api/jobs")
    app.state.job_service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    with TestClient(app) as client:
        assert client.put("/api/jobs/jira/settings", json={"base_url": BASE, "pat": "x"}).status_code == 200
        yield client, transport


def test_done_hidden_by_default_and_all_pages_fetched(api):
    client, transport = api
    body = client.get("/api/jobs/jira/tickets", params={"board_id": 7}).json()
    names = {t["status"] for t in body["tickets"]}
    assert names == {"To Do", "Blocked", "In Progress"}
    assert len(body["tickets"]) == 60
    assert "statusCategory != Done" in body["jql"]
    assert {s["name"] for s in body["statuses"]} == names


def test_include_done_returns_everything_across_pages(api):
    client, transport = api
    body = client.get("/api/jobs/jira/tickets", params={"board_id": 7, "include_done": True}).json()
    assert len(body["tickets"]) == 120
    pages = [p for path, p in transport.calls if path.endswith("/board/7/issue")]
    assert [p["startAt"] for p in pages] == [0, 50, 100]
    assert body["statuses"][0]["name"] == "To Do" and body["statuses"][-1]["name"] == "Done"


def test_status_filter(api):
    client, _ = api
    body = client.get("/api/jobs/jira/tickets", params={"board_id": 7, "status": "To Do,Blocked"}).json()
    assert {t["status"] for t in body["tickets"]} == {"To Do", "Blocked"}


def test_backlog(api):
    client, transport = api
    body = client.get("/api/jobs/jira/tickets", params={"board_id": 7, "backlog": True}).json()
    assert len(body["tickets"]) == 10
    assert any(path.endswith("/board/7/backlog") for path, _ in transport.calls)


def test_boards_all_pages_with_suggestions_first(api):
    client, _ = api
    body = client.get("/api/jobs/jira/boards").json()
    assert len(body["boards"]) == 60
    assert body["my_projects"] == ["PROJ"]
    first = body["boards"][:30]
    assert all(b["suggested"] and b["project"] == "PROJ" for b in first)
    assert not any(b["suggested"] for b in body["boards"][30:])


def test_build_ticket_jql():
    assert jira.build_ticket_jql() == "statusCategory != Done"
    assert jira.build_ticket_jql(include_done=True) == ""
    q = jira.build_ticket_jql("project = A ORDER BY rank", mine=True, statuses=["To Do"])
    assert q.startswith("(project = A)") and q.endswith("ORDER BY rank")
    assert "assignee = currentUser()" in q and 'status in ("To Do")' in q


def test_cloud_search_uses_search_jql_with_token_paging():
    calls = []

    def transport(method, url, headers, params, body):
        calls.append((url, dict(params)))
        if "nextPageToken" not in params:
            return {"issues": [_issue(1, "To Do")], "nextPageToken": "t2", "isLast": False}
        return {"issues": [_issue(2, "Blocked")], "isLast": True}

    cfg = jira.JiraConfig(base_url=BASE, email="a@b.c", api_token="tok")
    issues = jira.JiraClient(cfg, transport=transport).search("project = A", max_results=50)
    assert [i["key"] for i in issues] == ["PROJ-1", "PROJ-2"]
    assert all(url.endswith("/rest/api/3/search/jql") for url, _ in calls)
    assert calls[1][1]["nextPageToken"] == "t2"
