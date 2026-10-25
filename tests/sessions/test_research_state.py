"""Research state is session-owned: concurrent sessions never share it, and it
persists with the session."""

import json
import os
import threading

import pytest

from mu.research import ResearchState, get_citation_manager, use_research_state
from mu.session.manager import SessionManager
from mu.tools._dispatcher import dispatch
from mu.tools.research import handlers as research_handlers

from research_helpers import research_session


@pytest.fixture()
def isolated_home(monkeypatch, tmp_path):
    home = str(tmp_path / "mucli-home")
    os.makedirs(os.path.join(home, "sessions"), exist_ok=True)
    monkeypatch.setenv("MUCLI_HOME", home)
    import utils.config as _config

    monkeypatch.setattr(_config, "HISTORY_DIR", home)
    return home


def test_concurrent_sessions_searching_never_see_each_others_sources(monkeypatch):
    """Two sessions dispatch web_search at the same time; the search body runs
    on its own timeout thread, which must still resolve its own session."""
    barrier = threading.Barrier(2)

    def fake_ddgs(query, max_results):
        barrier.wait(timeout=10)
        return [{"title": f"{query} {i}", "href": f"https://{query}.example/{i}", "body": "b"}
                for i in range(5)]

    monkeypatch.setattr(research_handlers, "_ddgs_text_search", fake_ddgs)
    alpha, beta = research_session(), research_session()
    errors = []

    def run(session, query):
        try:
            dispatch("web_search", {"query": query}, None, variables=session.variables, session=session)
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(alpha, "alpha")),
               threading.Thread(target=run, args=(beta, "beta"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors
    alpha_urls = {s.url for s in alpha.session_manager.research.all_sources()}
    beta_urls = {s.url for s in beta.session_manager.research.all_sources()}
    assert len(alpha_urls) == 5 and all("alpha" in u for u in alpha_urls)
    assert len(beta_urls) == 5 and all("beta" in u for u in beta_urls)
    # Nothing leaked into the process-wide fallback either.
    assert not any("alpha" in s.url or "beta" in s.url for s in get_citation_manager().all_sources())


def test_bound_state_is_per_thread():
    a, b = ResearchState(), ResearchState()
    seen = {}

    def run(name, state):
        with use_research_state(state):
            get_citation_manager().add_question(f"{name} question")
            seen[name] = [q.text for q in get_citation_manager().questions()]

    threads = [threading.Thread(target=run, args=("a", a)), threading.Thread(target=run, args=("b", b))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert seen == {"a": ["a question"], "b": ["b question"]}


def test_research_state_persists_with_the_session(isolated_home):
    sm = SessionManager(session_name="research-persist")
    sm.history = [{"role": "user", "parts": [{"type": "text", "text": "hi"}]}]
    sm.research.add_question("Why is the sky blue?")
    source = sm.research.mark_read(url="https://physics.example/rayleigh", title="Rayleigh",
                                   text="Rayleigh scattering explains the colour of the sky. " * 10)
    sm.research.record_claim("q1", "Rayleigh scattering", evidence=[{
        "citation_id": source.citation_id, "stance": "supports",
        "excerpt": "Rayleigh scattering explains the colour of the sky."}])
    sm.save_history()

    reloaded = SessionManager(session_name="research-persist")
    assert reloaded.research is not sm.research
    assert reloaded.research.to_dict() == sm.research.to_dict()
    assert reloaded.research.claims()[0].text == "Rayleigh scattering"


def test_earlier_list_shaped_snapshot_loads(isolated_home):
    sm = SessionManager(session_name="research-legacy")
    sm.history = [{"role": "user", "parts": [{"type": "text", "text": "hi"}]}]
    sm.save_history()
    path = os.path.join(isolated_home, "sessions", "research-legacy", "session.json")
    doc = json.load(open(path))
    doc["research_sources"] = [
        {"id": 3, "title": "Old paper", "url": "https://arxiv.org/abs/2101.00001",
         "source_type": "academic", "credibility_score": 0.7, "topic": "transformers"},
        {"id": 5, "title": "Old blog", "url": "https://blog.example/post", "source_type": "web"},
        {"id": "bad"},
    ]
    json.dump(doc, open(path, "w"))

    state = SessionManager(session_name="research-legacy").research
    numbered = state.numbered_sources()
    assert [s.citation_id for s in numbered] == [3, 5]
    assert numbered[0].state == "cited"
    assert state.group_label(numbered[0]) == "transformers"
    assert "[^3]: Old paper." in state.compile_bibliography()
    assert state.mark_read(url="https://new.example/x", title="x", text="t").citation_id == 6
