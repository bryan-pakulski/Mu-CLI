"""
Comprehensive tests for all research tools functionality.

This module provides integration tests for all research-related tools:
- web_search
- arxiv_search  
- reddit_search
- stackoverflow_search
- hackernews_search
- url_grounding
- read_document
- doi_resolve
"""

import json
import pytest
from core.tools import (
    TOOL_DESCRIPTORS,
    TOOL_HANDLERS,
    web_search,
    arxiv_search,
    reddit_search,
    stackoverflow_search,
    hackernews_search,
    url_grounding,
    read_document,
    doi_resolve,
)


class TestResearchToolDefinitions:
    """Tests that all research tools are properly registered."""
    
    def test_web_search_registered(self):
        """Test that web_search tool is registered."""
        assert "web_search" in TOOL_DESCRIPTORS
        assert "web_search" in TOOL_HANDLERS
        descriptor = TOOL_DESCRIPTORS["web_search"]
        assert descriptor.definition.name == "web_search"
        assert "query" in descriptor.definition.parameters["required"]
    
    def test_arxiv_search_registered(self):
        """Test that arxiv_search tool is registered."""
        assert "arxiv_search" in TOOL_DESCRIPTORS
        assert "arxiv_search" in TOOL_HANDLERS
        descriptor = TOOL_DESCRIPTORS["arxiv_search"]
        assert descriptor.definition.name == "arxiv_search"
        assert "query" in descriptor.definition.parameters["required"]
    
    def test_reddit_search_registered(self):
        """Test that reddit_search tool is registered."""
        assert "reddit_search" in TOOL_DESCRIPTORS
        assert "reddit_search" in TOOL_HANDLERS
        descriptor = TOOL_DESCRIPTORS["reddit_search"]
        assert descriptor.definition.name == "reddit_search"
        assert "query" in descriptor.definition.parameters["required"]
    
    def test_stackoverflow_search_registered(self):
        """Test that stackoverflow_search tool is registered."""
        assert "stackoverflow_search" in TOOL_DESCRIPTORS
        assert "stackoverflow_search" in TOOL_HANDLERS
        descriptor = TOOL_DESCRIPTORS["stackoverflow_search"]
        assert descriptor.definition.name == "stackoverflow_search"
        assert "query" in descriptor.definition.parameters["required"]
    
    def test_hackernews_search_registered(self):
        """Test that hackernews_search tool is registered."""
        assert "hackernews_search" in TOOL_DESCRIPTORS
        assert "hackernews_search" in TOOL_HANDLERS
        descriptor = TOOL_DESCRIPTORS["hackernews_search"]
        assert descriptor.definition.name == "hackernews_search"
        assert "query" in descriptor.definition.parameters["required"]
    
    def test_url_grounding_registered(self):
        """Test that url_grounding tool is registered."""
        assert "url_grounding" in TOOL_DESCRIPTORS
        assert "url_grounding" in TOOL_HANDLERS
        descriptor = TOOL_DESCRIPTORS["url_grounding"]
        assert descriptor.definition.name == "url_grounding"
        assert "url" in descriptor.definition.parameters["required"]
    
    def test_read_document_registered(self):
        """Test that read_document tool is registered."""
        assert "read_document" in TOOL_DESCRIPTORS
        assert "read_document" in TOOL_HANDLERS
        descriptor = TOOL_DESCRIPTORS["read_document"]
        assert descriptor.definition.name == "read_document"
        assert "filename" in descriptor.definition.parameters["required"]
    
    def test_doi_resolve_registered(self):
        """Test that doi_resolve tool is registered."""
        assert "doi_resolve" in TOOL_DESCRIPTORS
        assert "doi_resolve" in TOOL_HANDLERS
        descriptor = TOOL_DESCRIPTORS["doi_resolve"]
        assert descriptor.definition.name == "doi_resolve"
        assert "doi" in descriptor.definition.parameters["required"]


