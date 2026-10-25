"""Search results are candidates: never numbered, never in the bibliography,
until the agent reads them."""

import json

from mu.tools._dispatcher import dispatch
from mu.tools.research import handlers as research_handlers

from research_helpers import article, research_session, serve_pages


def _data(envelope: str):
    payload = json.loads(envelope)
    assert payload["ok"], payload
    message = payload["message"]
    return json.loads(message) if message.lstrip().startswith("{") else message


def test_search_rows_are_candidates_until_read(monkeypatch):
    paragraphs = ["Solid-state batteries use a solid electrolyte instead of a liquid one. " * 3,
                  "Energy density gains of 50 percent have been reported in lab cells. " * 3]
    with serve_pages({"/batteries": ("text/html", article("Solid-state batteries", paragraphs))}) as base:
        rows = [{"title": f"Result {i}", "href": f"{base}/r{i}", "body": f"snippet {i}"} for i in range(9)]
        rows.append({"title": "Batteries", "href": f"{base}/batteries?utm_source=feed", "body": "solid"})
        monkeypatch.setattr(research_handlers, "_ddgs_text_search", lambda q, max_results: rows)
        session = research_session()
        state = session.session_manager.research

        found = _data(dispatch("web_search", {"query": "solid state"}, None,
                               variables=session.variables, session=session))
        assert len(found["results"]) == 10
        assert all(r["source_id"].startswith("s") and "citation_id" not in r for r in found["results"])
        assert state.compile_bibliography() == ""
        assert state.source_count == 0

        # Reading the candidate by its id (its URL carries a tracking param).
        sid = found["results"][-1]["source_id"]
        text = _data(dispatch("url_grounding", {"url": sid}, None,
                              variables=session.variables, session=session))
        assert "[^1]" in text
        bib = state.compile_bibliography()
        assert bib.count("[^") == 1 and "Solid-state batteries" in bib

        # The same page found again (clean URL) keeps its number and is not re-listed.
        again = _data(dispatch("web_search", {"query": "solid state"}, None,
                               variables=session.variables, session=session))
        rows_by_url = {r["url"]: r for r in again["results"]}
        assert rows_by_url[f"{base}/batteries?utm_source=feed"]["citation_id"] == 1
        assert len(state.all_sources()) == 10
