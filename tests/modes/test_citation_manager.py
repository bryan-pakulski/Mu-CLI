"""
Tests for the CitationManager module.
"""

import pytest
from datetime import datetime
from unittest.mock import patch

from utils.citation_manager import (
    CitationManager,
    Source,
    SourceType,
    get_citation_manager,
    reset_citation_manager,
    register_source,
    get_citation,
    compile_bibliography,
)


class TestSource:
    """Tests for the Source dataclass."""
    
    def test_source_creation(self):
        """Test creating a valid Source."""
        source = Source(
            id=1,
            title="Test Title",
            url="https://example.com",
            source_type=SourceType.WEB
        )
        assert source.id == 1
        assert source.title == "Test Title"
        assert source.url == "https://example.com"
        assert source.source_type == SourceType.WEB
        assert source.authors == []
        assert source.date is None
        assert source.metadata == {}
    
    def test_source_with_authors(self):
        """Test creating a Source with authors."""
        source = Source(
            id=1,
            title="Test Title",
            url="https://example.com",
            source_type=SourceType.ACADEMIC,
            authors=["John Doe", "Jane Smith"]
        )
        assert len(source.authors) == 2
        assert source.authors == ["John Doe", "Jane Smith"]
    
    def test_source_with_date(self):
        """Test creating a Source with a publication date."""
        source = Source(
            id=1,
            title="Test Title",
            url="https://example.com",
            source_type=SourceType.WEB,
            date="2024-01-15"
        )
        assert source.date == "2024-01-15"
    
    def test_source_without_title_raises(self):
        """Test that creating a Source without a title raises an error."""
        with pytest.raises(TypeError):
            Source(id=1, url="https://example.com", source_type=SourceType.WEB)
    
    def test_source_without_url_raises(self):
        """Test that creating a Source without a URL raises an error."""
        with pytest.raises(TypeError):
            Source(id=1, title="Test Title", source_type=SourceType.WEB)