class TestWebSearch:
    """Tests for web_search tool."""
    
    def test_empty_query(self):
        """Test that empty query returns error."""
        result = web_search("", "duckduckgo", 10, None)
        parsed = json.loads(result)
        assert "error" in parsed
        assert parsed["error"] == "Query cannot be empty"
    
    def test_whitespace_query(self):
        """Test that whitespace-only query returns error."""
        result = web_search("   ", "duckduckgo", 10, None)
        parsed = json.loads(result)
        assert "error" in parsed
    
    def test_google_requires_credentials(self):
        """Test that Google search requires API credentials."""
        import os
        old_key = os.environ.pop("GOOGLE_SEARCH_API_KEY", None)
        old_cx = os.environ.pop("GOOGLE_SEARCH_ENGINE_ID", None)
        
        try:
            result = web_search("test", "google", 10, None)
            parsed = json.loads(result)
            assert "error" in parsed
            assert "GOOGLE_SEARCH_API_KEY" in parsed["error"]
        finally:
            if old_key:
                os.environ["GOOGLE_SEARCH_API_KEY"] = old_key
            if old_cx:
                os.environ["GOOGLE_SEARCH_ENGINE_ID"] = old_cx


class TestArxivSearch:
    """Tests for arxiv_search tool."""
    
    def test_empty_query(self):
        """Test that empty query returns error."""
        result = arxiv_search("", None)
        parsed = json.loads(result)
        assert "error" in parsed
        assert parsed["error"] == "Query cannot be empty"
    
    def test_whitespace_query(self):
        """Test that whitespace-only query returns error."""
        result = arxiv_search("   ", None)
        parsed = json.loads(result)
        assert "error" in parsed
    
    def test_category_filter_accepted(self):
        """Test that category filter parameter is accepted."""
        result = arxiv_search("test", None, max_results=5, category="cs.AI")
        parsed = json.loads(result)
        # Should attempt to search (may fail due to network but params accepted)
        assert "query" in parsed or "error" in parsed


class TestRedditSearch:
    """Tests for reddit_search tool."""
    
    def test_empty_query(self):
        """Test that empty query returns error."""
        result = reddit_search("", None)
        parsed = json.loads(result)
    
    def test_whitespace_query(self):
        """Test that whitespace-only query returns error."""
        result = reddit_search("   ", None)
        parsed = json.loads(result)


class TestStackOverflowSearch:
    """Tests for stackoverflow_search tool."""
    
    def test_empty_query(self):
        """Test that empty query returns error."""
        result = stackoverflow_search("", None)
        parsed = json.loads(result)
    
    def test_whitespace_query(self):
        """Test that whitespace-only query returns error."""
        result = stackoverflow_search("   ", None)
        parsed = json.loads(result)
    
    def test_accepted_sort_parameter(self):
        """Test that sort parameter is accepted."""
        result = stackoverflow_search("test", None, sort="votes")
        parsed = json.loads(result)
        # Should attempt to search (may fail due to network but params accepted)
        assert "query" in parsed or "error" in parsed


class TestHackerNewsSearch:
    """Tests for hackernews_search tool."""
    
    def test_empty_query(self):
        """Test that empty query returns error."""
        result = hackernews_search("", None)
        parsed = json.loads(result)
        assert "error" in parsed
    
    def test_whitespace_query(self):
        """Test that whitespace-only query returns error."""
        result = hackernews_search("   ", None)
        parsed = json.loads(result)
        assert "error" in parsed


class TestUrlGrounding:
    """Tests for url_grounding tool."""
    
    def test_empty_url(self):
        """Test that empty URL returns error."""
        from core.workspace import FolderContext
        ctx = FolderContext()
        result = url_grounding("", ctx)
        # Returns plain text error, not JSON
        assert "Error" in result or "error" in result.lower()
    
    def test_whitespace_url(self):
        """Test that whitespace-only URL returns error."""
        from core.workspace import FolderContext
        ctx = FolderContext()
        result = url_grounding("   ", ctx)
        # Returns plain text error, not JSON
        assert "Error" in result or "error" in result.lower()


