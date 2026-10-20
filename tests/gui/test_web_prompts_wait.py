"""Agent questions never time out (user decision, 2026-10-20): when the model hands the turn to
the person, the turn pauses until they answer or cancel. Stop cancels the session's open
questions; an interrupt while waiting cancels the question and propagates; the browser drops a
card when its question ends anywhere and re-syncs pending questions after reloads."""

from __future__ import annotations

import ctypes
import threading
import time

from mu.gui.prompts import PromptStore



def _web_ui(store, events):
    from mu.gui.web_ui import WebUI
    ui = WebUI.__new__(WebUI)
    ui._prompts = store
    ui.session_name = "s1"
    ui._publish = events.append
    return ui


def test_a_question_waits_past_the_old_ten_minute_limit(monkeypatch):
    """No timeout: the wait loops until answered, whatever the clock says."""
    store, events = PromptStore(), []
    ui = _web_ui(store, events)
    calls = {"n": 0}
    real_wait = threading.Event.wait

    def fast_wait(self, timeout=None):
        calls["n"] += 1
        if calls["n"] == 50:                       # the person answers "much later"
            pid = store.pending()[0]["id"]
            threading.Thread(target=store.answer, args=(pid, {"value": "yes"})).start()
        return real_wait(self, 0.01)

    monkeypatch.setattr(threading.Event, "wait", fast_wait)
    result = ui._ask_prompt({"shape": "input", "message": "Continue?"})
    assert result == {"value": "yes"} and calls["n"] >= 50
    assert [e["kind"] for e in events] == ["prompt", "prompt_resolved"]


def test_stop_cancels_the_sessions_questions_and_wakes_the_turn():
    store, events = PromptStore(), []
    ui = _web_ui(store, events)
    out = {}
    worker = threading.Thread(target=lambda: out.setdefault("r", ui._ask_prompt({"shape": "confirm", "message": "?"})))
    worker.start()
    for _ in range(100):
        if store.pending():
            break
        time.sleep(0.01)
    other_id, _ = store.open({"shape": "input", "session_name": "s2"})
    assert store.cancel_session("s1") == 1
    worker.join(2)
    assert not worker.is_alive() and out["r"] == {"cancelled": True}
    assert events[-1]["kind"] == "prompt_cancelled"
    assert [p["id"] for p in store.pending()] == [other_id]        # other sessions untouched


def test_an_interrupt_while_waiting_cancels_the_question_and_propagates():
    store, events = PromptStore(), []
    ui = _web_ui(store, events)
    raised = {}

    def run():
        try:
            ui._ask_prompt({"shape": "input", "message": "?"})
        except KeyboardInterrupt:
            raised["yes"] = True

    worker = threading.Thread(target=run)
    worker.start()
    for _ in range(100):
        if store.pending():
            break
        time.sleep(0.01)
    # The same async exception the Stop endpoint injects; it lands between wait slices.
    ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(worker.ident), ctypes.py_object(KeyboardInterrupt))
    worker.join(3)
    assert raised.get("yes") and not store.pending()
    assert events[-1]["kind"] == "prompt_cancelled"

