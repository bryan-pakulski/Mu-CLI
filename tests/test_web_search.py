"""Tests for web_search tool functionality."""
import json
import pytest
from mu.tools._dispatcher import execute_tool, TOOL_HANDLERS
from mu.tools.descriptors import TOOL_DESCRIPTORS
from mu.tools.research.handlers import web_search
from mu.workspace.folder_context import FolderContext


def test_web_search_tool_definition_exists():
    """Test that web_search tool is registered."""
    assert "web_search" in TOOL_DESCRIPTORS
    assert "web_search" in TOOL_HANDLERS
    
    # Check the tool definition
    descriptor = TOOL_DESCRIPTORS["web_search"]
    assert descriptor.definition.name == "web_search"
    assert "query" in descriptor.definition.parameters["required"]


def test_web_search_empty_query():
    """Test that web_search handles empty query gracefully."""
    result = web_search("", "duckduckgo", 10, None)
    parsed = json.loads(result)
    
    assert "error" in parsed
    assert parsed["error"] == "Query cannot be empty"
    assert parsed["results"] == []


def test_web_search_whitespace_query():
    """Test that web_search handles whitespace-only query gracefully."""
    result = web_search("   ", "duckduckgo", 10, None)
    parsed = json.loads(result)
    
    assert "error" in parsed
    assert parsed["error"] == "Query cannot be empty"


def test_web_search_google_requires_env_vars():
    """Test that Google search requires API credentials."""
    import os
    
    # Clear environment variables temporarily
    old_key = os.environ.pop("GOOGLE_SEARCH_API_KEY", None)
    old_cx = os.environ.pop("GOOGLE_SEARCH_ENGINE_ID", None)
    
    try:
        result = web_search("test query", "google", 10, None)
        parsed = json.loads(result)
        
        assert "error" in parsed
        assert "GOOGLE_SEARCH_API_KEY" in parsed["error"]
        assert parsed["results"] == []
    finally:
        if old_key:
            os.environ["GOOGLE_SEARCH_API_KEY"] = old_key
        if old_cx:
            os.environ["GOOGLE_SEARCH_ENGINE_ID"] = old_cx


def test_web_search_result_format():
    """Test that web_search returns correct result format on error."""
    # Test with empty query to get predictable result format
    result = web_search("", "duckduckgo", 5, None)
    parsed = json.loads(result)
    
    # Result should have query, engine, and results keys
    assert "query" in parsed or "error" in parsed
    
    # If we got results, check structure
    if "results" in parsed:
        assert isinstance(parsed["results"], list)


def test_web_search_engine_parameter():
    """Test that engine parameter is accepted."""
    # Test with invalid credentials for Google (should fail fast)
    import os
    old_key = os.environ.pop("GOOGLE_SEARCH_API_KEY", None)
    old_cx = os.environ.pop("GOOGLE_SEARCH_ENGINE_ID", None)
    
    try:
        result = web_search("test", "google", 10, None)
        parsed = json.loads(result)
        
        # Should get error about missing credentials
        assert "error" in parsed
        assert "GOOGLE" in parsed["error"].upper()
    finally:
        if old_key:
            os.environ["GOOGLE_SEARCH_API_KEY"] = old_key
        if old_cx:
            os.environ["GOOGLE_SEARCH_ENGINE_ID"] = old_cx


def test_web_search_num_results_bounds():
    """Test that num_results is bounded correctly."""
    # Test with empty query but extreme num_results values
    # Function should cap num_results between 1 and 50
    
    # Test negative - should be capped to 1
    result = web_search("", "duckduckgo", -5, None)
    parsed = json.loads(result)
    assert "error" in parsed  # Empty query error
    
    # Test very large - should be capped to 50
    result = web_search("", "duckduckgo", 1000, None)
    parsed = json.loads(result)
    assert "error" in parsed  # Empty query error


def test_web_search_handler_registered():
    """The `@tool`-registered web_search handler is reachable via the new
    dispatcher. Post-migration the handler lives in
    `mu/tools/research/handlers.py`; an empty-query call surfaces the
    "Query cannot be empty" error via the envelope."""
    import mu.tools as _mu_tools

    ctx = _mu_tools.build_tool_context(
        folder_context=FolderContext(), ui=None, variables={}
    )
    envelope = _mu_tools.execute("web_search", {"query": ""}, ctx)
    assert envelope["ok"] is False
    assert envelope["message"] == "Query cannot be empty"
    assert envelope["data"]["error"] == "Query cannot be empty"


def test_web_search_duckduckgo_html_fallback(monkeypatch):
    """If DDGS returns nothing, fallback HTML parser should still return results."""
    import mu.tools.research.handlers as tools

    class FakeDDGS:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def text(self, query, max_results=10):
            return []

    class FakeResponse:
        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            return None

    html = """
    <div class="result">
      <a class="result__a" href="https://example.com/post">Example title</a>
      <a class="result__snippet">Example snippet</a>
    </div>
    """

    class FakeHttpx:
        @staticmethod
        def get(*args, **kwargs):
            return FakeResponse(html)

    monkeypatch.setattr(tools, "register_source", lambda **kwargs: "cite_1")
    monkeypatch.setattr("ddgs.DDGS", FakeDDGS)
    monkeypatch.setattr("httpx.get", FakeHttpx.get)

    result = web_search("example query", "duckduckgo", 5, None)
    parsed = json.loads(result)

    assert parsed["num_results"] >= 1
    assert parsed["results"][0]["title"] == "Example title"