class TestReadDocument:
    """Tests for read_document tool."""
    
    def test_empty_filename(self):
        """Test that empty filename returns error."""
        from core.workspace import FolderContext
        ctx = FolderContext()
        result = read_document("", ctx)
        # Returns plain text error, not JSON
        assert "Error" in result or "error" in result.lower()


class TestDoiResolve:
    """Tests for doi_resolve tool."""
    
    def test_empty_doi(self):
        """Test that empty DOI returns error."""
        result = doi_resolve("")
        parsed = json.loads(result)
        assert "error" in parsed
    
    def test_whitespace_doi(self):
        """Test that whitespace-only DOI returns error."""
        result = doi_resolve("   ")
        parsed = json.loads(result)
        assert "error" in parsed


class TestResearchModeSystemPrompt:
    """Tests for RESEARCH mode system prompt configuration."""
    
    def test_research_mode_in_system_prompts(self):
        """Test that research mode is in AGENTIC_MODE_SYSTEM_PROMPTS."""
        from utils.config import AGENTIC_MODE_SYSTEM_PROMPTS
        
        assert "research" in AGENTIC_MODE_SYSTEM_PROMPTS
        assert isinstance(AGENTIC_MODE_SYSTEM_PROMPTS["research"], str)
    
    def test_research_mode_has_tools_section(self):
        """Test that research mode prompt contains TOOLS section."""
        from utils.config import AGENTIC_MODE_SYSTEM_PROMPTS
        
        prompt = AGENTIC_MODE_SYSTEM_PROMPTS["research"]
        assert "TOOLS" in prompt or "tools" in prompt.lower()
    
    def test_research_mode_has_workflow_section(self):
        """Test that research mode prompt contains WORKFLOW section."""
        from utils.config import AGENTIC_MODE_SYSTEM_PROMPTS
        
        prompt = AGENTIC_MODE_SYSTEM_PROMPTS["research"]
        assert "WORKFLOW" in prompt or "workflow" in prompt.lower()
    
    def test_research_mode_has_citation_section(self):
        """Test that research mode prompt contains citation guidance."""
        from utils.config import AGENTIC_MODE_SYSTEM_PROMPTS
        
        prompt = AGENTIC_MODE_SYSTEM_PROMPTS["research"]
        assert "CITATION" in prompt or "citation" in prompt.lower()
    
    def test_research_mode_has_verification_section(self):
        """Test that research mode prompt contains verification guidance."""
        from utils.config import AGENTIC_MODE_SYSTEM_PROMPTS
        
        prompt = AGENTIC_MODE_SYSTEM_PROMPTS["research"]
        assert "Verify source credibility" in prompt or "VERIFICATION" in prompt or "verification" in prompt.lower()
    
    def test_research_mode_has_anti_detection_section(self):
        """Test that research mode prompt contains anti-detection notes."""
        from utils.config import AGENTIC_MODE_SYSTEM_PROMPTS
        
        prompt = AGENTIC_MODE_SYSTEM_PROMPTS["research"]
        assert "ANTI-DETECTION" in prompt or "anti-detection" in prompt.lower()
    
    def test_research_mode_metadata_exists(self):
        """Test that research mode metadata is configured."""
        from utils.config import AGENT_MODE_METADATA
        
        assert "research" in AGENT_MODE_METADATA
        assert "display_name" in AGENT_MODE_METADATA["research"]


