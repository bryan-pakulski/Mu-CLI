"""Tests for the stackoverflow_search tool."""

import json
import pytest
from unittest.mock import patch, MagicMock


def test_stackoverflow_search_basic():
    """Test basic Stack Overflow search."""
    from core.tools import TOOL_HANDLERS
    
    handler = TOOL_HANDLERS.get("stackoverflow_search")
    assert handler is not None, "stackoverflow_search handler should be registered"
    
    # The handler wraps the actual function, so we just check it exists
    assert callable(handler), "stackoverflow_search handler should be callable"


@patch("httpx.Client")
def test_stackoverflow_search_with_query(mock_client):
    """Test Stack Overflow search with a query."""
    from core.tools import stackoverflow_search
    
    # Mock the response
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "items": [
            {
                "title": "How to center a div in CSS?",
                "question_id": 12345,
                "link": "https://stackoverflow.com/questions/12345",
                "score": 100,
                "answer_count": 5,
                "is_answered": True,
                "view_count": 10000,
                "tags": ["css", "html"],
                "body": "I want to center a div...",
                "creation_date": 1609459200,
                "last_activity_date": 1609545600,
                "owner": {"display_name": "user1", "reputation": 5000}
            }
        ],
        "has_more": False
    }
    mock_response.raise_for_status = MagicMock()
    
    mock_client_instance = MagicMock()
    mock_client_instance.get.return_value = mock_response
    mock_client.return_value.__enter__.return_value = mock_client_instance
    
    result = stackoverflow_search("center div css", folder_context=None)
    data = json.loads(result)
    
    assert data["query"] == "center div css"
    assert data["count"] == 1
    assert len(data["results"]) == 1
    assert data["results"][0]["title"] == "How to center a div in CSS?"
    assert data["results"][0]["score"] == 100
    assert data["results"][0]["answer_count"] == 5
    assert "css" in data["results"][0]["tags"]


@patch("httpx.Client")
def test_stackoverflow_search_with_tags(mock_client):
    """Test Stack Overflow search with tag filtering."""
    from core.tools import stackoverflow_search
    
    # Mock the response
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "items": [
            {
                "title": "Python async await explained",
                "question_id": 67890,
                "link": "https://stackoverflow.com/questions/67890",
                "score": 200,
                "answer_count": 10,
                "is_answered": True,
                "view_count": 50000,
                "tags": ["python", "async-await"],
                "body": "How does async work?",
                "creation_date": 1609459200,
                "last_activity_date": 1609545600,
                "owner": {"display_name": "user2", "reputation": 10000}
            }
        ],
        "has_more": False
    }
    mock_response.raise_for_status = MagicMock()
    
    mock_client_instance = MagicMock()
    mock_client_instance.get.return_value = mock_response
    mock_client.return_value.__enter__.return_value = mock_client_instance
    
    result = stackoverflow_search("async await", tags=["python", "async-await"], folder_context=None)
    data = json.loads(result)
    
    assert data["query"] == "async await"
    assert data["tags"] == ["python", "async-await"]
    assert data["count"] == 1
    
    # Verify the API was called with the correct tag parameter
    call_args = mock_client_instance.get.call_args
    assert "tagged" in call_args.kwargs["params"]
    assert call_args.kwargs["params"]["tagged"] == "python;async-await"


@patch("httpx.Client")
def test_stackoverflow_search_sort_options(mock_client):
    """Test Stack Overflow search with different sort options."""
    from core.tools import stackoverflow_search
    
    # Mock the response
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "items": [],
        "has_more": False
    }
    mock_response.raise_for_status = MagicMock()
    
    mock_client_instance = MagicMock()
    mock_client_instance.get.return_value = mock_response
    mock_client.return_value.__enter__.return_value = mock_client_instance
    
    # Test with 'votes' sort
    result = stackoverflow_search("test query", sort="votes", folder_context=None)
    data = json.loads(result)
    
    assert data["sort"] == "votes"
    
    # Verify the API was called with the correct sort parameter
    call_args = mock_client_instance.get.call_args
    assert call_args.kwargs["params"]["sort"] == "votes"


@patch("httpx.Client")
def test_stackoverflow_search_limit(mock_client):
    """Test Stack Overflow search with custom limit."""
    from core.tools import stackoverflow_search
    
    # Mock the response
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "items": [],
        "has_more": False
    }
    mock_response.raise_for_status = MagicMock()
    
    mock_client_instance = MagicMock()
    mock_client_instance.get.return_value = mock_response
    mock_client.return_value.__enter__.return_value = mock_client_instance
    
    result = stackoverflow_search("test query", limit=25, folder_context=None)
    data = json.loads(result)
    
    # Verify the API was called with the correct pagesize
    call_args = mock_client_instance.get.call_args
    assert call_args.kwargs["params"]["pagesize"] == 25


@patch("httpx.Client")
def test_stackoverflow_search_empty_results(mock_client):
    """Test Stack Overflow search with no results."""
    from core.tools import stackoverflow_search
    
    # Mock the response
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "items": [],
        "has_more": False
    }
    mock_response.raise_for_status = MagicMock()
    
    mock_client_instance = MagicMock()
    mock_client_instance.get.return_value = mock_response
    mock_client.return_value.__enter__.return_value = mock_client_instance
    
    result = stackoverflow_search("nonexistent query xyz123", folder_context=None)
    data = json.loads(result)
    
    assert data["count"] == 0
    assert data["results"] == []


def test_stackoverflow_search_registered():
    """Test that stackoverflow_search is registered in TOOLS list."""
    from core.tools import TOOLS
    
    tool_names = [t.name for t in TOOLS]
    assert "stackoverflow_search" in tool_names, "stackoverflow_search should be in TOOLS list"


def test_stackoverflow_search_in_collated_tool_names():
    """Test that stackoverflow_search is in _COLLATED_TOOL_NAMES."""
    from core.tools import _COLLATED_TOOL_NAMES
    
    assert "stackoverflow_search" in _COLLATED_TOOL_NAMES, "stackoverflow_search should be in _COLLATED_TOOL_NAMES"