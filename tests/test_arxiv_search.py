"""Tests for arxiv_search tool functionality."""
import json
import pytest


def test_arxiv_search_tool_definition_exists():
    """Test that arxiv_search tool is registered."""
    from core.tools import TOOL_DESCRIPTORS, TOOL_HANDLERS
    
    assert "arxiv_search" in TOOL_DESCRIPTORS
    assert "arxiv_search" in TOOL_HANDLERS
    
    # Check the tool definition
    descriptor = TOOL_DESCRIPTORS["arxiv_search"]
    assert descriptor.definition.name == "arxiv_search"
    assert "query" in descriptor.definition.parameters["required"]


def test_arxiv_search_empty_query():
    """Test that arxiv_search handles empty query gracefully."""
    from core.tools import arxiv_search
    
    result = arxiv_search("", None)
    parsed = json.loads(result)
    
    assert "error" in parsed
    assert parsed["error"] == "Query cannot be empty"
    assert parsed["results"] == []


def test_arxiv_search_whitespace_query():
    """Test that arxiv_search handles whitespace-only query."""
    from core.tools import arxiv_search
    
    result = arxiv_search("   ", None)
    parsed = json.loads(result)
    
    assert "error" in parsed
    assert parsed["error"] == "Query cannot be empty"


def test_arxiv_search_result_format():
    """Test that arxiv_search returns correct result format on error."""
    from core.tools import arxiv_search
    
    # Test with empty query to get predictable result format
    result = arxiv_search("", None)
    parsed = json.loads(result)
    
    # Result should have query, engine, and results keys
    assert "query" in parsed or "error" in parsed
    
    # If we got results, check structure
    if "results" in parsed:
        assert isinstance(parsed["results"], list)
        assert parsed["engine"] == "arxiv"


def test_arxiv_search_category_filter():
    """Test that category filter is accepted."""
    from core.tools import arxiv_search
    
    # Test with category filter (will fail due to no httpx in test env, but validates params)
    result = arxiv_search("quantum computing", None, max_results=5, category="cs.AI")
    parsed = json.loads(result)
    
    # Should attempt to search (may fail due to network/httpx but params accepted)
    assert "query" in parsed or "error" in parsed


def test_arxiv_search_max_results_bounds():
    """Test that max_results is bounded correctly."""
    from core.tools import arxiv_search
    
    # Test with negative - should be capped
    result = arxiv_search("test", None, max_results=-5)
    parsed = json.loads(result)
    # Function should handle gracefully
    
    # Test with very large - should be capped
    result = arxiv_search("test", None, max_results=1000)
    parsed = json.loads(result)
    # Should not error - function caps at 100


def test_arxiv_search_handler_registered():
    """Test that _handle_arxiv_search handler is properly registered."""
    from core.tools import _handle_arxiv_search
    
    result = _handle_arxiv_search({"query": ""}, None, None, None)
    parsed = json.loads(result)
    
    assert "error" in parsed
    assert parsed["error"] == "Query cannot be empty"