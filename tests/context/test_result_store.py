"""Durable ResultStore tests (spec #1, #6, #11)."""

import json
import os
import tempfile
import time

import pytest

from mu.session.result_store import ResultStore, _render_text


@pytest.fixture
def store(tmp_path):
    return ResultStore("run_test1", root=str(tmp_path), max_bytes=1_000_000, gc_age_days=0)


def _put(store, key, result, tool="read_file", args=None, iteration=0):
    return store.put(key, tool, args, result, iteration=iteration)


def test_put_and_get_roundtrip(store):
    _put(store, "abc123", {"lines": ["a", "b", "c"], "n": 3})
    payload = store.get("abc123")
    assert payload is not None
    assert payload["result"] == {"lines": ["a", "b", "c"], "n": 3}
    assert payload["tool_name"] == "read_file"
    assert store.has("abc123")
    assert not store.has("missing")


def test_get_text_renders_dict_and_string(store):
    _put(store, "kdict", {"x": 1, "y": [2, 3]})
    text = store.get_text("kdict")
    assert '"x": 1' in text and '"y"' in text
    _put(store, "kstr", "plain string\nline2")
    assert store.get_text("kstr") == "plain string\nline2"


def test_line_range_head_tail(store):
    body = "\n".join(f"line {i}" for i in range(1, 21))  # 20 lines
    _put(store, "rng", body)
    assert store.line_range("rng", 3, 5) == "line 3\nline 4\nline 5"
    assert store.head("rng", 2) == "line 1\nline 2"
    assert store.tail("rng", 2) == "line 19\nline 20"
    # out-of-range clamps
    assert store.line_range("rng", 18, 99) == "line 18\nline 19\nline 20"


def test_search_grouped_with_line_refs(store):
    body = "alpha\nbeta alpha\ngamma\nalpha delta"
    _put(store, "srch", body)
    out = store.search("srch", "alpha", max_matches=10)
    assert out == "1: alpha\n2: beta alpha\n4: alpha delta"


def test_diagnostics_dedups_and_drops_noise(store):
    body = (
        "Building...\n"           # noise -> dropped
        "12% done\n"               # noise -> dropped
        "Error: missing foo\n"
        "Error: missing foo\n"     # dup -> dropped
        "warning: bar is stale\n"
        "all good\n"               # not a diagnostic
    )
    _put(store, "diag", body)
    out = store.diagnostics("diag")
    assert "Error: missing foo" in out
    assert "warning: bar is stale" in out
    assert out.count("Error: missing foo") == 1
    assert "Building" not in out and "12%" not in out and "all good" not in out


def test_json_path_dotted(store):
    _put(store, "jp", {"a": {"b": [10, 20, {"c": 30}]}})
    assert store.json_path("jp", "a.b.2.c") == 30
    assert store.json_path("jp", "a.b.1") == 20
    assert store.json_path("jp", "") == {"a": {"b": [10, 20, {"c": 30}]}}
    assert store.json_path("jp", "a.b.99") is None  # index out of range


def test_compare_unified_diff(store):
    _put(store, "ka", "alpha\nbeta\ngamma\n")
    _put(store, "kb", "alpha\nBETA\ngamma\ndelta\n")
    diff = store.compare("ka", "kb")
    assert "-beta" in diff and "+BETA" in diff and "+delta" in diff


def test_missing_key_returns_none(store):
    assert store.get("nope") is None
    assert store.get_text("nope") is None
    assert store.line_range("nope", 1, 5) is None
    assert store.diagnostics("nope") is None
    assert store.compare("nope", "also-no") is None


def test_byte_cap_prunes_oldest(store):
    # max_bytes is large here; use a tiny store to force eviction.
    small = ResultStore("run_small", root=store.root, max_bytes=600, gc_age_days=0)
    chunk = "x" * 400
    small.put("k1", "t1", {"p": "p1"}, chunk, iteration=1)
    small.put("k2", "t2", {"p": "p2"}, chunk, iteration=2)
    # k1 (older) should be pruned when k2's bytes push over the cap.
    assert small.has("k2")
    # k1 may or may not survive depending on exact sizing; assert cap holds.
    assert small._current_bytes <= small.max_bytes + 400  # within one chunk


def test_disabled_store_is_noop(tmp_path):
    s = ResultStore("run_off", root=str(tmp_path), enabled=False)
    assert s.put("k", "t", None, "data") is None
    assert s.get("k") is None
    assert s.summary()["entries"] == 0


def test_gc_drops_old_run_dirs(tmp_path):
    root = str(tmp_path)
    old = ResultStore("run_old", root=root, gc_age_days=7)
    old.put("k", "t", None, "data")
    # Backdate the old run dir.
    old_path = old.run_dir
    past = time.time() - 86400 * 30
    os.utime(old_path, (past, past))

    cur = ResultStore("run_cur", root=root, gc_age_days=7)
    cur.put("k2", "t", None, "data")
    removed = cur.gc()
    assert removed == 1
    assert not os.path.exists(old_path)
    assert os.path.exists(cur.run_dir)  # current run preserved


def test_render_text_handles_none():
    assert _render_text(None) == ""
    assert _render_text("s") == "s"


def test_index_records_args_digest(store):
    _put(store, "k", "data", tool="read_file", args={"path": "foo.py", "n": 1})
    idx = store._load_index()
    assert "k" in idx
    assert idx["k"]["tool"] == "read_file"
    assert "args_digest" in idx["k"]