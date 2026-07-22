"""ToolResultCache write-through durability + bounded-op tests (spec #1/#6/#11)."""

import json
import tempfile

import pytest

from mu.session.result_store import ResultStore
from mu.session.tool_cache import ToolResultCache


@pytest.fixture
def cache_with_store(tmp_path):
    cache = ToolResultCache(max_entries=2, max_bytes=10_000)
    store = ResultStore("run_tc", root=str(tmp_path), max_bytes=1_000_000, gc_age_days=0)
    cache.set_store(store)
    return cache, store


def test_store_writes_through_to_disk(cache_with_store):
    cache, store = cache_with_store
    key = cache.store_with_locator("call1", "read_file", {"path": "foo.py"}, "FILE CONTENTS")
    assert key is not None
    payload = store.get(key)
    assert payload is not None
    assert payload["result"] == "FILE CONTENTS"
    assert payload["tool_name"] == "read_file"
    assert payload["tool_args"] == {"path": "foo.py"}


def test_recall_falls_back_to_disk_after_memory_eviction(cache_with_store):
    cache, store = cache_with_store
    k1 = cache.store_with_locator("c1", "read_file", {"path": "a.py"}, "A" * 100)
    k2 = cache.store_with_locator("c2", "read_file", {"path": "b.py"}, "B" * 100)
    k3 = cache.store_with_locator("c3", "read_file", {"path": "c.py"}, "C" * 100)
    # max_entries=2 -> k1 evicted from memory.
    assert k1 not in cache._cache
    # But still recallable from disk.
    rec = cache.recall(k1)
    assert rec is not None
    assert rec["result"] == "A" * 100
    assert rec.get("from_disk") is True
    assert cache.disk_hits == 1


def test_recall_memory_hit_does_not_touch_disk(cache_with_store):
    cache, store = cache_with_store
    k = cache.store_with_locator("c1", "read_file", {"path": "a.py"}, "IN MEM")
    rec = cache.recall(k)
    assert rec["result"] == "IN MEM"
    assert "from_disk" not in rec
    assert cache.disk_hits == 0


def test_bounded_ops_delegate_to_store(cache_with_store):
    cache, store = cache_with_store
    body = "\n".join(f"line {i}" for i in range(1, 11))
    key = cache.store_with_locator("c1", "read_file", {"path": "a.py"}, body)
    assert cache.line_range(key, 2, 4) == "line 2\nline 3\nline 4"
    assert cache.head(key, 1) == "line 1"
    assert cache.tail(key, 1) == "line 10"
    assert cache.search(key, "line 5") == "5: line 5"
    assert "line 1" in cache.diagnostics(key, max_lines=1) or cache.diagnostics(key, max_lines=1) == ""


def test_bounded_ops_none_without_store():
    cache = ToolResultCache()  # no store attached
    assert cache.line_range("x", 1, 2) is None
    assert cache.head("x") is None
    assert cache.diagnostics("x") is None
    assert cache.compare("a", "b") is None


def test_eviction_counter_increments(cache_with_store):
    cache, store = cache_with_store
    cache.store_with_locator("c1", "read_file", {"path": "a.py"}, "A" * 100)
    cache.store_with_locator("c2", "read_file", {"path": "b.py"}, "B" * 100)
    cache.store_with_locator("c3", "read_file", {"path": "c.py"}, "C" * 100)
    assert cache.evictions >= 1


def test_metrics_snapshot_shape(cache_with_store):
    cache, store = cache_with_store
    snap = cache.metrics_snapshot()
    assert set(snap.keys()) == {
        "evictions", "invalidations", "disk_hits", "dup_bytes_avoided", "locator_hits",
    }


def test_write_through_failure_does_not_break_store(tmp_path):
    """A disk write failure must not raise into the loop (best-effort)."""
    cache = ToolResultCache()
    # Point the store at a path that can't be created (root under a file).
    bad = ResultStore("run_bad", root=str(tmp_path / "afile"), max_bytes=1000)
    # make 'afile' a regular file so makedirs(run_dir) fails
    (tmp_path / "afile").write_text("x")
    cache.set_store(bad)
    # Should not raise; result still cached in memory.
    key = cache.store("c1", "read_file", "DATA")
    assert key is not None
    assert cache.recall(key)["result"] == "DATA"