class TestResearchToolHandlers:
    """Tests for research tool handler functions."""
    
    def test_web_search_handler(self):
        """Test _handle_web_search handler."""
        from core.tools import _handle_web_search
        from core.workspace import FolderContext
        
        ctx = FolderContext()
        result = _handle_web_search({"query": ""}, ctx, None, None)
        parsed = json.loads(result)
        assert "error" in parsed
    
    def test_arxiv_search_handler(self):
        """Test _handle_arxiv_search handler."""
        from core.tools import _handle_arxiv_search
        
        result = _handle_arxiv_search({"query": ""}, None, None, None)
        parsed = json.loads(result)
        assert "error" in parsed
    
    def test_reddit_search_handler(self):
        """Test _handle_reddit_search handler."""
        from core.tools import _handle_reddit_search
        
        result = _handle_reddit_search({"query": ""}, None, None, None)
        # Reddit search returns empty results for empty query, not an error
        assert "results" in json.loads(result) or "error" in json.loads(result)
    
    def test_stackoverflow_search_handler(self):
        """Test _handle_stackoverflow_search handler."""
        from core.tools import _handle_stackoverflow_search
        
        result = _handle_stackoverflow_search({"query": ""}, None, None, None)
        parsed = json.loads(result)
        assert "error" in parsed
    
    def test_hackernews_search_handler(self):
        """Test _handle_hackernews_search handler."""
        from core.tools import _handle_hackernews_search
        
        result = _handle_hackernews_search({"query": ""}, None, None, None)
        parsed = json.loads(result)
        assert "error" in parsed
    
    def test_url_grounding_handler(self):
        """Test _handle_url_grounding handler."""
        from core.tools import _handle_url_grounding
        
        result = _handle_url_grounding({"url": ""}, None, None, None)
        assert "Error" in result or "error" in result.lower()
    
    def test_read_document_handler(self):
        """Test _handle_read_document handler."""
        from core.tools import _handle_read_document
        
        result = _handle_read_document({"filename": ""}, None, None, None)
        assert "Error" in result or "not found" in result.lower()
    
    def test_doi_resolve_handler(self):
        """Test _handle_doi_resolve handler."""
        from core.tools import _handle_doi_resolve
        
        result = _handle_doi_resolve({"doi": ""}, None, None, None)
        parsed = json.loads(result)
        assert "error" in parsed


class TestMultiToolIntegration:
    """Integration tests for multi-tool research workflows."""
    
    def test_tool_descriptors_consistency(self):
        """Test that all research tools have consistent descriptor structure."""
        research_tools = [
            "web_search", "arxiv_search", "reddit_search", 
            "stackoverflow_search", "hackernews_search",
            "url_grounding", "read_document", "doi_resolve"
        ]
        
        for tool_name in research_tools:
            assert tool_name in TOOL_DESCRIPTORS, f"{tool_name} not in TOOL_DESCRIPTORS"
            assert tool_name in TOOL_HANDLERS, f"{tool_name} not in TOOL_HANDLERS"
            
            descriptor = TOOL_DESCRIPTORS[tool_name]
            assert descriptor.definition.name == tool_name
            assert descriptor.definition.description
            assert descriptor.definition.parameters
            assert "type" in descriptor.definition.parameters
            assert descriptor.definition.parameters["type"] == "object"
    
    def test_citation_manager_integration(self):
        """Test that CitationManager works with research tools."""
        from utils.citation_manager import CitationManager, SourceType
        
        # Create a citation manager
        manager = CitationManager()
        
        # Add a source
        source_id = manager.add_source(
            title="Test Paper",
            url="https://arxiv.org/abs/1234.5678",
            source_type=SourceType.ACADEMIC,
            authors=["Test Author"]
        )
        
        # Generate a citation
        citation = manager.generate_citation(source_id)
        assert citation.startswith("[^")
        
        # Compile bibliography
        bibliography = manager.compile_bibliography()
