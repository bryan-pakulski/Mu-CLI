"""
Tests for citation integration with research tools.
"""

import pytest
from utils.citation_manager import (
    CitationManager,
    SourceType,
    Source,
    reset_citation_manager,
    get_citation_manager,
    calculate_credibility_score,
)


def test_citation_manager_singleton():
    """Test that get_citation_manager returns consistent instance."""
    reset_citation_manager()
    manager1 = get_citation_manager()
    manager2 = get_citation_manager()
    assert manager1 is manager2


def test_add_source_returns_citation_id():
    """Test that add_source returns a citation ID (int)."""
    reset_citation_manager()
    manager = CitationManager()
    citation_id = manager.add_source(
        title="Example",
        url="https://example.com",
        source_type=SourceType.WEB,
    )
    assert isinstance(citation_id, int)
    assert citation_id == 1


def test_generate_citation():
    """Test generating a citation reference."""
    reset_citation_manager()
    manager = CitationManager()
    citation_id = manager.add_source(
        title="Example",
        url="https://example.com",
        source_type=SourceType.WEB,
    )
    ref = manager.generate_citation(citation_id)
    assert ref == "[^1]"


def test_credibility_score_web():
    """Web sources default to 0.0 (unassessed)."""
    score = calculate_credibility_score(
        source_type=SourceType.WEB,
        metadata={}
    )
    assert 0.0 <= score <= 1.0
    assert score == 0.0  # unassessed default


def test_credibility_score_academic():
    """Academic sources default to 0.0 (unassessed)."""
    score = calculate_credibility_score(
        source_type=SourceType.ACADEMIC,
        metadata={}
    )
    assert 0.0 <= score <= 1.0
    assert score == 0.0  # unassessed default


def test_credibility_score_social():
    """Social sources default to 0.0 (unassessed)."""
    score = calculate_credibility_score(
        source_type=SourceType.SOCIAL,
        metadata={}
    )
    assert 0.0 <= score <= 1.0
    assert score == 0.0  # unassessed default


def test_credibility_score_with_authors():
    """Authors no longer boost credibility; defaults 0.0."""
    score = calculate_credibility_score(
        source_type=SourceType.ACADEMIC,
        metadata={"authors": ["John Doe"]}
    )
    assert score == 0.0  # no longer metadata-boosted


def test_credibility_score_capped():
    """Peer review no longer boosts; defaults 0.0."""
    score = calculate_credibility_score(
        source_type=SourceType.ACADEMIC,
        metadata={"authors": ["John Doe"], "peer_reviewed": True}
    )
    assert score == 0.0  # no longer metadata-boosted


def test_source_dataclass():
    """Test Source dataclass includes credibility_score."""
    source = Source(
        id=1,
        url="https://example.com",
        title="Test",
        source_type=SourceType.WEB,
        authors=[],
        date=None,
        metadata={},
        credibility_score=0.8,
    )
    assert source.credibility_score == 0.8


def test_source_credibility_calculated():
    """Test that credibility_score is calculated on add_source."""
    reset_citation_manager()
    manager = CitationManager()
    citation_id = manager.add_source(
        title="Test",
        url="https://example.com",
        source_type=SourceType.ACADEMIC,
    )
    source = manager.get_source(citation_id)
    assert source.credibility_score == 0.0  # unassessed until assess_source


def test_model_assessment_varies_web_source_within_hard_cap():
    manager = CitationManager()
    source_id = manager.add_source("Evidence", "https://example.com", SourceType.WEB)
    source = manager.assess_source(source_id, 0.37, "Weakly corroborated summary")
    assert source.credibility_score == 0.37
    assert source.metadata["model_importance"] == 0.37

    capped = manager.assess_source(source_id, 0.99, "Useful, but still a web source")
    assert capped.credibility_score == 0.80
    assert capped.metadata["credibility_cap"] == 0.80


def test_bibliography_includes_credibility():
    """Test that bibliography includes credibility indicators."""
    reset_citation_manager()
    manager = CitationManager()
    cid = manager.add_source(
        title="Test Academic",
        url="https://arxiv.org/paper",
        source_type=SourceType.ACADEMIC,
    )
    manager.assess_source(cid, 0.9, "Well-corroborated peer-reviewed paper")
    bibliography = manager.compile_bibliography()
    assert "Credibility:" in bibliography
    assert "★" in bibliography  # Stars for credibility visualization


