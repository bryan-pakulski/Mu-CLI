"""Efficiency metrics aggregation (spec #12)."""

from types import SimpleNamespace

from mu.session.efficiency_metrics import (
    accumulate_tool_result,
    collect_efficiency_metrics,
    is_retrieval_tool,
    reset_per_turn_accumulators,
)
from mu.session.tool_cache import ToolResultCache


def _session_with_cache():
    cache = ToolResultCache()
    cache.evictions = 2
    cache.invalidations = 1
    cache.disk_hits = 3
    cache.dup_bytes_avoided = 5
    cache.locator_hits = 7
    sess = SimpleNamespace(tool_result_cache=cache)
    reset_per_turn_accumulators(sess)
    return sess


def test_collect_reads_cache_snapshot():
    sess = _session_with_cache()
    m = collect_efficiency_metrics(sess)
    assert m["cache"]["evictions"] == 2
    assert m["cache"]["invalidations"] == 1
    assert m["cache"]["disk_hits"] == 3
    assert m["cache"]["dup_bytes_avoided"] == 5
    assert m["cache"]["locator_hits"] == 7


def test_accumulate_and_compression_ratio():
    sess = _session_with_cache()
    # Two tool results: one verbatim (raw==injected), one observed (raw >> injected).
    accumulate_tool_result(sess, {
        "telemetry": {"raw_token_count": 100, "injected_token_count": 100}
    })
    accumulate_tool_result(sess, {
        "telemetry": {
            "raw_token_count": 2000,
            "injected_token_count": 200,
            "delivery_mode": "observed",
        },
        "data": {"omitted": True},
    })
    m = collect_efficiency_metrics(sess)
    assert m["raw_tool_tokens"] == 2100
    assert m["injected_tool_tokens"] == 300
    assert m["omitted_results"] == 1
    assert m["tokens_saved"] == 1800
    assert m["compression_ratio"] == round(1800 / 2100, 3)


def test_retrieval_rate():
    sess = _session_with_cache()
    sess._eff_retrievals = 2
    m = collect_efficiency_metrics(sess, tool_calls_this_turn=10, retrieval_calls_this_turn=2)
    assert m["retrieval_calls"] == 2
    assert m["tool_calls"] == 10
    assert m["retrieval_rate"] == 0.2


def test_tool_output_share():
    sess = _session_with_cache()
    m = collect_efficiency_metrics(
        sess, tool_result_tokens=4000, total_context_tokens=20000
    )
    assert m["tool_output_share"] == 0.2


def test_reset_zeroes_accumulators():
    sess = _session_with_cache()
    sess._eff_raw_tokens = 999
    reset_per_turn_accumulators(sess)
    assert sess._eff_raw_tokens == 0
    assert sess._eff_injected_tokens == 0


def test_is_retrieval_tool():
    assert is_retrieval_tool("recall")
    assert is_retrieval_tool("result_range")
    assert is_retrieval_tool("compare_results")
    assert not is_retrieval_tool("read_file")
    assert not is_retrieval_tool("bash")


def test_collect_handles_missing_cache():
    sess = SimpleNamespace()
    m = collect_efficiency_metrics(sess)
    assert m["raw_tool_tokens"] == 0
    assert m["compression_ratio"] == 0.0
    assert m["cache"] == {}