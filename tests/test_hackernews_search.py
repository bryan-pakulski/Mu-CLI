"""Unit tests for hackernews_search tool."""
import json
import pytest
from unittest.mock import MagicMock, patch


class TestHackerNewsSearch:
    """Tests for hackernews_search tool functionality."""

    @patch('httpx.Client')
    def test_hackernews_search_basic(self, mock_client_class):
        """Test basic Hacker News search."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "hits": [
                {
                    "objectID": "12345",
                    "title": "Test Story",
                    "url": "https://example.com/test",
                    "author": "testuser",
                    "points": 100,
                    "num_comments": 25,
                    "created_at": "2023-11-15T10:30:00.000Z",
                }
            ]
        }

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_client_class.return_value = mock_client

        from core.tools import hackernews_search
        result = hackernews_search("test")

        # Verify request was made to correct endpoint
        call_args = mock_client.get.call_args
        assert "hn.algolia.com/api/v1/search" in call_args[0][0]

        data = json.loads(result)
        assert data["query"] == "test"
        assert data["count"] == 1
        assert len(data["results"]) == 1

    @patch('httpx.Client')
    def test_hackernews_search_with_sort(self, mock_client_class):
        """Test Hacker News search with sort parameter."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "hits": [
                {
                    "objectID": "12345",
                    "title": "Sort test story",
                    "url": "https://example.com/sort",
                    "author": "testuser",
                    "points": 50,
                    "num_comments": 10,
                    "created_at_i": 1699900000,
                }
            ]
        }

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_client_class.return_value = mock_client

        from core.tools import hackernews_search
        result = hackernews_search("test", sort="date")

        # Verify sort parameter caused endpoint change to search_by_date
        call_args = mock_client.get.call_args
        # When sort="date", the endpoint should be search_by_date
        assert "search_by_date" in call_args[0][0]

        data = json.loads(result)
        assert data["sort"] == "date"

    @patch('httpx.Client')
    def test_hackernews_search_result_fields(self, mock_client_class):
        """Test that all expected fields are returned in results."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "hits": [
                {
                    "objectID": "99999",
                    "title": "Complete Story",
                    "url": "https://example.com/complete",
                    "author": "complete_author",
                    "points": 200,
                    "num_comments": 75,
                    "created_at": "2023-11-15T12:00:00.000Z",
                }
            ]
        }

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_client_class.return_value = mock_client

        from core.tools import hackernews_search
        result = hackernews_search("complete")

        data = json.loads(result)

        assert len(data["results"]) == 1
        result_item = data["results"][0]
        assert result_item["title"] == "Complete Story"
        assert result_item["url"] == "https://example.com/complete"
        assert result_item["author"] == "complete_author"
        assert result_item["points"] == 200
        assert result_item["num_comments"] == 75
        # Implementation returns objectID, not hn_id
        assert "objectID" in result_item
        assert result_item["objectID"] == "99999"

    @patch('httpx.Client')
    def test_hackernews_search_empty_results(self, mock_client_class):
        """Test handling of empty results."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"hits": []}

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_client_class.return_value = mock_client

        from core.tools import hackernews_search
        result = hackernews_search("nonexistent query that returns nothing")
        data = json.loads(result)

        assert data["count"] == 0
        assert len(data["results"]) == 0

    def test_hackernews_search_empty_query(self):
        """Test handling of empty query."""
        from core.tools import hackernews_search
        result = hackernews_search("")
        data = json.loads(result)
        assert "error" in data

    def test_hackernews_search_whitespace_query(self):
        """Test handling of whitespace-only query."""
        from core.tools import hackernews_search
        result = hackernews_search("   ")
        data = json.loads(result)
        assert "error" in data

    @patch('httpx.Client')
    def test_hackernews_search_multiple_results(self, mock_client_class):
        """Test handling of multiple results."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "hits": [
                {"objectID": "1", "title": "First", "url": "https://1.com", "author": "a1", "points": 10, "num_comments": 5},
                {"objectID": "2", "title": "Second", "url": "https://2.com", "author": "a2", "points": 20, "num_comments": 10},
                {"objectID": "3", "title": "Third", "url": "https://3.com", "author": "a3", "points": 30, "num_comments": 15},
            ]
        }

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_client_class.return_value = mock_client

        from core.tools import hackernews_search
        result = hackernews_search("test", num_results=10)
        data = json.loads(result)

        assert data["count"] == 3
        assert len(data["results"]) == 3

    @patch('httpx.Client')
    def test_hackernews_search_error_handling(self, mock_client_class):
        """Test error handling for HTTP errors."""
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = Exception("Network error")
        mock_client_class.return_value = mock_client

        from core.tools import hackernews_search
        result = hackernews_search("error test")
        data = json.loads(result)
        assert "error" in data