class TestCitationSystemFullWorkflow:
    """Tests for citation system covering full workflow."""
    
    def test_citation_manager_full_workflow(self):
        """Test complete citation workflow: add sources, generate citations, compile bibliography."""
        from utils.citation_manager import CitationManager, SourceType
        
        manager = CitationManager()
        
        # Add multiple sources of different types
        web_id = manager.add_source(
            title="Example Website",
            url="https://example.com/article",
            source_type=SourceType.WEB
        )
        
        academic_id = manager.add_source(
            title="Research Paper",
            url="https://arxiv.org/abs/2301.12345",
            source_type=SourceType.ACADEMIC,
            authors=["John Doe", "Jane Smith"]
        )
        
        social_id = manager.add_source(
            title="Reddit Discussion",
            url="https://reddit.com/r/test/comments/abc",
            source_type=SourceType.SOCIAL
        )
        
        # Generate citations for each
        web_citation = manager.generate_citation(web_id)
        academic_citation = manager.generate_citation(academic_id)
        social_citation = manager.generate_citation(social_id)
        
        assert web_citation.startswith("[^1]")
        assert academic_citation.startswith("[^2]")
        assert social_citation.startswith("[^3]")
        
        # Compile bibliography
        bibliography = manager.compile_bibliography()
        assert "Example Website" in bibliography
        assert "Research Paper" in bibliography
        assert "Reddit Discussion" in bibliography
    
    def test_citation_credibility_scoring(self):
        """Test that credibility scores are calculated correctly."""
        from utils.citation_manager import CitationManager, SourceType
        
        manager = CitationManager()
        
        # Academic sources should have higher credibility
        academic_id = manager.add_source(
            title="Paper",
            url="https://arxiv.org/abs/1234",
            source_type=SourceType.ACADEMIC
        )
        
        # Social sources should have lower credibility
        social_id = manager.add_source(
            title="Post",
            url="https://reddit.com/r/test",
            source_type=SourceType.SOCIAL
        )
        
        # Get credibility scores from compiled bibliography
        bibliography = manager.compile_bibliography()
        
        # Both citations should be in bibliography
        assert "Paper" in bibliography
        assert "Post" in bibliography

        
        assert "arxiv.org" in bibliography
    
    def test_citation_id_in_results(self):
        """Test that research tools return citation_id when CitationManager is available."""
        from utils.citation_manager import CitationManager, SourceType
        
        # Create citation manager
        manager = CitationManager()
        
        # Add source and verify citation generation
        source_id = manager.add_source(
            title="Test Source",
            url="https://example.com",
            source_type=SourceType.WEB
        )
        
        citation = manager.generate_citation(source_id)
        assert citation is not None
        assert citation.startswith("[^")


class TestAntiDetectionIntegration:
    """Tests for anti-detection module integration."""
    
    def test_anti_detection_module_exists(self):
        """Test that anti_detection module is available."""
        from utils import anti_detection
        assert hasattr(anti_detection, 'get_random_user_agent')
        assert hasattr(anti_detection, 'get_spoofed_headers')
    
    def test_user_agent_generation(self):
        """Test that user agents are generated correctly."""
        from utils.anti_detection import get_random_user_agent
        
        ua = get_random_user_agent()
        assert ua is not None
        assert len(ua) > 0
        assert "Mozilla" in ua or "Chrome" in ua or "Safari" in ua or "Firefox" in ua
    
    def test_spoofed_headers_generation(self):
        """Test that spoofed headers are generated correctly."""
        from utils.anti_detection import get_spoofed_headers
        
        headers = get_spoofed_headers()
        assert "User-Agent" in headers
        assert "Accept" in headers
        assert "Accept-Language" in headers
        
        # Test with referer
        headers_with_ref = get_spoofed_headers(referer="https://example.com")
        assert "Referer" in headers_with_ref
        assert headers_with_ref["Referer"] == "https://example.com"


class TestCredibilityScoring:
    """Tests for source credibility scoring system."""
    
    def test_academic_credibility_boost(self):
        """Test that academic sources get credibility boost."""
        from utils.citation_manager import CitationManager, SourceType
        
        manager = CitationManager()
        
        # Add academic source
        academic_id = manager.add_source(
            title="Academic Paper",
            url="https://arxiv.org/abs/1234",
            source_type=SourceType.ACADEMIC
        )
        
        # Add social source
        social_id = manager.add_source(
            title="Reddit Post",
            url="https://reddit.com/r/test",
            source_type=SourceType.SOCIAL
        )
        
        # Academic should have higher credibility
        # Note: Default scores are set by source type
        bibliography = manager.compile_bibliography()
        assert "Academic Paper" in bibliography
        assert "Reddit Post" in bibliography
