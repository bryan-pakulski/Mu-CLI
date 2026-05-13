"""Pin the /research command's wiring to the CitationManager engine.

Before this rewrite, `/research sources` just regex-scanned the
conversation history for `https://...` strings and returned bare URLs.
The CitationManager — which the research tools (web_search,
arxiv_search, etc.) actually register sources into with credibility
scores and dedupe — was hidden from the CLI surface.

This file pins that every subcommand reads from the real engine.
"""

import pytest

import mu.commands as mc
from core.session import Session, SessionManager
from providers.base import LLMProvider, ProviderResponse
from utils.citation_manager import (
    SourceType,
    get_citation_manager,
    register_source,
    reset_citation_manager,
)


class _DummyProvider(LLMProvider):
    def get_available_models(self):
        return ["dummy"]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        return ProviderResponse(text="", parts=[])

    def upload_file(self, file_path, mime_type):
        return None


@pytest.fixture
def session():
    sm = SessionManager()
    s = Session(_DummyProvider("dummy"), False, "sys", sm)
    s._mcp_clients = []
    s.session_manager.history = []
    s.session_manager.summary_anchor = 0
    s.session_manager.conversation_summary = ""
    return s


@pytest.fixture(autouse=True)
def fresh_citation_engine():
    """Reset the global citation registry between tests so they don't
    bleed sources across each other."""
    reset_citation_manager()
    yield
    reset_citation_manager()


def _seed_sources():
    """Register a representative mix of sources via the same path
    research tools use."""
    register_source(
        title="Attention Is All You Need",
        url="https://arxiv.org/abs/1706.03762",
        source_type=SourceType.ACADEMIC,
        authors=["Vaswani", "Shazeer"],
        date="2017-06-12",
    )
    register_source(
        title="Python docs — asyncio",
        url="https://docs.python.org/3/library/asyncio.html",
        source_type=SourceType.DOCUMENTATION,
    )
    register_source(
        title="r/MachineLearning — discussion",
        url="https://reddit.com/r/MachineLearning/abc",
        source_type=SourceType.SOCIAL,
    )
    register_source(
        title="StackOverflow — q42",
        url="https://stackoverflow.com/q/42",
        source_type=SourceType.FORUM,
    )


# ----------------------------------------------- status


def test_status_reads_from_engine(session):
    _seed_sources()
    result = mc.dispatch(session, "/research", allow_prompt=False)
    assert result.ok
    assert result.data["source_count"] == 4
    assert result.data["by_type"]["academic"] == 1
    assert result.data["by_type"]["documentation"] == 1
    assert result.data["by_type"]["social"] == 1
    assert result.data["by_type"]["forum"] == 1
    assert result.data["avg_credibility"] > 0


def test_status_when_empty(session):
    result = mc.dispatch(session, "/research status", allow_prompt=False)
    assert result.ok
    assert result.data["source_count"] == 0
    assert result.data["avg_credibility"] == 0.0


# ----------------------------------------------- sources listing


def test_sources_lists_every_registered_source(session):
    _seed_sources()
    result = mc.dispatch(session, "/research sources", allow_prompt=False)
    assert result.ok
    assert result.data["count"] == 4
    titles = {s["title"] for s in result.data["sources"]}
    assert "Attention Is All You Need" in titles


def test_sources_sorted_by_credibility_desc(session):
    _seed_sources()
    result = mc.dispatch(session, "/research sources", allow_prompt=False)
    creds = [s["credibility"] for s in result.data["sources"]]
    assert creds == sorted(creds, reverse=True), creds


def test_sources_includes_full_metadata_not_just_urls(session):
    """Regression-pin for the original bug: `/research sources` used
    to return bare URL strings via regex scan. Now each source carries
    id/title/type/credibility/authors/accessed."""
    _seed_sources()
    result = mc.dispatch(session, "/research sources", allow_prompt=False)
    first = result.data["sources"][0]
    for required in ("id", "title", "url", "type", "credibility", "authors", "accessed"):
        assert required in first, f"missing {required!r} on returned source: {first}"


def test_sources_filter_by_type(session):
    _seed_sources()
    result = mc.dispatch(session, "/research sources --type academic", allow_prompt=False)
    assert result.ok
    assert result.data["count"] == 1
    assert result.data["sources"][0]["type"] == "academic"


