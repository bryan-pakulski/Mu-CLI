"""Regression pin for the `/research sources` crash.

The bug: `register_source` was type-annotated as taking `SourceType`,
but several call sites in `core/tools.py` (web_search HTML scrape,
InstantAnswer fallback) passed plain strings (`"web"`). The string
landed in storage verbatim. Later `/research sources` did
`source.source_type.value` and crashed with
`AttributeError: 'str' object has no attribute 'value'`.

Two fixes:
  1. `CitationManager.add_source` coerces strings → enum at the boundary.
  2. `_source_to_dict` defensively handles legacy string source_type
     so sources already in memory don't keep crashing.
"""

import pytest

from utils.citation_manager import (
    CitationManager,
    Source,
    SourceType,
    register_source,
    reset_citation_manager,
)


@pytest.fixture(autouse=True)
def fresh_engine():
    reset_citation_manager()
    yield
    reset_citation_manager()


# ----------------------------------------------- boundary coercion


def test_add_source_coerces_string_source_type():
    """Plain string source_type must be coerced to the matching enum."""
    cm = CitationManager()
    cid = cm.add_source(
        title="Example",
        url="https://example.com/a",
        source_type="web",  # type: ignore[arg-type]
    )
    src = cm.get_source(cid)
    assert isinstance(src.source_type, SourceType)
    assert src.source_type == SourceType.WEB


def test_add_source_coerces_each_valid_string():
    cm = CitationManager()
    for value in ("web", "academic", "social", "forum", "news", "documentation", "other"):
        cid = cm.add_source(
            title=f"t {value}",
            url=f"https://example.com/{value}",
            source_type=value,  # type: ignore[arg-type]
        )
        src = cm.get_source(cid)
        assert isinstance(src.source_type, SourceType)
        assert src.source_type.value == value


def test_add_source_unknown_string_falls_back_to_other():
    cm = CitationManager()
    cid = cm.add_source(
        title="weird",
        url="https://example.com/x",
        source_type="not-a-real-type",  # type: ignore[arg-type]
    )
    src = cm.get_source(cid)
    assert src.source_type == SourceType.OTHER


def test_add_source_accepts_proper_enum_unchanged():
    """The enum path must still work — coercion is additive, not
    replacing."""
    cm = CitationManager()
    cid = cm.add_source(
        title="proper",
        url="https://example.com/p",
        source_type=SourceType.ACADEMIC,
    )
    assert cm.get_source(cid).source_type is SourceType.ACADEMIC


def test_register_source_helper_coerces_too():
    """The module-level `register_source` shortcut routes through
    `add_source`, so coercion must apply via that path as well —
    that's the entry point web_search uses."""
    cid = register_source(
        title="via helper",
        url="https://example.com/h",
        source_type="web",  # type: ignore[arg-type]
    )
    from utils.citation_manager import get_citation_manager

    src = get_citation_manager().get_source(cid)
    assert isinstance(src.source_type, SourceType)


# ----------------------------------------------- defensive renderer


def test_research_sources_handles_legacy_string_type():
    """Pin the user-visible crash. Even if a Source slipped in with a
    string source_type (pre-fix data, pickled state, etc.),
    `/research sources` must NOT crash."""
    import mu.commands as mc
    from mu.session.session import Session, SessionManager
    from providers.base import LLMProvider, ProviderResponse
    from utils.citation_manager import get_citation_manager

    class _DummyProvider(LLMProvider):
        def get_available_models(self):
            return ["dummy"]

        def generate(self, *a, **k):
            return ProviderResponse(text="", parts=[])

        def upload_file(self, *a, **k):
            return None

    sm = SessionManager()
    session = Session(_DummyProvider("dummy"), False, "sys", sm)
    session._mcp_clients = []
    session.session_manager.history = []
    session.session_manager.summary_anchor = 0
    session.session_manager.conversation_summary = ""

    # Inject a legacy source with a STRING source_type, bypassing
    # `add_source`'s coercion to simulate the bad state that already
    # exists in a running session.
    engine = get_citation_manager()
    legacy = Source(
        id=999,
        title="Legacy string-typed",
        url="https://example.com/legacy",
        source_type="web",  # type: ignore[arg-type]
        credibility_score=0.5,
    )
    engine._sources[999] = legacy
    engine._source_urls[legacy.url] = 999

    # Pre-fix this used to crash with AttributeError. Post-fix it
    # renders fine.
    result = mc.dispatch(session, "/research sources", allow_prompt=False)
    assert result.ok
    titles = {s["title"] for s in result.data["sources"]}
    assert "Legacy string-typed" in titles
    # The "type" field is the string value, not a torn enum.
    legacy_entry = next(s for s in result.data["sources"] if s["id"] == 999)
    assert legacy_entry["type"] == "web"
