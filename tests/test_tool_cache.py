"""Tests for ToolResultCache payload fix.

The bug: loop_body.py stored the structured result envelope (with collation
metadata like {"collated": True, "source_char_count": 4500}) in the cache
instead of the raw source_result. recall() then returned useless metadata,
not the file content the model needed — causing implementation loops where
the agent re-read the same files forever.

The fix: store source_result (raw tool output) in the cache.
"""

import json

import pytest

from mu.session.tool_cache import ToolResultCache


def test_cache_stores_and_recalls_raw_content():
    """recall() should return the exact content that was stored."""
    cache = ToolResultCache()
    raw_content = "def hello():\n    print('hello world')\n"
    key = cache.store(
        call_id="call_1",
        tool_name="read_file",
        result=raw_content,
    )
    assert key is not None
    recalled = cache.recall(key)
    assert recalled is not None
    assert recalled["tool_name"] == "read_file"
    assert recalled["result"] == raw_content


def test_cache_recall_returns_none_for_evicted_key():
    cache = ToolResultCache(max_entries=2)
    k1 = cache.store("c1", "read_file", "content1")
    cache.store("c2", "read_file", "content2")
    cache.store("c3", "read_file", "content3")  # evicts k1
    assert cache.recall(k1) is None


def test_cache_recall_returns_none_for_unknown_key():
    cache = ToolResultCache()
    assert cache.recall("nonexistent") is None


def test_cache_does_not_store_write_tools():
    """Write tools (write_file, bash, etc.) should not be cached."""
    cache = ToolResultCache()
    key = cache.store("c1", "write_file", "wrote something")
    assert key is None


def test_cache_stores_search_results():
    """search_for_string results should be cacheable."""
    cache = ToolResultCache()
    search_result = "/path/to/file.py:42 -> result_line"
    key = cache.store("c1", "search_for_string", search_result)
    assert key is not None
    recalled = cache.recall(key)
    assert recalled["result"] == search_result


def test_cache_stores_structured_dict_content():
    """When source_result is a structured dict (from a tool handler),
    recall should return that dict, not a metadata wrapper."""
    cache = ToolResultCache()
    structured = {
        "ok": True,
        "message": "file content here",
        "data": {"content": "actual file text"},
    }
    key = cache.store("c1", "read_file", structured)
    assert key is not None
    recalled = cache.recall(key)
    assert recalled["result"] == structured
    assert recalled["result"]["data"]["content"] == "actual file text"


def test_cache_key_is_deterministic():
    """Same input → same key."""
    cache = ToolResultCache()
    k1 = cache.store("c1", "read_file", "same content")
    k2 = cache.store("c1", "read_file", "same content")
    assert k1 == k2


def test_cache_key_differs_for_different_content():
    cache = ToolResultCache()
    k1 = cache.store("c1", "read_file", "content A")
    k2 = cache.store("c1", "read_file", "content B")
    assert k1 != k2


def test_cache_lru_touch_on_recall():
    """Recalling an entry should move it to the end (most recently used)."""
    cache = ToolResultCache(max_entries=3)
    k1 = cache.store("c1", "read_file", "A")
    k2 = cache.store("c2", "read_file", "B")
    k3 = cache.store("c3", "read_file", "C")
    # Touch k1 → it becomes most recently used.
    cache.recall(k1)
    # Add k4 → should evict k2 (least recently used now), not k1.
    k4 = cache.store("c4", "read_file", "D")
    assert cache.recall(k1) is not None  # k1 survived
    assert cache.recall(k2) is None      # k2 evicted