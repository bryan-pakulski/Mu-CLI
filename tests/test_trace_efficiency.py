"""Trace efficiency series + summary (spec #12 visualization backend)."""

import json
import tempfile

from mu.trace.parser import build_series, build_summary, parse_trace


def _write_trace(records):
    tmp = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
    for r in records:
        tmp.write(json.dumps(r) + "\n")
    tmp.close()
    return tmp.name


def _base():
    return [
        {"type": "run_start", "run_id": "r1", "session": "s", "model": "m",
         "provider": "p", "mode": "d", "context_limit": 8000, "max_iterations": 50},
        {"type": "iter", "iter": 1, "context": {"total_est": 100}},
        {"type": "iter", "iter": 2, "context": {"total_est": 200}},
    ]


def test_efficiency_series_aggregates_per_iter():
    recs = _base()
    recs.append({"type": "tool", "iter": 1, "name": "read_file", "ok": True,
                 "latency_ms": 10, "result_bytes": 5000, "stored": True,
                 "omitted": True, "raw_tokens": 1200, "injected_tokens": 180,
                 "delivery_mode": "observed", "store_key": "abc"})
    recs.append({"type": "tool", "iter": 2, "name": "read_file", "ok": True,
                 "latency_ms": 5, "cache_hit": True, "result_bytes": 0,
                 "stored": False, "omitted": False, "raw_tokens": 0,
                 "injected_tokens": 50, "delivery_mode": "structured"})
    recs.append({"type": "tool", "iter": 2, "name": "recall", "ok": True,
                 "latency_ms": 2, "result_bytes": 300, "raw_tokens": 300,
                 "injected_tokens": 300})
    run = parse_trace(_write_trace(recs))
    series = build_series(run)
    eff = series["efficiency"]
    assert len(eff) == 2
    assert eff[0]["raw_tokens"] == 1200
    assert eff[0]["injected_tokens"] == 180
    assert eff[0]["omitted"] == 1
    assert eff[0]["stored"] == 1
    assert eff[0]["retrievals"] == 0
    assert eff[1]["raw_tokens"] == 300
    assert eff[1]["injected_tokens"] == 350  # 50 + 300
    assert eff[1]["retrievals"] == 1
    assert eff[1]["cache_hits"] == 1


def test_tool_histogram_carries_efficiency_fields():
    recs = _base()
    recs.append({"type": "tool", "iter": 1, "name": "read_file", "ok": True,
                 "latency_ms": 10, "result_bytes": 5000, "stored": True,
                 "omitted": True, "raw_tokens": 1200, "injected_tokens": 180})
    recs.append({"type": "tool", "iter": 2, "name": "read_file", "ok": True,
                 "latency_ms": 5, "result_bytes": 1000, "stored": True,
                 "omitted": False, "raw_tokens": 1000, "injected_tokens": 1000})
    run = parse_trace(_write_trace(recs))
    h = build_series(run)["tool_histogram"][0]
    assert h["count"] == 2
    assert h["stored"] == 2
    assert h["omitted"] == 1
    assert h["raw_tokens_sum"] == 2200
    assert h["injected_tokens_sum"] == 1180
    assert h["tokens_saved"] == 1020
    assert h["compression_ratio"] == round(1020 / 2200, 3)
    assert h["avg_raw_tokens"] == 1100
    assert h["stored_rate"] == 1.0
    assert h["omitted_rate"] == 0.5


def test_summary_efficiency_block_reads_turn_end():
    recs = _base()
    recs.append({"type": "tool", "iter": 1, "name": "read_file", "ok": True,
                 "latency_ms": 10, "stored": True, "omitted": True,
                 "raw_tokens": 1200, "injected_tokens": 180})
    recs.append({"type": "tool", "iter": 2, "name": "recall", "ok": True,
                 "latency_ms": 2, "raw_tokens": 300, "injected_tokens": 300})
    recs.append({"type": "turn_end", "run_id": "r1", "status": "done",
                 "total_in": 5000, "total_out": 200, "tool_calls": 2,
                 "efficiency": {
                     "raw_tool_tokens": 1500, "injected_tool_tokens": 480,
                     "compression_ratio": 0.68, "retrieval_calls": 1,
                     "retrieval_rate": 0.5,
                     "cache": {"evictions": 2, "invalidations": 1,
                               "disk_hits": 3, "dup_bytes_avoided": 500,
                               "locator_hits": 4}}})
    run = parse_trace(_write_trace(recs))
    eff = build_summary(run, build_series(run))["efficiency"]
    assert eff["raw_tokens"] == 1500
    assert eff["injected_tokens"] == 480
    assert eff["tokens_saved"] == 1020
    assert eff["compression_ratio"] == round(1020 / 1500, 3)
    assert eff["omitted_results"] == 1
    assert eff["stored_results"] == 1
    assert eff["retrieval_calls"] == 1
    assert eff["retrieval_rate"] == 0.5
    assert eff["cache"]["evictions"] == 2
    assert eff["cache"]["invalidations"] == 1
    assert eff["cache"]["disk_hits"] == 3
    assert eff["cache"]["locator_hits"] == 4


def test_summary_efficiency_tolerates_old_traces():
    recs = _base()
    recs.append({"type": "tool", "iter": 1, "name": "bash", "ok": True,
                 "latency_ms": 10, "result_bytes": 500})  # no efficiency fields
    recs.append({"type": "turn_end", "run_id": "r1", "status": "done",
                 "total_in": 100, "total_out": 10, "tool_calls": 1})
    run = parse_trace(_write_trace(recs))
    eff = build_summary(run, build_series(run))["efficiency"]
    assert eff["raw_tokens"] == 0
    assert eff["compression_ratio"] == 0.0
    assert eff["retrieval_rate"] == 0.0
    assert "cache" not in eff  # no turn_end.efficiency.cache