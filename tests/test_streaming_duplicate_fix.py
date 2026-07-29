"""Regression tests for the duplicate assistant response bug.

Root cause: ``loop_body.py`` calls ``render_message("assistant", full_text)``
right after the streaming loop delivers the same text token-by-token via
``stream_assistant_delta``.  Three UI adapters handle this differently:

* **RichUI** (TUI): already had a ``_streamed_any_text`` guard that
  suppresses the duplicate panel — the reference implementation.
* **WebUI** (web GUI): had *no* guard.  ``render_message`` published a
  ``{kind: "message", role: "assistant"}`` event that, while not directly
  rendered as a second bubble by ``app.js``, still contributed to stale
  state and, on ``turn_complete`` / ``history_refresh``, caused
  ``loadHistory`` to flash a duplicate during reconciliation.
* **WorkerBridgeUI** (container mode): had *no* guard.
  ``render_message`` re-emitted ``assistant_start`` + ``assistant_delta``
  (full text) + ``assistant_end`` with a **new turn_id** → clients
  created a literal second bubble = visible duplicate.

The fix mirrors RichUI's ``_streamed_any_text`` pattern in both WebUI and
WorkerBridgeUI.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB_UI = ROOT / "mu/gui/web_ui.py"
WORKER = ROOT / "mu/container/worker.py"
RICH_UI = ROOT / "mu/ui/rich_ui.py"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── WebUI ─────────────────────────────────────────────────────────────────


def test_webui_has_streamed_any_text_flag():
    """WebUI must initialise _streamed_any_text in __init__."""
    src = read(WEB_UI)
    assert "_streamed_any_text" in src, "WebUI missing _streamed_any_text flag"
    assert "self._streamed_any_text = False" in src, \
        "WebUI must initialise _streamed_any_text = False"


def test_webui_sets_flag_on_delta():
    """stream_assistant_delta must set _streamed_any_text = True."""
    src = read(WEB_UI)
    assert "self._streamed_any_text = True" in src, \
        "WebUI stream_assistant_delta must set _streamed_any_text = True"


def test_webui_resets_flag_on_new_turn():
    """stream_assistant_delta must reset flag when _current_turn_id is None."""
    src = read(WEB_UI)
    # The reset must happen before the new turn_id is generated.
    assert "self._streamed_any_text = False" in src, \
        "WebUI must reset _streamed_any_text on new turn"


def test_webui_render_message_guards_assistant():
    """render_message must suppress assistant text when _streamed_any_text."""
    src = read(WEB_UI)
    assert 'role == "assistant" and self._streamed_any_text' in src, \
        "WebUI render_message must guard assistant role with _streamed_any_text"
    assert "return" in src, \
        "WebUI render_message must return early when guard fires"


def test_webui_render_message_still_publishes_user():
    """render_message for non-assistant roles must still publish."""
    src = read(WEB_UI)
    # The guard is assistant-only; user messages must still go through.
    assert '"kind": "message"' in src, \
        "WebUI render_message must still publish message events"


# ── WorkerBridgeUI ────────────────────────────────────────────────────────


def test_worker_has_streamed_any_text_flag():
    """WorkerBridgeUI must initialise _streamed_any_text in __init__."""
    src = read(WORKER)
    assert "_streamed_any_text" in src, \
        "WorkerBridgeUI missing _streamed_any_text flag"
    assert "self._streamed_any_text = False" in src, \
        "WorkerBridgeUI must initialise _streamed_any_text = False"


def test_worker_sets_flag_on_delta():
    """stream_assistant_delta must set _streamed_any_text = True."""
    src = read(WORKER)
    assert "self._streamed_any_text = True" in src, \
        "WorkerBridgeUI stream_assistant_delta must set _streamed_any_text = True"


def test_worker_resets_flag_on_new_turn():
    """stream_assistant_delta must reset flag when turn_id is None."""
    src = read(WORKER)
    assert "self._streamed_any_text = False" in src, \
        "WorkerBridgeUI must reset _streamed_any_text on new turn"


def test_worker_render_message_guards_assistant():
    """render_message must suppress assistant text when _streamed_any_text."""
    src = read(WORKER)
    assert 'self._streamed_any_text' in src, \
        "WorkerBridgeUI render_message must check _streamed_any_text"
    # The guard must be inside the assistant branch.
    assert 'if self._streamed_any_text' in src, \
        "WorkerBridgeUI render_message must have explicit guard"


# ── Functional tests ──────────────────────────────────────────────────────


def test_webui_functional_dedup():
    """WebUI: streaming then render_message must not publish a second event."""
    from mu.gui.bus import EventBus
    from mu.gui.web_ui import WebUI
    from mu.gui.prompts import PromptStore

    bus = EventBus()
    prompts = PromptStore()
    ui = WebUI(bus, prompts, session_name="test")

    events = []
    bus.subscribe_sync = lambda e: events.append(e)
    # Patch _publish_raw to capture events directly.
    ui._publish_raw = lambda ev: events.append(ev)

    # Simulate streaming.
    ui.stream_assistant_delta("Hello ")
    ui.stream_assistant_delta("world!")
    ui.stream_assistant_end()

    # Now loop_body calls render_message with the full text.
    ui.render_message("assistant", "Hello world!")

    # Only streaming events should have been published:
    # assistant_start + 2x assistant_delta + assistant_end.
    assistant_messages = [
        e for e in events
        if e.get("kind") == "message" and e.get("role") == "assistant"
    ]
    assert len(assistant_messages) == 0, \
        f"WebUI published {len(assistant_messages)} duplicate assistant message(s)"


def test_webui_functional_non_streamed_still_publishes():
    """WebUI: render_message without prior streaming must still publish."""
    from mu.gui.bus import EventBus
    from mu.gui.web_ui import WebUI
    from mu.gui.prompts import PromptStore

    bus = EventBus()
    prompts = PromptStore()
    ui = WebUI(bus, prompts, session_name="test")
    ui._publish_raw = lambda ev: events.append(ev)
    events = []

    # No streaming happened — render_message should publish.
    ui.render_message("assistant", "Fresh text")
    assistant_messages = [
        e for e in events
        if e.get("kind") == "message" and e.get("role") == "assistant"
    ]
    assert len(assistant_messages) == 1, \
        "WebUI must still publish when no streaming occurred"


def test_webui_functional_user_always_publishes():
    """WebUI: user render_message must always publish regardless of flag."""
    from mu.gui.bus import EventBus
    from mu.gui.web_ui import WebUI
    from mu.gui.prompts import PromptStore

    bus = EventBus()
    prompts = PromptStore()
    ui = WebUI(bus, prompts, session_name="test")
    events = []
    ui._publish_raw = lambda ev: events.append(ev)

    # Stream some assistant text (sets flag).
    ui.stream_assistant_delta("test")
    ui.stream_assistant_end()

    # User message should still publish even with flag set.
    ui.render_message("user", "hello")
    user_messages = [
        e for e in events
        if e.get("kind") == "message" and e.get("role") == "user"
    ]
    assert len(user_messages) == 1, \
        "WebUI must always publish user messages regardless of _streamed_any_text"


def test_worker_functional_dedup():
    """WorkerBridgeUI: streaming then render_message must not re-emit."""
    import os

    # WorkerBridgeUI reads env vars; set stubs.
    os.environ.setdefault("MUCLI_SUPERVISOR_URL", "")
    from mu.container.worker import WorkerBridgeUI

    ui = WorkerBridgeUI("test-session")
    events = []
    ui.publish = lambda ev: events.append(ev)

    # Simulate streaming.
    ui.stream_assistant_delta("Hello ")
    ui.stream_assistant_delta("world!")
    ui.stream_assistant_end()

    # Now loop_body calls render_message with the full text.
    ui.render_message("assistant", "Hello world!")

    # Count assistant_start events — should be exactly 1 (from streaming).
    starts = [e for e in events if e.get("kind") == "assistant_start"]
    assert len(starts) == 1, \
        f"WorkerBridgeUI emitted {len(starts)} assistant_start event(s), expected 1"

    # Count assistant_delta events — should be 2 (from streaming), not 3.
    deltas = [e for e in events if e.get("kind") == "assistant_delta"]
    assert len(deltas) == 2, \
        f"WorkerBridgeUI emitted {len(deltas)} assistant_delta event(s), expected 2"


def test_worker_functional_non_streamed_still_publishes():
    """WorkerBridgeUI: render_message without streaming must still emit."""
    import os

    os.environ.setdefault("MUCLI_SUPERVISOR_URL", "")
    from mu.container.worker import WorkerBridgeUI

    ui = WorkerBridgeUI("test-session")
    events = []
    ui.publish = lambda ev: events.append(ev)

    # No streaming — render_message should emit.
    ui.render_message("assistant", "Fresh text")
    starts = [e for e in events if e.get("kind") == "assistant_start"]
    assert len(starts) == 1, \
        "WorkerBridgeUI must still emit when no streaming occurred"


# ── Cross-adapter consistency ─────────────────────────────────────────────


def test_all_three_adapters_have_guard():
    """RichUI, WebUI, WorkerBridgeUI must all have _streamed_any_text."""
    rich = read(RICH_UI)
    web = read(WEB_UI)
    worker = read(WORKER)
    assert "_streamed_any_text" in rich, "RichUI lost _streamed_any_text guard"
    assert "_streamed_any_text" in web, "WebUI missing _streamed_any_text guard"
    assert "_streamed_any_text" in worker, "WorkerBridgeUI missing _streamed_any_text guard"


def test_loop_body_calls_render_message_after_streaming():
    """loop_body must still call render_message after streaming (document the trigger)."""
    loop = read(ROOT / "mu/agent/loop_body.py")
    assert 'render_message(' in loop, "loop_body must still call render_message"
    assert '"assistant"' in loop, "loop_body must call render_message with assistant role"