"""File-read dedup + provable caching (spec #7/#8): content-hash freshness,
range memo, and the _path_arg fix that makes auto-recall work for read_file/
get_chunk (which key paths under `filename`/`file`, not `path`)."""

import os
import time

import pytest

from mu.session.tool_cache import ToolResultCache


def _write(path, content):
    path.write_text(content)
    # Ensure mtime ticks forward so the mtime+size check alone would pass
    # even when content differs (we want content_hash to catch the change).
    return path


def test_path_arg_handles_filename_and_file():
    assert ToolResultCache._path_arg({"filename": "a.py"}) == "a.py"
    assert ToolResultCache._path_arg({"file": "b.py"}) == "b.py"
    assert ToolResultCache._path_arg({"path": "c.py"}) == "c.py"
    assert ToolResultCache._path_arg({"x": 1}) is None


def test_locator_records_mtime_size_and_content_hash(tmp_path):
    cache = ToolResultCache()
    cache.content_hash_enabled = True
    f = tmp_path / "x.py"
    _write(f, "content one\n")
    key = cache.store_with_locator("c1", "read_file", {"filename": str(f)}, "content one\n")
    assert key is not None
    entry = cache._cache[key]
    assert entry["mtime"] is not None
    assert entry["size"] is not None
    assert entry["content_hash"] is not None


def test_auto_recall_hits_for_read_file_unchanged(tmp_path):
    """The _path_arg fix: auto-recall now works for read_file (filename arg)."""
    cache = ToolResultCache()
    f = tmp_path / "x.py"
    _write(f, "hello\n")
    cache.store_with_locator("c1", "read_file", {"filename": str(f)}, "hello\n")
    hit = cache.lookup_by_locator("read_file", {"filename": str(f)})
    assert hit is not None
    assert hit["cache_hit"] is True
    assert hit["result"] == "hello\n"
    assert cache.locator_hits == 1


def test_content_hash_catches_same_size_change(tmp_path):
    """Same size, same mtime, different content → content_hash invalidates."""
    cache = ToolResultCache()
    cache.content_hash_enabled = True
    f = tmp_path / "x.py"
    _write(f, "AAAAAAAAAA\n")  # 11 bytes
    key = cache.store_with_locator("c1", "read_file", {"filename": str(f)}, "AAAAAAAAAA\n")
    # Rewrite with same length, force same mtime.
    f.write_text("BBBBBBBBBB\n")  # 11 bytes, same size
    st = os.stat(f)
    old_mtime = cache._cache[key]["mtime"]
    os.utime(f, (st.st_atime, old_mtime))  # preserve mtime
    hit = cache.lookup_by_locator("read_file", {"filename": str(f)})
    assert hit is None  # content_hash mismatch → invalidated
    assert cache.invalidations >= 1


def test_content_hash_disabled_skips_check(tmp_path):
    """With content_hash off, a same-mtime/same-size change is NOT caught
    (the blind spot returns) — only mtime+size gate auto-recall."""
    cache = ToolResultCache()
    cache.content_hash_enabled = False
    f = tmp_path / "x.py"
    _write(f, "AAAAAAAAAA\n")  # 11 bytes
    key = cache.store_with_locator("c1", "read_file", {"filename": str(f)}, "AAAAAAAAAA\n")
    cached_mtime = cache._cache[key]["mtime"]
    # Same size, different content, preserved mtime.
    f.write_text("BBBBBBBBBB\n")  # 11 bytes
    st = os.stat(f)
    os.utime(f, (st.st_atime, cached_mtime))
    hit = cache.lookup_by_locator("read_file", {"filename": str(f)})
    # No content_hash check → stale hit is served (the blind spot).
    assert hit is not None
    assert cache.invalidations == 0


def test_range_memo_exact_range_dedup(tmp_path):
    cache = ToolResultCache()
    f = tmp_path / "x.py"
    _write(f, "line\n" * 100)
    key = cache.store_with_locator("c1", "get_chunk", {"file": str(f), "start_line": 10, "end_line": 20}, "body")
    cache.record_read_range("get_chunk", {"file": str(f), "start_line": 10, "end_line": 20}, key)
    rr = cache.lookup_read_range("get_chunk", {"file": str(f), "start_line": 10, "end_line": 20})
    assert rr is not None
    assert rr["cache_key"] == key
    assert cache.dup_bytes_avoided == 1


def test_range_memo_miss_for_different_range(tmp_path):
    cache = ToolResultCache()
    f = tmp_path / "x.py"
    _write(f, "line\n" * 100)
    key = cache.store_with_locator("c1", "get_chunk", {"file": str(f), "start_line": 10, "end_line": 20}, "body")
    cache.record_read_range("get_chunk", {"file": str(f), "start_line": 10, "end_line": 20}, key)
    rr = cache.lookup_read_range("get_chunk", {"file": str(f), "start_line": 30, "end_line": 40})
    assert rr is None


def test_range_memo_invalidates_on_write(tmp_path):
    cache = ToolResultCache()
    f = tmp_path / "x.py"
    _write(f, "line\n" * 100)
    key = cache.store_with_locator("c1", "get_chunk", {"file": str(f), "start_line": 10, "end_line": 20}, "body")
    cache.record_read_range("get_chunk", {"file": str(f), "start_line": 10, "end_line": 20}, key)
    cache.invalidate_path(str(f))
    assert cache.lookup_read_range("get_chunk", {"file": str(f), "start_line": 10, "end_line": 20}) is None


def test_range_memo_whole_file_read_file(tmp_path):
    cache = ToolResultCache()
    f = tmp_path / "x.py"
    _write(f, "whole\n")
    key = cache.store_with_locator("c1", "read_file", {"filename": str(f)}, "whole\n")
    cache.record_read_range("read_file", {"filename": str(f)}, key)
    rr = cache.lookup_read_range("read_file", {"filename": str(f)})
    assert rr is not None and rr["cache_key"] == key