class TestCitationManager:
    """Tests for the CitationManager class."""
    
    def setup_method(self):
        """Create a fresh CitationManager for each test."""
        self.manager = CitationManager()
    
    def test_add_source_returns_id(self):
        """Test that add_source returns a citation ID."""
        cid = self.manager.add_source(
            title="Test Article",
            url="https://example.com/article",
            source_type=SourceType.WEB
        )
        assert cid == 1
    
    def test_add_source_increments_id(self):
        """Test that citation IDs increment."""
        cid1 = self.manager.add_source(
            title="Article 1",
            url="https://example.com/1",
            source_type=SourceType.WEB
        )
        cid2 = self.manager.add_source(
            title="Article 2",
            url="https://example.com/2",
            source_type=SourceType.WEB
        )
        assert cid1 == 1
        assert cid2 == 2
    
    def test_add_source_deduplicates_by_url(self):
        """Test that duplicate URLs return the same citation ID."""
        cid1 = self.manager.add_source(
            title="Article",
            url="https://example.com/article",
            source_type=SourceType.WEB
        )
        cid2 = self.manager.add_source(
            title="Same Article Different Title",
            url="https://example.com/article",
            source_type=SourceType.WEB
        )
        assert cid1 == cid2
        assert self.manager.source_count == 1
    
    def test_get_source_returns_source(self):
        """Test that get_source returns the correct Source."""
        cid = self.manager.add_source(
            title="Test Article",
            url="https://example.com/article",
            source_type=SourceType.WEB,
            authors=["John Doe"]
        )
        source = self.manager.get_source(cid)
        assert source is not None
        assert source.title == "Test Article"
        assert source.authors == ["John Doe"]
    
    def test_get_source_invalid_id_returns_none(self):
        """Test that get_source returns None for invalid IDs."""
        source = self.manager.get_source(999)
        assert source is None
    
    def test_generate_citation_format(self):
        """Test that generate_citation returns correct markdown footnote."""
        cid = self.manager.add_source(
            title="Test Article",
            url="https://example.com/article",
            source_type=SourceType.WEB
        )
        citation = self.manager.generate_citation(cid)
        assert citation == "[^1]"
    
    def test_generate_citation_invalid_id_raises(self):
        """Test that generate_citation raises for invalid IDs."""
        with pytest.raises(ValueError):
            self.manager.generate_citation(999)
    
    def test_compile_bibliography_empty(self):
        """Test that compile_bibliography returns empty string for no sources."""
        bibliography = self.manager.compile_bibliography()
        assert bibliography == ""
    
    def test_compile_bibliography_single_source(self):
        """Test compiling bibliography for a single source."""
        self.manager.add_source(
            title="Test Article",
            url="https://example.com/article",
            source_type=SourceType.WEB
        )
        bibliography = self.manager.compile_bibliography()
        assert "## Bibliography" in bibliography
        assert "[^1]" in bibliography
        assert "Test Article" in bibliography
        assert "https://example.com/article" in bibliography
    
    def test_compile_bibliography_with_authors(self):
        """Test that bibliography includes authors."""
        self.manager.add_source(
            title="Academic Paper",
            url="https://example.com/paper",
            source_type=SourceType.ACADEMIC,
            authors=["Alice Smith", "Bob Jones"]
        )
        bibliography = self.manager.compile_bibliography()
        assert "Alice Smith & Bob Jones" in bibliography
    
    def test_compile_bibliography_with_many_authors(self):
        """Test bibliography format with more than two authors."""
        self.manager.add_source(
            title="Multi-Author Paper",
            url="https://example.com/paper",
            source_type=SourceType.ACADEMIC,
            authors=["Alice", "Bob", "Charlie", "Diana"]
        )
        bibliography = self.manager.compile_bibliography()
        assert "Alice et al." in bibliography
    
    def test_compile_bibliography_with_date(self):
        """Test that bibliography includes publication date."""
        self.manager.add_source(
            title="News Article",
            url="https://example.com/news",
            source_type=SourceType.NEWS,
            date="2024-01-15"
        )
        bibliography = self.manager.compile_bibliography()
        assert "Published: 2024-01-15" in bibliography
    
    def test_compile_bibliography_source_type_indicators(self):
        """Test that bibliography includes source type indicators."""
        sources = [
            ("Academic Paper", SourceType.ACADEMIC, "[Academic]"),
            ("Reddit Post", SourceType.SOCIAL, "[Social]"),
            ("Stack Overflow Answer", SourceType.FORUM, "[Forum]"),
            ("News Article", SourceType.NEWS, "[News]"),
            ("Python Docs", SourceType.DOCUMENTATION, "[Documentation]"),
            ("Web Page", SourceType.WEB, "[Web]"),
        ]
        
        for title, source_type, indicator in sources:
            manager = CitationManager()
            manager.add_source(
                title=title,
                url=f"https://example.com/{title.lower().replace(' ', '-')}",
                source_type=source_type
            )
            bibliography = manager.compile_bibliography()
            assert indicator in bibliography, f"Expected {indicator} for {title}"
    
    def test_clear(self):
        """Test that clear resets the manager."""
        self.manager.add_source(
            title="Test Article",
            url="https://example.com/article",
            source_type=SourceType.WEB
        )
        assert self.manager.source_count == 1
        
        self.manager.clear()
        assert self.manager.source_count == 0
    
    def test_get_all_sources(self):
        """Test getting all sources in order."""
        cid1 = self.manager.add_source(
            title="First",
            url="https://example.com/1",
            source_type=SourceType.WEB
        )
        cid2 = self.manager.add_source(
            title="Second",
            url="https://example.com/2",
            source_type=SourceType.WEB
        )
        
        sources = self.manager.get_all_sources()
        assert len(sources) == 2
        assert sources[0].title == "First"
        assert sources[1].title == "Second"


class TestGlobalFunctions:
    """Tests for the global citation manager functions."""
    
    def setup_method(self):
        """Reset the global citation manager before each test."""
        reset_citation_manager()
    
    def test_get_citation_manager_returns_singleton(self):
        """Test that get_citation_manager returns the same instance."""
        manager1 = get_citation_manager()
        manager2 = get_citation_manager()
        assert manager1 is manager2
    
    def test_register_source(self):
        """Test registering a source via the global function."""
        cid = register_source(
            title="Test Article",
            url="https://example.com/article",
            source_type=SourceType.WEB
        )
        assert cid == 1
        
        manager = get_citation_manager()
        source = manager.get_source(cid)
        assert source.title == "Test Article"
    
    def test_get_citation(self):
        """Test getting a citation reference via the global function."""
        cid = register_source(
            title="Test Article",
            url="https://example.com/article",
            source_type=SourceType.WEB
        )
        citation = get_citation(cid)
        assert citation == "[^1]"
    
    def test_compile_bibliography_global(self):
        """Test compiling bibliography via the global function."""
        register_source(
            title="Test Article",
            url="https://example.com/article",
            source_type=SourceType.WEB
        )
        bibliography = compile_bibliography()
        assert "## Bibliography" in bibliography
        assert "Test Article" in bibliography
    
    def test_reset_citation_manager(self):
        """Test that reset creates a new instance."""
        manager1 = get_citation_manager()
        manager1.add_source(
            title="Test",
            url="https://example.com",
            source_type=SourceType.WEB
        )
        
        reset_citation_manager()
        
        manager2 = get_citation_manager()
        assert manager1 is not manager2
        assert manager2.source_count == 0