def test_sources_filter_min_credibility(session):
    _seed_sources()
    # Only the academic + documentation entries score ≥0.7
    result = mc.dispatch(session, "/research sources --min 0.7", allow_prompt=False)
    assert result.ok
    for src in result.data["sources"]:
        assert src["credibility"] >= 0.7


def test_sources_filter_query_substring(session):
    _seed_sources()
    result = mc.dispatch(session, "/research sources --query asyncio", allow_prompt=False)
    assert result.ok
    assert result.data["count"] == 1
    assert "asyncio" in result.data["sources"][0]["title"].lower()


def test_sources_unknown_flag_errors(session):
    result = mc.dispatch(session, "/research sources --weird foo", allow_prompt=False)
    assert not result.ok
    assert "unknown flag" in result.message


def test_sources_min_must_be_number(session):
    result = mc.dispatch(session, "/research sources --min hello", allow_prompt=False)
    assert not result.ok


def test_sources_invalid_type_errors(session):
    result = mc.dispatch(session, "/research sources --type bogus", allow_prompt=False)
    assert not result.ok
    assert "unknown --type" in result.message


# ----------------------------------------------- show one


def test_show_returns_full_record(session):
    _seed_sources()
    result = mc.dispatch(session, "/research show 1", allow_prompt=False)
    assert result.ok
    assert result.data["id"] == 1
    assert result.data["title"] == "Attention Is All You Need"
    assert result.data["authors"] == ["Vaswani", "Shazeer"]


def test_show_accepts_hash_prefix(session):
    _seed_sources()
    result = mc.dispatch(session, "/research show #2", allow_prompt=False)
    assert result.ok
    assert result.data["id"] == 2


def test_show_missing_id_errors(session):
    _seed_sources()
    result = mc.dispatch(session, "/research show 999", allow_prompt=False)
    assert not result.ok


def test_show_non_integer_errors(session):
    result = mc.dispatch(session, "/research show abc", allow_prompt=False)
    assert not result.ok


# ----------------------------------------------- bibliography


def test_bibliography_emits_compiled_markdown(session):
    _seed_sources()
    result = mc.dispatch(session, "/research bibliography", allow_prompt=False)
    assert result.ok
    body = result.data["bibliography"]
    # The CitationManager's bibliography contains a markdown header and
    # footnote-style entries.
    assert "## Bibliography" in body
    assert "[^1]" in body
    assert "Attention Is All You Need" in body


def test_bibliography_aliases(session):
    """`biblio` and `bib` are accepted shortcuts."""
    _seed_sources()
    for alias in ("biblio", "bib"):
        result = mc.dispatch(session, f"/research {alias}", allow_prompt=False)
        assert result.ok
        assert "Bibliography" in result.data["bibliography"]


def test_bibliography_empty_engine(session):
    result = mc.dispatch(session, "/research bibliography", allow_prompt=False)
    assert result.ok
    assert result.data["bibliography"] == ""


# ----------------------------------------------- stats


def test_stats_returns_breakdown_and_average(session):
    _seed_sources()
    result = mc.dispatch(session, "/research stats", allow_prompt=False)
    assert result.ok
    assert result.data["count"] == 4
    assert "academic" in result.data["by_type"]
    assert 0 < result.data["avg_credibility"] <= 1.0


# ----------------------------------------------- clear


def test_clear_wipes_engine(session):
    _seed_sources()
    assert get_citation_manager().source_count == 4
    result = mc.dispatch(session, "/research clear", allow_prompt=False)
    assert result.ok
    assert result.data["cleared"] == 4
    assert get_citation_manager().source_count == 0


# ----------------------------------------------- autocomplete


def test_research_autocomplete_lists_subcommands():
    """`/research <Tab>` offers every subcommand."""
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document

    from ui.input import InputHandler

    handler = InputHandler()
    doc = Document(text="/research ", cursor_position=len("/research "))
    completions = list(
        handler.completer.get_completions(doc, CompleteEvent(completion_requested=True))
    )
    texts = {c.text for c in completions}
    for sub in ("status", "sources", "show", "bibliography", "stats", "clear"):
        assert sub in texts, f"missing /research subcommand {sub!r} in autocomplete"
