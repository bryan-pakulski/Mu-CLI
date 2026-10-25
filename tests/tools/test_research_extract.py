"""Reading a source: clean main content, metadata, bounded query-ranked
passages, full text kept, mirrors merged — served from loopback only."""

import json

import pytest

from mu.research import extract as extract_mod
from mu.tools._dispatcher import dispatch
from mu.tools.research.handlers import research_fetch, url_grounding

from research_helpers import article, research_session, serve_pages, tool_context

META = (
    '<meta name="citation_author" content="Ada Lovelace">'
    '<meta name="citation_author" content="Charles Babbage">'
    '<meta name="citation_publication_date" content="2024-05-01">'
    '<meta property="og:site_name" content="Engine Review">'
    '<link rel="canonical" href="/engines/analytical">'
)

INTRO = [
    "The analytical engine was a proposed mechanical general-purpose computer. " * 4,
    "Its design included an arithmetic logic unit, control flow and integrated memory. " * 4,
]
SECTIONS = [
    ("Funding", "The British government stopped funding after spending seventeen thousand pounds. " * 4),
    ("Programming", "Lovelace wrote the first published algorithm intended for the machine, computing Bernoulli numbers. " * 4),
    ("Legacy", "Later historians debated how close the engine came to being built in its era. " * 4),
]


def _page() -> bytes:
    body = "".join(f"<h2>{h}</h2><p>{t}</p>" for h, t in SECTIONS)
    return article("The Analytical Engine", INTRO, extra_head=META).replace(
        b"</article>", body.encode() + b"</article>")


@pytest.fixture(params=["trafilatura", "bs4"])
def extractor(request, monkeypatch):
    if request.param == "bs4":
        monkeypatch.setattr(extract_mod, "_trafilatura", lambda: None)
    else:
        pytest.importorskip("trafilatura")
    return request.param


def test_reading_returns_clean_bounded_query_ranked_text(extractor):
    session = research_session()
    with serve_pages({"/engine": ("text/html; charset=utf-8", _page())}) as base:
        out = url_grounding(f"{base}/engine?utm_campaign=x", query="Bernoulli numbers algorithm",
                            max_chars=1500, context=tool_context(session))

    assert out.startswith("# The Analytical Engine")
    assert extractor in out
    assert "cookies" not in out and "All rights reserved" not in out and "About" not in out
    assert len(out) <= 1500 + 400
    first_passage = out.split("\n## ", 1)[1]
    assert first_passage.startswith("Programming")

    state = session.session_manager.research
    source = state.get_source(1)
    assert source.state == "read"
    assert source.authors == ["Ada Lovelace", "Charles Babbage"]
    assert source.date == "2024-05-01"
    assert source.site == "Engine Review"
    # The full text is recallable even though only some passages came back inline.
    full = session.tool_result_cache.recall(source.result_ref)["result"]
    assert "seventeen thousand pounds" in full and "historians debated" in full
    # The text kept for excerpt checks is the same text.
    state.add_question("Who programmed it?")
    state.record_claim("q1", "Lovelace", evidence=[{
        "citation_id": 1, "stance": "supports",
        "excerpt": "Lovelace wrote the first published algorithm intended for the machine"}])


def test_without_a_query_the_current_question_ranks_passages():
    session = research_session()
    session.session_manager.research.add_question("Why did the government stop funding?")
    with serve_pages({"/engine": ("text/html", _page())}) as base:
        out = url_grounding(f"{base}/engine", max_chars=1200, context=tool_context(session))
    assert out.split("\n## ", 1)[1].startswith("Funding")


def test_mirror_with_identical_text_merges_into_the_earlier_source():
    session = research_session()
    page = _page().replace(META.encode(), b"")
    with serve_pages({"/a": ("text/html", page), "/mirror/copy": ("text/html", page)}) as base:
        url_grounding(f"{base}/a", context=tool_context(session))
        url_grounding(f"{base}/mirror/copy", context=tool_context(session))
    state = session.session_manager.research
    assert [s.citation_id for s in state.numbered_sources()] == [1]
    assert state.get_source(1).aliases == [f"{base}/mirror/copy"]


def test_fetch_reads_several_urls_and_reports_failures_individually():
    session = research_session()
    pages = {
        f"/p{i}": ("text/html", article(f"Page {i}", [f"Distinct topic number {i} discussed at length. " * 8]))
        for i in range(3)
    }
    with serve_pages(pages) as base:
        out = research_fetch([f"{base}/p0", f"{base}/missing", f"{base}/p1", f"{base}/p2"],
                             max_chars=8000, context=tool_context(session))
    assert out.startswith("Read 3/4 URL(s).")
    assert f"failed: {base}/missing — HTTP 404" in out
    assert all(f"# Page {i}" in out for i in range(3))
    assert session.session_manager.research.source_count == 3


def test_fetch_is_one_read_only_call_without_approval():
    session = research_session()
    pages = {f"/p{i}": ("text/html", article(f"P{i}", ["Text body. " * 40])) for i in range(2)}
    with serve_pages(pages) as base:
        envelope = json.loads(dispatch("research_fetch", {"urls": [f"{base}/p0", f"{base}/p1"]}, None,
                                       variables=session.variables, session=session))
    assert envelope["ok"]
    from mu.tools.descriptors import get_tool_descriptor

    assert get_tool_descriptor("research_fetch").definition.requires_approval is False


def test_unreadable_pages_are_reported_not_recorded(monkeypatch):
    from mu.tools.research import handlers

    rendered = []
    monkeypatch.setattr(handlers, "_render_with_playwright", lambda url: rendered.append(url) or None)
    session = research_session()
    with serve_pages({"/empty": ("text/html", b"<html><body><nav>menu</nav></body></html>"),
                      "/bin": ("application/octet-stream", b"\x00\x01")}) as base:
        empty = url_grounding(f"{base}/empty", context=tool_context(session))
        binary = url_grounding(f"{base}/bin", context=tool_context(session))
    assert empty.startswith("Error reading") and "try another source" in empty
    assert binary.startswith("Error reading") and "unsupported content type" in binary
    assert rendered == [f"{base}/empty"]  # a near-empty page gets one JS-rendered retry
    assert session.session_manager.research.source_count == 0
