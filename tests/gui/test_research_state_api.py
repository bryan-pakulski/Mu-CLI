"""GET /api/research/state serves the requested session's research ledger."""

import threading
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mu.gui.app import session_by_name
from mu.gui.routers import research as research_router
from mu.research import ResearchState

PAD = "Padding sentence that gives the page a realistic body. " * 6


def _session(mode="research"):
    sm = SimpleNamespace(research=ResearchState(), variables={"agent_mode": mode})
    return SimpleNamespace(session_manager=sm, variables=sm.variables)


def _app(sessions):
    app = FastAPI()
    app.state.sessions = dict(sessions)
    app.state.session_locks = {name: threading.Lock() for name in sessions}
    app.state.current_session_name = next(iter(sessions))
    app.state._fallback_lock = threading.Lock()
    app.state.session_by_name = lambda name=None: session_by_name(app, name)
    app.include_router(research_router.router, prefix="/api/research")
    return TestClient(app)


def test_state_reports_the_ledger_of_the_named_session():
    alpha, beta = _session(), _session(mode="default")
    state = alpha.session_manager.research
    state.add_question("Is the bridge safe?")
    a = state.mark_read(url="https://inspect.example/report", title="Inspection",
                        text=PAD + "No structural cracks were found." + PAD)
    b = state.mark_read(url="https://news.example/bridge", title="News",
                        text=PAD + "Engineers reported hairline cracks." + PAD)
    state.add_candidate(url="https://forum.example/t/1", title="Forum thread")
    state.record_claim("q1", "No cracks", evidence=[
        {"citation_id": a.citation_id, "stance": "supports", "excerpt": "No structural cracks were found."},
        {"citation_id": b.citation_id, "stance": "contradicts", "excerpt": "Engineers reported hairline cracks."}])
    beta.session_manager.research.add_question("Unrelated beta question")

    client = _app({"alpha": alpha, "beta": beta})
    body = client.get("/api/research/state", params={"session_name": "alpha"}).json()

    assert [q["text"] for q in body["questions"]] == ["Is the bridge safe?"]
    assert body["questions"][0]["status"] == "contested"
    claim = body["claims"][0]
    assert claim["contested"] and claim["confidence"] == "low"
    assert [e["stance"] for e in claim["evidence"]] == ["supports", "contradicts"]
    assert body["coverage"]["sources"] == {"candidate": 1, "read": 0, "cited": 2}
    assert [s["id"] for s in body["sources"]] == [1, 2]
    assert [c["title"] for c in body["candidates"]] == ["Forum thread"]
    assert body["matrix"]["origins"] == ["inspect.example", "news.example"]
    assert body["matrix"]["rows"][0]["cells"] == {"inspect.example": ["supports"],
                                                  "news.example": ["contradicts"]}
    assert "[^1]" in body["bibliography"] and "Forum thread" not in body["bibliography"]
    # Earlier clients read claims as findings.
    assert body["findings"][0]["content"] == "No cracks"
    assert body["workspace"]["status"]["label"] == "collecting"

    other = client.get("/api/research/state", params={"session_name": "beta"}).json()
    assert [q["text"] for q in other["questions"]] == ["Unrelated beta question"]
    assert other["sources"] == [] and other["claims"] == []
    assert other["workspace"]["status"]["label"] == "idle"
