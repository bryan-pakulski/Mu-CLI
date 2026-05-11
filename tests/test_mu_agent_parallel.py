"""Tests for `mu.agent.parallel` — concurrent tool dispatch.

Pins: result ordering matches input order; max_concurrency caps in-flight
work; errors are captured per-call without aborting the batch; async and
sync handlers both work.
"""

import asyncio
import time

import pytest

from mu.agent.parallel import (
    ToolCall,
    ToolResult,
    execute_calls,
    execute_calls_async,
)


def test_results_preserve_input_order():
    # Build calls whose handlers complete in reverse order — slowest first.
    calls = [
        ToolCall(tool_name="a", tool_args={"delay": 0.10}),
        ToolCall(tool_name="b", tool_args={"delay": 0.05}),
        ToolCall(tool_name="c", tool_args={"delay": 0.01}),
    ]

    def handler(call: ToolCall):
        time.sleep(call.tool_args["delay"])
        return f"result-{call.tool_name}"

    results = execute_calls(calls, handler, max_concurrency=4)
    assert [r.tool_name for r in results] == ["a", "b", "c"]
    assert [r.result for r in results] == ["result-a", "result-b", "result-c"]


def test_max_concurrency_caps_inflight():
    inflight = 0
    max_inflight = 0
    lock = asyncio.Lock()

    calls = [ToolCall(tool_name=f"t{i}", tool_args={}) for i in range(10)]

    async def handler(call: ToolCall):
        nonlocal inflight, max_inflight
        async with lock:
            inflight += 1
            if inflight > max_inflight:
                max_inflight = inflight
        await asyncio.sleep(0.02)
        async with lock:
            inflight -= 1
        return call.tool_name

    asyncio.run(execute_calls_async(calls, handler, max_concurrency=3))
    assert max_inflight <= 3
    assert max_inflight >= 1


def test_per_call_errors_do_not_abort_batch():
    calls = [
        ToolCall(tool_name="ok1", tool_args={}),
        ToolCall(tool_name="boom", tool_args={}),
        ToolCall(tool_name="ok2", tool_args={}),
    ]

    def handler(call: ToolCall):
        if call.tool_name == "boom":
            raise RuntimeError("kaboom")
        return f"value-{call.tool_name}"

    results = execute_calls(calls, handler, max_concurrency=2)
    assert [r.tool_name for r in results] == ["ok1", "boom", "ok2"]
    assert results[0].error is None and results[0].result == "value-ok1"
    assert results[1].error is not None and isinstance(results[1].error, RuntimeError)
    assert results[2].error is None and results[2].result == "value-ok2"


def test_async_handler_supported():
    calls = [ToolCall(tool_name=f"t{i}", tool_args={}) for i in range(3)]

    async def handler(call: ToolCall):
        await asyncio.sleep(0.005)
        return call.tool_name.upper()

    results = execute_calls(calls, handler, max_concurrency=2)
    assert [r.result for r in results] == ["T0", "T1", "T2"]


def test_empty_input_returns_empty():
    assert execute_calls([], lambda c: None) == []


def test_concurrency_actually_happens():
    """Verify the wall-clock is shorter than serial for parallel-safe handlers."""
    n = 4
    delay = 0.05

    def handler(call: ToolCall):
        time.sleep(delay)
        return call.tool_name

    calls = [ToolCall(tool_name=f"t{i}", tool_args={}) for i in range(n)]

    t0 = time.monotonic()
    execute_calls(calls, handler, max_concurrency=n)
    elapsed = time.monotonic() - t0

    # With concurrency = n, total time should be roughly `delay` not `n * delay`.
    # Add slack for thread-pool overhead and OS scheduling.
    assert elapsed < (n * delay) * 0.7, (
        f"expected parallel speedup but elapsed={elapsed:.3f}s for n={n}, delay={delay}"
    )
