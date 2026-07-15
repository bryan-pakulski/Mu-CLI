"""GUI WebUI delta coalescing.

Verifies the fix for the GUI "freeze while a live turn keeps streaming"
symptom: a fast provider (local Ollama) emits one ``assistant_delta`` /
``thinking_delta`` per token, and the old :class:`WebUI` published every
one of them through the bus individually — flooding the event loop with
``publish_threadsafe`` calls and starving every other HTTP route (loading
traces, navigating sessions, reopening the GUI) even though the existing
SSE stream kept trickling.

The coalescer buffers deltas on the agent thread and flushes at most ~one
batch every ``_DELTAS_FLUSH_MS`` (plus a size cap and a forced flush on
boundaries), so N rapid deltas collapse into a handful of publishes with no
lost text and correct ordering relative to ``tool_call`` / ``assistant_end``.
"""

from __future__ import annotations

import time

import pytest

from mu.gui.bus import EventBus
from mu.gui.prompts import PromptStore
from mu.gui.web_ui import WebUI


class _RecordingBus(EventBus):
    """EventBus that records every published event synchronously instead
    of routing through a loop. Lets the test count publishes + inspect
    payloads without an asyncio loop running."""

    def __init__(self):
        # Skip EventBus.__init__ — we don't want the real subscriber plumbing.
        self.events: list[dict] = []

    def publish_threadsafe(self, event):  # type: ignore[override]
        self.events.append(event)


def _make_ui() -> tuple[WebUI, _RecordingBus]:
    bus = _RecordingBus()
    ui = WebUI(bus, PromptStore(), session_name="s1")
    return ui, bus


def _deltas(bus: _RecordingBus, kind: str) -> list[str]:
    return [e["text"] for e in bus.events if e.get("kind") == kind]


def test_many_rapid_deltas_collapse_to_few_publishes():
    ui, bus = _make_ui()
    # 200 tiny deltas fired as fast as the loop can (well under the flush
    # window) — this is the Ollama "hundreds of chunks/sec" shape.
    for i in range(200):
        ui.stream_assistant_delta("x")
    # Force a final flush the way assistant_end would.
    ui.stream_assistant_end()

    asst_deltas = [e for e in bus.events if e["kind"] == "assistant_delta"]
    # Before the fix this would be ~200 assistant_delta publishes. After,
    # the whole burst collapses to a small number of batches — at most one
    # per flush window plus the forced flush, never one-per-token.
    assert len(asst_deltas) <= 5, len(asst_deltas)
    # And the text is reconstructed exactly — nothing lost in coalescing.
    assert "".join(e["text"] for e in asst_deltas) == "x" * 200
    # assistant_start precedes the first delta; assistant_end closes the turn.
    kinds = [e["kind"] for e in bus.events]
    assert kinds[0] == "assistant_start"
    assert kinds[-1] == "assistant_end"
    assert "assistant_delta" in kinds


def test_no_text_no_publish():
    ui, bus = _make_ui()
    ui.stream_assistant_delta("")
    ui.stream_assistant_end()  # turn_id is None → no-op (no deltas this turn)
    assert bus.events == []


def test_tool_call_forces_flush_preserving_order():
    ui, bus = _make_ui()
    ui.stream_assistant_delta("hello ")
    ui.stream_assistant_delta("world")
    # tool_call is a boundary — it must flush any buffered deltas *first*
    # so the tool-call marker lands after the text that preceded it.
    ui.stream_tool_call("edit_file")
    ui.stream_assistant_end()

    kinds = [e["kind"] for e in bus.events]
    # Every assistant_delta precedes tool_call (the marker lands after the
    # text that preceded it); assistant_end closes the turn last.
    asst_idx = [i for i, k in enumerate(kinds) if k == "assistant_delta"]
    tc_idx = kinds.index("tool_call")
    assert asst_idx, "expected at least one assistant_delta"
    assert max(asst_idx) < tc_idx, kinds
    assert kinds[-1] == "assistant_end"
    # And the text reconstructs exactly — the first delta may flush on its
    # own because the flush clock starts at 0.0, but nothing is lost.
    asst = _deltas(bus, "assistant_delta")
    assert "".join(asst) == "hello world"
    tool_call = next(e for e in bus.events if e["kind"] == "tool_call")
    assert tool_call["tool_name"] == "edit_file"


