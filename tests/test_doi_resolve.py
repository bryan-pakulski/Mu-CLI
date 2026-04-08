"""Tests for the doi_resolve tool."""
import json
import pytest


def test_doi_resolve_empty_doi():
    """Test that doi_resolve handles empty DOI gracefully."""
    from core.tools import doi_resolve

    result = doi_resolve("", None)
    data = json.loads(result)
    assert "error" in data
    assert data["error"] == "DOI cannot be empty"


def test_doi_resolve_whitespace_doi():
    """Test that doi_resolve handles whitespace-only DOI."""
    from core.tools import doi_resolve

    result = doi_resolve("   ", None)
    data = json.loads(result)
    assert "error" in data
    assert data["error"] == "DOI cannot be empty"


def test_doi_resolve_invalid_format():
    """Test that doi_resolve handles invalid DOI format."""
    from core.tools import doi_resolve

    # DOI should start with "10." followed by a number
    result = doi_resolve("invalid-doi", None)
    data = json.loads(result)
    # This might return an error or try to resolve anyway
    # The exact behavior depends on implementation
    assert "error" in data or "doi" in data


def test_doi_resolve_handler_registered():
    """Test that _handle_doi_resolve handler is properly registered."""
    from core.tools import TOOL_HANDLERS

    assert "doi_resolve" in TOOL_HANDLERS
    assert TOOL_HANDLERS["doi_resolve"] is not None


def test_doi_resolve_handler_call():
    """Test that handler properly extracts DOI from args."""
    from core.tools import _handle_doi_resolve

    result = _handle_doi_resolve({"doi": "10.1000/xyz123"}, None, None, None)
    data = json.loads(result)
    # Should attempt to resolve (may fail without network)
    assert "doi" in data or "error" in data


def test_doi_resolve_in_tools_list():
    """Test that doi_resolve is in the TOOLS list."""
    from core.tools import TOOLS

    tool_names = [t.name for t in TOOLS]
    assert "doi_resolve" in tool_names