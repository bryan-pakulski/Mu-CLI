"""Tests for research mode system prompt and configuration."""

import pytest
from utils.config import AGENTIC_MODES, AGENT_MODE_METADATA


class TestResearchModeSystemPrompt:
    """Tests for research mode system prompt configuration."""

    def test_research_mode_exists_in_system_prompts(self):
        """Test that research mode is defined in AGENTIC_MODES."""
        assert "research" in AGENTIC_MODES, "Research mode should exist in AGENTIC_MODES"

    def test_research_mode_prompt_not_empty(self):
        """Test that research mode system prompt is not empty."""
        research_prompt = AGENTIC_MODES.get("research", "")
        assert research_prompt, "Research mode system prompt should not be empty"
        assert len(research_prompt) > 100, "Research mode system prompt should be substantial"

    def test_research_mode_prompt_contains_workflow(self):
        """Test that research mode prompt contains WORKFLOW section."""
        research_prompt = AGENTIC_MODES.get("research", "")
        assert "WORKFLOW" in research_prompt, "Research mode should have WORKFLOW section"

    def test_research_mode_prompt_contains_tool_descriptions(self):
        """Test that research mode prompt describes research tools."""
        research_prompt = AGENTIC_MODES.get("research", "")
        
        # Check for tool descriptions
        tool_keywords = ["web_search", "arxiv_search", "url_grounding", "read_document"]
        found_tools = sum(1 for tool in tool_keywords if tool in research_prompt)
        assert found_tools >= 2, f"Research mode should describe research tools (found {found_tools} of {len(tool_keywords)})"

    def test_research_mode_prompt_contains_citation_requirements(self):
        """Test that research mode prompt includes citation requirements."""
        research_prompt = AGENTIC_MODES.get("research", "")
        
        # Check for citation-related content
        citation_keywords = ["citation", "source", "reference", "bibliography"]
        found_citation = sum(1 for kw in citation_keywords if kw.lower() in research_prompt.lower())
        assert found_citation >= 2, f"Research mode should mention citation requirements (found {found_citation} of {len(citation_keywords)})"

    def test_research_mode_prompt_contains_verification_guidance(self):
        """Test that research mode prompt includes source verification guidance."""
        research_prompt = AGENTIC_MODES.get("research", "")
        
        # Check for verification-related content
        verification_keywords = ["verify", "credibility", "cross-reference", "reliable"]
        found_verification = sum(1 for kw in verification_keywords if kw.lower() in research_prompt.lower())
        assert found_verification >= 1, f"Research mode should mention source verification (found {found_verification})"

    def test_research_mode_prompt_anti_detection_notes(self):
        """Test that research mode prompt includes anti-detection guidance."""
        research_prompt = AGENTIC_MODES.get("research", "")
        
        # Check for anti-detection content
        detection_keywords = ["rate limit", "javascript", "paywall", "authentication", "detection", "crawl"]
        found_detection = sum(1 for kw in detection_keywords if kw.lower() in research_prompt.lower())
        assert found_detection >= 1, f"Research mode should mention anti-detection notes (found {found_detection})"


class TestResearchModeMetadata:
    """Tests for research mode metadata configuration."""

    def test_research_mode_in_agent_mode_metadata(self):
        """Test that research mode exists in AGENT_MODE_METADATA."""
        assert "research" in AGENT_MODE_METADATA, "Research mode should exist in AGENT_MODE_METADATA"

    def test_research_mode_has_description(self):
        """Test that research mode metadata includes description."""
        research_metadata = AGENT_MODE_METADATA.get("research", {})
        assert "description" in research_metadata, "Research mode metadata should have description"
        assert research_metadata["description"], "Research mode description should not be empty"

    def test_research_mode_has_display_name(self):
        """Test that research mode metadata includes display_name."""
        research_metadata = AGENT_MODE_METADATA.get("research", {})
        assert "display_name" in research_metadata, "Research mode metadata should have display_name"
        assert research_metadata["display_name"], "Research mode display_name should not be empty"

    def test_research_mode_display_name(self):
        """Test research mode display name is correct."""
        research_metadata = AGENT_MODE_METADATA.get("research", {})
        assert research_metadata.get("display_name") == "Research Mode", "Research mode display name should be 'Research Mode'"


class TestResearchToolsAvailable:
    """Tests for research tools availability."""

    def test_web_search_tool_exists(self):
        """Test that web_search tool is defined."""
        from core.tools import TOOLS, TOOL_HANDLERS
        
        tool_names = [t.name for t in TOOLS]
        assert "web_search" in tool_names, "web_search tool should be defined"
        assert "web_search" in TOOL_HANDLERS, "web_search handler should be defined"

    def test_arxiv_search_tool_exists(self):
        """Test that arxiv_search tool is defined."""
        from core.tools import TOOLS, TOOL_HANDLERS
        
        tool_names = [t.name for t in TOOLS]
        assert "arxiv_search" in tool_names, "arxiv_search tool should be defined"
        assert "arxiv_search" in TOOL_HANDLERS, "arxiv_search handler should be defined"

    def test_reddit_search_tool_exists(self):
        """Test that reddit_search tool is defined."""
        from core.tools import TOOLS, TOOL_HANDLERS
        
        tool_names = [t.name for t in TOOLS]
        assert "reddit_search" in tool_names, "reddit_search tool should be defined"
        assert "reddit_search" in TOOL_HANDLERS, "reddit_search handler should be defined"

    def test_stackoverflow_search_tool_exists(self):
        """Test that stackoverflow_search tool is defined."""
        from core.tools import TOOLS, TOOL_HANDLERS
        
        tool_names = [t.name for t in TOOLS]
        assert "stackoverflow_search" in tool_names, "stackoverflow_search tool should be defined"
        assert "stackoverflow_search" in TOOL_HANDLERS, "stackoverflow_search handler should be defined"

    def test_hackernews_search_tool_exists(self):
        """Test that hackernews_search tool is defined."""
        from core.tools import TOOLS, TOOL_HANDLERS
        
        tool_names = [t.name for t in TOOLS]
        assert "hackernews_search" in tool_names, "hackernews_search tool should be defined"
        assert "hackernews_search" in TOOL_HANDLERS, "hackernews_search handler should be defined"

    def test_url_grounding_tool_exists(self):
        """Test that url_grounding tool is defined."""
        from core.tools import TOOLS, TOOL_HANDLERS
        
        tool_names = [t.name for t in TOOLS]
        assert "url_grounding" in tool_names, "url_grounding tool should be defined"
        assert "url_grounding" in TOOL_HANDLERS, "url_grounding handler should be defined"

    def test_read_document_tool_exists(self):
        """Test that read_document tool is defined."""
        from core.tools import TOOLS, TOOL_HANDLERS
        
        tool_names = [t.name for t in TOOLS]
        assert "read_document" in tool_names, "read_document tool should be defined"
        assert "read_document" in TOOL_HANDLERS, "read_document handler should be defined"

    def test_doi_resolve_tool_exists(self):
        """Test that doi_resolve tool is defined."""
        from core.tools import TOOLS, TOOL_HANDLERS
        
        tool_names = [t.name for t in TOOLS]
        assert "doi_resolve" in tool_names, "doi_resolve tool should be defined"
        assert "doi_resolve" in TOOL_HANDLERS, "doi_resolve handler should be defined"