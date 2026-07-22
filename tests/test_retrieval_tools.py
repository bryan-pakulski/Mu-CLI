"""Retrieval tool family over stored results (spec #6)."""

import json
from types import SimpleNamespace

import pytest

from mu.session.result_store import ResultStore
from mu.session.tool_cache import ToolResultCache
from mu.tools.memory.handlers import (
    compare_results,
    result_diagnostics,
    result_head,
    result_json_path,
    result_range,
    result_search,
    result_tail,
)


@pytest.fixture
def ctx_with_cache(tmp_path):
    cache = ToolResultCache(max_entries=10, max_bytes=1_000_000)
    store = ResultStore("run_rt", root=str(tmp_path), max_bytes=1_000_000, gc_age_days=0)
    cache.set_store(store)
    session = SimpleNamespace(tool_result_cache=cache)
    return SimpleNamespace(session=session), cache


def _store_body(ctx_with_cache, body, tool="read_file", args=None):
    ctx, cache = ctx_with_cache
    key = cache.store_with_locator("c1", tool, args or {"path": "x"}, body)
    return ctx, key


def test_result_range(ctx_with_cache):
    ctx, key = _store_body(ctx_with_cache, "\n".join(f"line {i}" for i in range(1, 11)))
    out = result_range({"cache_key": key, "start_line": 2, "end_line": 4}, ctx)
    assert out == "line 2\nline 3\nline 4"


def test_result_head_tail(ctx_with_cache):
    ctx, key = _store_body(ctx_with_cache, "\n".join(f"line {i}" for i in range(1, 11)))
    assert result_head({"cache_key": key, "lines": 1}, ctx) == "line 1"
    assert result_tail({"cache_key": key, "lines": 1}, ctx) == "line 10"


def test_result_search(ctx_with_cache):
    ctx, key = _store_body(ctx_with_cache, "\n".join(f"line {i}" for i in range(1, 11)))
    assert "5: line 5" in result_search({"cache_key": key, "query": "line 5"}, ctx)


def test_result_diagnostics(ctx_with_cache):
    body = "Error: boom\nError: boom\nwarning: w\nok\n"
    # Store under a cacheable tool (bash is never-cached); diagnostics is
    # content-based, so the originating tool name doesn't matter.
    ctx, key = _store_body(ctx_with_cache, body, tool="read_file")
    out = result_diagnostics({"cache_key": key}, ctx)
    assert out.count("Error: boom") == 1
    assert "warning: w" in out


def test_result_json_path(ctx_with_cache):
    body = json.dumps({"data": {"matches": [{"file": "a.py", "line": 42}]}})
    ctx, key = _store_body(ctx_with_cache, body, tool="search_for_string")
    out = result_json_path({"cache_key": key, "pointer": "/data/matches/0/file"}, ctx)
    assert "a.py" in out


def test_compare_results(ctx_with_cache):
    ctx, cache = ctx_with_cache
    k1 = cache.store_with_locator("c1", "read_file", {"path": "a"}, "line1\nline2\nline3\n")
    k2 = cache.store_with_locator("c2", "read_file", {"path": "a"}, "line1\nCHANGED\nline3\n")
    out = compare_results({"cache_key_a": k1, "cache_key_b": k2}, ctx)
    assert "CHANGED" in out
    assert "line2" in out


def test_missing_key_reports_evicted(ctx_with_cache):
    ctx, _ = ctx_with_cache
    out = result_head({"cache_key": "nonexistent"}, ctx)
    assert "not found" in out or "evicted" in out


def test_no_cache_session():
    ctx = SimpleNamespace(session=None)
    out = result_range({"cache_key": "x", "start_line": 1, "end_line": 2}, ctx)
    assert "No tool result cache" in out


def test_missing_cache_key_arg(ctx_with_cache):
    ctx, _ = ctx_with_cache
    assert "cache_key" in result_head({}, ctx).lower()