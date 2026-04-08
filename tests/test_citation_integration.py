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
    """Test credibility scoring for web sources."""
    score = calculate_credibility_score(
        source_type=SourceType.WEB,
        metadata={}
    )
    # Base score for WEB is 0.5
    assert 0.0 <= score <= 1.0
    assert score == 0.5


def test_credibility_score_academic():
    """Test credibility scoring for academic sources."""
    score = calculate_credibility_score(
        source_type=SourceType.ACADEMIC,
        metadata={}
    )
    # Base score for ACADEMIC is 0.8
    assert 0.0 <= score <= 1.0
    assert score == 0.8


def test_credibility_score_social():
    """Test credibility scoring for social sources."""
    score = calculate_credibility_score(
        source_type=SourceType.SOCIAL,
        metadata={}
    )
    # Base score for SOCIAL is 0.3
    assert 0.0 <= score <= 1.0
    assert score == 0.3


def test_credibility_score_with_authors():
    """Test credibility scoring with authors boost."""
    score = calculate_credibility_score(
        source_type=SourceType.ACADEMIC,
        metadata={"authors": ["John Doe"]}
    )
    # Base 0.8 + 0.1 for authors = 0.9
    assert score == 0.9


def test_credibility_score_capped():
    """Test credibility scoring is capped at 1.0."""
    score = calculate_credibility_score(
        source_type=SourceType.ACADEMIC,
        metadata={"authors": ["John Doe"], "peer_reviewed": True}
    )
    # Base 0.8 + 0.1 for authors + 0.1 for peer review = 1.0 (capped)
    assert score == 1.0


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
    assert source.credibility_score == 0.8  # Academic base score


def test_bibliography_includes_credibility():
    """Test that bibliography includes credibility indicators."""
    reset_citation_manager()
    manager = CitationManager()
    manager.add_source(
        title="Test Academic",
        url="https://arxiv.org/paper",
        source_type=SourceType.ACADEMIC,
    )
    bibliography = manager.compile_bibliography()
    assert "Credibility:" in bibliography
    assert "★" in bibliography  # Stars for credibility visualization