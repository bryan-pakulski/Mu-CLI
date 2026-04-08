"""Tests for reddit_search tool."""

import pytest
from unittest.mock import patch, MagicMock
import json


def test_reddit_search_handler_exists():
    """Test that reddit_search handler is registered."""
    from core.tools import TOOL_HANDLERS
    assert "reddit_search" in TOOL_HANDLERS


def test_reddit_search_function_exists():
    """Test that reddit_search function exists."""
    from core.tools import reddit_search
    assert callable(reddit_search)


@patch('httpx.Client')
def test_reddit_search_basic(mock_client_class):
    """Test basic reddit_search functionality."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": {
            "children": [
                {
                    "data": {
                        "title": "Test Post",
                        "selftext": "Test content",
                        "author": "test_user",
                        "subreddit": "test",
                        "score": 100,
                        "num_comments": 50,
                        "permalink": "/r/test/comments/123",
                        "created_utc": 1234567890.0,
                        "upvote_ratio": 0.95,
                        "link_flair_text": None,
                        "is_video": False
                    }
                }
            ]
        }
    }
    
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = mock_response
    mock_client_class.return_value = mock_client
    
    from core.tools import reddit_search
    result = reddit_search("test query")
    
    data = json.loads(result)
    
    assert "results" in data
    assert len(data["results"]) == 1
    assert data["results"][0]["title"] == "Test Post"
    assert data["results"][0]["author"] == "test_user"
    assert data["results"][0]["subreddit"] == "test"


@patch('httpx.Client')
def test_reddit_search_with_subreddit(mock_client_class):
    """Test reddit_search with subreddit filter."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"data": {"children": []}}
    
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = mock_response
    mock_client_class.return_value = mock_client
    
    from core.tools import reddit_search
    result = reddit_search("test query", subreddit="python")
    
    # Verify subreddit was included in URL
    call_args = mock_client.get.call_args
    assert "r/python" in call_args[0][0] or "python" in str(call_args)


@patch('httpx.Client')
def test_reddit_search_with_limit(mock_client_class):
    """Test reddit_search with result limit."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"data": {"children": []}}
    
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = mock_response
    mock_client_class.return_value = mock_client
    
    from core.tools import reddit_search
    result = reddit_search("test query", limit=5)
    
    # Verify limit was passed as parameter
    call_args = mock_client.get.call_args
    assert "limit=5" in call_args[0][0]


@patch('httpx.Client')
def test_reddit_search_sort_options(mock_client_class):
    """Test reddit_search with different sort options."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"data": {"children": []}}
    
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = mock_response
    mock_client_class.return_value = mock_client
    
    from core.tools import reddit_search
    
    # Test 'new' sort
    result = reddit_search("test", sort="new")
    call_args = mock_client.get.call_args
    assert "sort=new" in call_args[0][0]
    
    # Test 'top' sort
    result = reddit_search("test", sort="top")
    call_args = mock_client.get.call_args
    assert "sort=top" in call_args[0][0]


@patch('httpx.Client')
def test_reddit_search_error_handling(mock_client_class):
    """Test reddit_search handles API errors gracefully."""
    from httpx import HTTPStatusError
    
    mock_response = MagicMock()
    mock_response.status_code = 429
    
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.side_effect = HTTPStatusError("Rate limit", request=MagicMock(), response=mock_response)
    mock_client_class.return_value = mock_client
    
    from core.tools import reddit_search
    result = reddit_search("test query")
    
    data = json.loads(result)
    
    assert "error" in data