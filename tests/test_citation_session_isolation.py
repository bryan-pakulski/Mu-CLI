"""
Tests verifying citation/research source isolation per session.

Covers:
- new_session() clears the global CitationManager singleton
- switch_session() restores sources from persisted data
- persistence round-trip (snapshot -> save -> load -> restore) preserves sources
"""

import pytest
from utils.citation_manager import (
    SourceType,
    reset_citation_manager,
    get_citation_manager,
    register_source,
)


@pytest.fixture(autouse=True)
def _isolate_history(tmp_path, monkeypatch):
    """Point HISTORY_DIR at a temp dir so sessions don't clobber real data."""
    monkeypatch.setattr("utils.config.HISTORY_DIR", str(tmp_path / "history"))
    reset_citation_manager()
    yield
    reset_citation_manager()


# ---------------------------------------------------------------------------
# Test 1: new_session() clears the singleton
# ---------------------------------------------------------------------------

def test_new_session_clears_singleton():
    """After new_session(), the global CitationManager must have 0 sources."""
    from mu.session.session import SessionManager

    sm = SessionManager(session_name="pre-fill")
    # Add a source to the global singleton, simulating research activity
    register_source(
        title="Pre-existing Source",
        url="https://example.com/pre",
        source_type=SourceType.WEB,
    )
    assert get_citation_manager().source_count == 1

    # Snapshot into the session (so it's persisted)
    sm.snapshot_research_sources()
    assert len(sm.research_sources) == 1

    # Create a new session — must reset the singleton
    sm.new_session(name="fresh-session", provider_name="dummy", model_name="dummy")

    assert get_citation_manager().source_count == 0
    assert sm.research_sources == []


# ---------------------------------------------------------------------------
# Test 2: switch_session() restores sources from persisted data
# ---------------------------------------------------------------------------

def test_switch_session_restores_sources():
    """switch_session() must hydrate the singleton from the target session's
    persisted research_sources."""
    from mu.session.session import SessionManager

    # Session A: add a source and persist
    sm = SessionManager(session_name="session-a")
    register_source(
        title="Source A",
        url="https://example.com/a",
        source_type=SourceType.ACADEMIC,
    )
    sm.snapshot_research_sources()
    sm.save_history()
    assert len(sm.research_sources) == 1

    # Session B: add a different source and persist
    reset_citation_manager()
    sm.new_session(name="session-b", provider_name="dummy", model_name="dummy")
    register_source(
        title="Source B",
        url="https://example.com/b",
        source_type=SourceType.WEB,
    )
    sm.snapshot_research_sources()
    sm.save_history()
    assert len(sm.research_sources) == 1

    # Switch back to session A — singleton must be restored from A's data
    sm.switch_session("session-a")

    cm = get_citation_manager()
    assert cm.source_count == 1
    sources = cm.get_all_sources()
    assert sources[0].title == "Source A"


# ---------------------------------------------------------------------------
# Test 3: persistence round-trip preserves sources
# ---------------------------------------------------------------------------

def test_persistence_roundtrip_preserves_sources():
    """Full round-trip: add sources -> snapshot -> save -> new SessionManager
    -> load -> restore -> sources match."""
    from mu.session.session import SessionManager

    # Phase 1: create session, add sources, persist
    sm1 = SessionManager(session_name="persist-test")
    register_source(
        title="Academic Paper",
        url="https://arxiv.org/abs/1234",
        source_type=SourceType.ACADEMIC,
        authors=["Jane Doe"],
        date="2024-01-15",
    )
    register_source(
        title="Forum Post",
        url="https://news.ycombinator.com/item/123",
        source_type=SourceType.FORUM,
    )
    sm1.snapshot_research_sources()
    sm1.save_history()
    assert len(sm1.research_sources) == 2

    # Phase 2: simulate process restart — new SessionManager, load the session
    reset_citation_manager()
    assert get_citation_manager().source_count == 0

    sm2 = SessionManager(session_name="persist-test")

    # Phase 3: restore research sources from persisted data
    sm2.restore_research_sources()

    cm = get_citation_manager()
    assert cm.source_count == 2
    sources = cm.get_all_sources()
    titles = {s.title for s in sources}
    assert "Academic Paper" in titles
    assert "Forum Post" in titles

    # Verify source metadata survived the round-trip
    academic = next(s for s in sources if s.title == "Academic Paper")
    assert academic.authors == ["Jane Doe"]
    assert academic.date == "2024-01-15"
    assert academic.source_type == SourceType.ACADEMIC