"""Background session states: the bus records how each session's last turn ended and the session
list reports it for loaded sessions (the sidebar shows running / needs you / error / paused / done)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from mu.gui.bus import EventBus


def _publish(bus, *events):
    async def run():
        for event in events:
            await bus.publish(event)
    asyncio.run(run())


def test_the_bus_records_each_sessions_last_outcome():
    bus = EventBus()
    _publish(bus,
             {"kind": "turn_complete", "session_name": "a", "result": {"status": "completed"}},
             {"kind": "error", "session_name": "b", "text": "provider timeout"},
             {"kind": "turn_complete", "session_name": "b", "result": {"status": "completed"}},  # its own turn_complete
             {"kind": "turn_complete", "session_name": "c", "result": {"status": "interrupted"}},
             {"kind": "assistant_delta", "session_name": "d", "text": "x"})
    assert bus.outcomes["a"]["status"] == "completed"
    assert bus.outcomes["b"]["status"] == "error" and bus.outcomes["b"]["error"] == "provider timeout"
    assert bus.outcomes["c"]["status"] == "interrupted" and "d" not in bus.outcomes


def test_the_session_list_reports_outcomes_for_loaded_sessions(monkeypatch):
    from mu.gui.routers import sessions
    bus = EventBus()
    _publish(bus, {"kind": "error", "session_name": "loaded-one", "text": "boom"},
             {"kind": "error", "session_name": "gone", "text": "boom"})
    state = SimpleNamespace(current_session_name=None, sessions={"loaded-one": object()}, bus=bus)
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    monkeypatch.setattr(sessions, "_busy_session_names", lambda request: set())
    monkeypatch.setattr(sessions, "_session_dirs", lambda: [])
    data = asyncio.run(sessions.list_sessions(request))
    assert set(data["outcomes"]) == {"loaded-one"} and data["outcomes"]["loaded-one"]["status"] == "error"


def test_the_sidebar_renders_one_state_per_session():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    index = (root / "mu/gui/templates/index.html").read_text()
    assert '$store.sessions.state(s.name)' in index and "w-session__state" in index
    app = (root / "mu/gui/static/js/app.js").read_text()
    for key in ('"running"', '"waiting"', '"error"', '"paused"', '"done"', '"loaded"'):
        assert f"make({key}" in app or f"key: {key}" in app, key