# --------------------------------------------------------------------------- topic grouping

def test_set_topic_returns_topic():
    reset_citation_manager()
    manager = CitationManager()
    assert manager.set_topic("rabbit hole A") == "rabbit hole A"
    assert manager.get_current_topic() == "rabbit hole A"

def test_set_topic_empty_falls_back_to_default():
    reset_citation_manager()
    manager = CitationManager()
    manager.set_topic("  ")
    assert manager.get_current_topic() == "general"

def test_source_inherits_active_topic():
    reset_citation_manager()
    manager = CitationManager()
    manager.set_topic("schema world models")
    cid = manager.add_source("Paper", "https://example.com/p", SourceType.ACADEMIC)
    source = manager.get_source(cid)
    assert source.topic == "schema world models"

def test_source_explicit_topic_override():
    reset_citation_manager()
    manager = CitationManager()
    manager.set_topic("topic A")
    cid = manager.add_source("Paper", "https://example.com/p", SourceType.ACADEMIC, topic="topic B")
    source = manager.get_source(cid)
    assert source.topic == "topic B"

def test_list_topics_first_seen_order():
    reset_citation_manager()
    manager = CitationManager()
    manager.set_topic("alpha")
    manager.add_source("A", "https://a.io", SourceType.WEB)
    manager.set_topic("beta")
    manager.add_source("B", "https://b.io", SourceType.WEB)
    assert manager.list_topics() == ["alpha", "beta"]

def test_get_sources_by_topic():
    reset_citation_manager()
    manager = CitationManager()
    manager.set_topic("alpha")
    manager.add_source("A1", "https://a1.io", SourceType.WEB)
    manager.add_source("A2", "https://a2.io", SourceType.WEB)
    manager.set_topic("beta")
    manager.add_source("B1", "https://b1.io", SourceType.WEB)
    assert len(manager.get_sources_by_topic("alpha")) == 2
    assert len(manager.get_sources_by_topic("beta")) == 1
    assert len(manager.get_sources_by_topic("gamma")) == 0

def test_bibliography_grouped_by_topic():
    reset_citation_manager()
    manager = CitationManager()
    manager.set_topic("alpha")
    manager.add_source("A1", "https://a1.io", SourceType.WEB)
    manager.set_topic("beta")
    manager.add_source("B1", "https://b1.io", SourceType.WEB)
    bib = manager.compile_bibliography()
    assert "### alpha" in bib
    assert "### beta" in bib
    assert bib.index("### alpha") < bib.index("### beta")

def test_bibliography_single_topic_filter():
    reset_citation_manager()
    manager = CitationManager()
    manager.set_topic("alpha")
    manager.add_source("A1", "https://a1.io", SourceType.WEB)
    manager.set_topic("beta")
    manager.add_source("B1", "https://b1.io", SourceType.WEB)
    bib = manager.compile_bibliography(topic="alpha")
    assert "### alpha" in bib
    assert "### beta" not in bib

def test_unassessed_source_shows_unassessed_in_bibliography():
    reset_citation_manager()
    manager = CitationManager()
    manager.add_source("S", "https://s.io", SourceType.WEB)
    bib = manager.compile_bibliography()
    assert "unassessed" in bib

def test_clear_resets_topic():
    reset_citation_manager()
    manager = CitationManager()
    manager.set_topic("alpha")
    manager.add_source("A", "https://a.io", SourceType.WEB)
    manager.clear()
    assert manager.get_current_topic() == "general"
    assert manager.list_topics() == []

def test_load_dict_preserves_topic():
    reset_citation_manager()
    manager = CitationManager()
    manager.set_topic("alpha")
    manager.add_source("A", "https://a.io", SourceType.WEB)
    snapshot = manager.to_dict()
    manager2 = CitationManager()
    manager2.load_dict(snapshot)
    assert manager2.get_source(1).topic == "alpha"
    assert "alpha" in manager2.list_topics()

def test_load_dict_legacy_record_without_topic():
    reset_citation_manager()
    manager = CitationManager()
    manager.load_dict([{"id": 1, "title": "Old", "url": "https://old.io", "source_type": "web"}])
    source = manager.get_source(1)
    assert source.topic == "general"