def test_assistant_end_forces_flush():
    ui, bus = _make_ui()
    ui.stream_assistant_delta("a")
    ui.stream_assistant_delta("b")
    ui.stream_assistant_delta("c")
    ui.stream_assistant_end()

    asst = _deltas(bus, "assistant_delta")
    # The buffered "abc" must be flushed before assistant_end closes the turn.
    assert "".join(asst) == "abc"
    kinds = [e["kind"] for e in bus.events]
    assert kinds[-1] == "assistant_end"


def test_thinking_before_assistant_published_immediately():
    """Reasoning models emit thinking before any assistant text. There's
    no turn bubble yet, so the first thinking_delta publishes immediately
    (preserving the pre-coalescer behavior) — it doesn't get stuck in a
    buffer waiting for a turn that hasn't started."""
    ui, bus = _make_ui()
    ui.stream_thinking_delta("pondering")
    ui.stream_assistant_delta("answer")
    ui.stream_assistant_end()

    kinds = [e["kind"] for e in bus.events]
    # thinking_delta (turn_id None) comes out before assistant_start.
    assert kinds[0] == "thinking_delta"
    assert kinds[1] == "assistant_start"
    assert _deltas(bus, "thinking_delta")[0] == "pondering"
    assert "".join(_deltas(bus, "assistant_delta")) == "answer"


def test_thinking_after_turn_buffers_and_flushes():
    ui, bus = _make_ui()
    ui.stream_assistant_delta("a")
    for i in range(50):
        ui.stream_thinking_delta("z")
    ui.stream_assistant_end()

    think = _deltas(bus, "thinking_delta")
    # The 50 thinking deltas collapse to a handful of batches.
    assert len(think) <= 5
    assert "".join(think) == "z" * 50


def test_size_cap_forces_flush_before_window():
    """If a single chunk is bigger than the size cap, it flushes immediately
    even though the time window hasn't elapsed."""
    from mu.gui.web_ui import _DELTAS_MAX_CHARS

    ui, bus = _make_ui()
    big = "A" * (_DELTAS_MAX_CHARS + 10)
    ui.stream_assistant_delta(big)
    # A second small delta right after should start a fresh buffer.
    ui.stream_assistant_delta("b")
    ui.stream_assistant_end()

    asst = _deltas(bus, "assistant_delta")
    joined = "".join(asst)
    assert joined == big + "b"
    # The oversized chunk alone exceeds the cap, so it must have flushed
    # before "b" was appended — at least 2 assistant_delta publishes.
    assert len(asst) >= 2


def test_concurrent_deltas_do_not_lose_or_corrupt_text():
    """Two threads streaming deltas through one WebUI (defensive — subagents
    don't actually share the root WebUI's delta methods, but the buffer
    lock must keep the text intact if they ever did)."""
    import threading

    ui, bus = _make_ui()
    N = 100

    def worker(marker: str):
        for i in range(N):
            ui.stream_assistant_delta(marker)

    t1 = threading.Thread(target=worker, args=("A",))
    t2 = threading.Thread(target=worker, args=("B",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    ui.stream_assistant_end()

    joined = "".join(_deltas(bus, "assistant_delta"))
    # Every delta made it through, exactly once, with no garbled overlap.
    assert joined.count("A") == N
    assert joined.count("B") == N
    assert len(joined) == 2 * N


def test_publish_drains_deltas_before_non_delta_event():
    """Any non-delta event published via _publish must drain buffered deltas
    first (e.g. show_info mid-turn shouldn't leapfrog buffered text)."""
    ui, bus = _make_ui()
    ui.stream_assistant_delta("buffered")
    ui.show_info("heads up")  # routes through the flush-first _publish
    ui.stream_assistant_end()

    kinds = [e["kind"] for e in bus.events]
    # The buffered assistant_delta precedes the info event.
    assert kinds.index("assistant_delta") < kinds.index("info")
    assert _deltas(bus, "assistant_delta") == ["buffered"]
    assert any(e["kind"] == "info" and e["text"] == "heads up" for e in bus.events)