"""A thread wake is a turn like any other for clients: it opens with turn_started and ends
with turn_complete (then history_refresh and thread_wake_finished), so no panel or thread
row stays busy after the wake, whether it succeeds or fails."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import mu.gui.app as gui_app


class _Bus:
    def __init__(self):
        self.events = []

    def publish_threadsafe(self, event):
        self.events.append(event)


class _Coordinator:
    group_id = "g1"

    def __init__(self):
        self.recorded = []

    def get_thread(self, thread_id):
        return {"thread_id": thread_id, "session_name": "main"}

    def record_event(self, kind, **_):
        self.recorded.append(kind)


def _app(bus):
    busy = threading.Event()
    lock = threading.Lock()
    state = SimpleNamespace(bus=bus, session_busy_for=lambda name=None: busy,
                            session_lock_for=lambda name=None: lock)
    return SimpleNamespace(state=state), busy


def _session(reply):
    def send_message(text, *, origin="user"):
        assert origin == "thread_wake"
        return reply(text)

    manager = SimpleNamespace(save_history_if_current=lambda *_: None)
    return SimpleNamespace(variables={"session_type": "workspace"}, send_message=send_message,
                           session_manager=manager, folder_context=None)


def _run(monkeypatch, reply):
    bus, coordinator = _Bus(), _Coordinator()
    app, busy = _app(bus)
    monkeypatch.setattr(gui_app, "_load_session", lambda *_a, **_k: _session(reply))
    delivered = gui_app._run_thread_wake(app, coordinator, {"target_thread_id": "t1"})
    return delivered, bus.events, coordinator.recorded, busy


def test_wake_turn_ends_with_turn_complete(monkeypatch):
    delivered, events, recorded, busy = _run(
        monkeypatch, lambda _t: {"ok": True, "status": "completed"})
    assert delivered and recorded == ["thread_wake_completed"]
    assert not busy.is_set()
    kinds = [e["kind"] for e in events]
    assert kinds == ["thread_wake_started", "turn_started", "turn_complete",
                     "history_refresh", "thread_wake_finished"]
    complete = events[2]
    assert complete["session_name"] == "main"
    assert complete["result"]["status"] == "completed"


def test_failed_wake_still_ends_the_turn(monkeypatch):
    def boom(_t):
        raise RuntimeError("provider down")

    delivered, events, recorded, busy = _run(monkeypatch, boom)
    # _run_send turns provider errors into an error result; the wake is delivered.
    assert delivered and not busy.is_set()
    complete = next(e for e in events if e["kind"] == "turn_complete")
    assert complete["result"]["status"] == "error"
    assert "provider down" in complete["result"]["error"]
    assert [e["kind"] for e in events][-2:] == ["history_refresh", "thread_wake_finished"]
