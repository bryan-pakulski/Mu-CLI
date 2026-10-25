"""Automatic continuation: an orchestration chat resumes when a run it proposed finishes,
exactly once, without anyone typing "done"."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from mu.gui.app import _run_orch_continuation
from mu.orchestration.commands import dispatch
from mu.orchestration.continuation import RunContinuation, pending

from mode_helpers import agent_session, history_texts
from orch_helpers import Clock, FakeNode, add_node, drive, make_orch, publish_tool


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    clock = Clock()
    orch = make_orch(tmp_path, clock=clock)
    monkeypatch.setattr("mu.orchestration.agent_tools._orch", lambda: orch)
    pending(orch.store)                      # first use sets the watermark
    clock.advance(1)
    publish_tool(orch)
    node, _ = add_node(orch, "dev1", {"env": "dev"})
    yield SimpleNamespace(orch=orch, clock=clock, node=FakeNode(orch, node))
    orch.stop()


def _finish_run(fleet, actor="ai:ops-chat"):
    run = dispatch(fleet.orch, {"type": "proposeRun", "args": {
        "kind": "tool", "target": "demo.echo@1", "selector": {"labels": "env=dev"}, "title": "echo"}},
        actor=actor).data["run"]
    if run["state"] == "proposed":
        dispatch(fleet.orch, {"type": "approveRun", "args": {"run_id": run["id"]}})
    drive(fleet.orch, [fleet.node], clock=fleet.clock)
    assert fleet.orch.store.get_run(run["id"]).state == "succeeded"
    return run["id"]


def _poll(continuation):
    for worker in continuation.poll():
        worker.join(timeout=30)


class _Recorder:
    def __init__(self, result=True):
        self.calls = []
        self.result = result

    def __call__(self, name, runs):
        self.calls.append((name, [r.id for r in runs]))
        return self.result


def test_a_finished_ai_run_wakes_its_session_exactly_once(fleet):
    run_id = _finish_run(fleet)
    handler = _Recorder()
    continuation = RunContinuation(lambda: fleet.orch, handler)
    _poll(continuation)
    _poll(continuation)
    assert handler.calls == [("ops-chat", [run_id])]


def test_operator_runs_and_runs_before_first_use_wake_nothing(tmp_path, fleet):
    _finish_run(fleet, actor="operator")
    handler = _Recorder()
    _poll(RunContinuation(lambda: fleet.orch, handler))
    assert handler.calls == []

    # A store whose AI runs all finished before continuation was first used.
    clock = Clock()
    orch = make_orch(tmp_path / "old", clock=clock)
    try:
        publish_tool(orch)
        node, _ = add_node(orch, "dev1", {"env": "dev"})
        old = SimpleNamespace(orch=orch, clock=clock, node=FakeNode(orch, node))
        _finish_run(old)
        handler = _Recorder()
        _poll(RunContinuation(lambda: orch, handler))
        assert handler.calls == []
    finally:
        orch.stop()


def test_a_busy_session_is_retried_until_delivered(fleet):
    run_id = _finish_run(fleet)
    handler = _Recorder(result=False)
    continuation = RunContinuation(lambda: fleet.orch, handler)
    _poll(continuation)
    handler.result = True
    _poll(continuation)
    _poll(continuation)
    assert handler.calls == [("ops-chat", [run_id]), ("ops-chat", [run_id])]


def test_a_run_the_agent_already_read_wakes_nothing(fleet):
    import mu.tools as tools

    run_id = _finish_run(fleet)
    ctx = tools.ToolExecutionContext(folder_context=None, variables={"session_type": "orchestration",
                                                                     "session_name": "ops-chat"})
    assert tools.execute("orch_run_results", {"run_id": run_id}, ctx)["ok"]
    handler = _Recorder()
    _poll(RunContinuation(lambda: fleet.orch, handler))
    assert handler.calls == []


# ------------------------------------------------------------------ the GUI resumes the chat


def _gui(sessions):
    events = []
    busy = {name: threading.Event() for name in sessions}
    locks = {name: threading.Lock() for name in sessions}
    state = SimpleNamespace(
        sessions=dict(sessions), session_registry_lock=threading.Lock(), current_session_name=None,
        session_busy_for=lambda name: busy[name], session_lock_for=lambda name: locks[name],
        bus=SimpleNamespace(publish_threadsafe=events.append))
    return SimpleNamespace(state=state), events, busy


def _orch_session(tmp_path, name, steps, **variables):
    session = agent_session(tmp_path / name, steps=steps, name=name)
    session.variables["session_type"] = "orchestration"
    session.variables.update(variables)
    return session


def test_the_chat_resumes_reads_the_results_and_continues(tmp_path, fleet):
    run_id = _finish_run(fleet)
    session = _orch_session(tmp_path, "ops-chat", [[("orch_run_results", {"run_id": run_id})],
                                                   "dev1 echoed hi; nothing else to do."])
    app, events, _busy = _gui({"ops-chat": session})
    continuation = RunContinuation(lambda: fleet.orch, lambda n, r: _run_orch_continuation(app, n, r))
    _poll(continuation)
    texts = history_texts(session)
    assert any(run_id in t and "finished" in t for t in texts)
    assert texts[-1] == "dev1 echoed hi; nothing else to do."
    wake = next(m for m in session.session_manager.history if m.get("origin") == "orch_run_finished")
    assert wake["synthetic"]
    assert [e["kind"] for e in events] == ["orch_continuation_started", "orch_continuation_finished"]
    _poll(continuation)
    assert len(session.provider.calls) == 2


def test_the_chat_waits_while_busy_and_respects_the_switch(tmp_path, fleet):
    run_id = _finish_run(fleet)
    session = _orch_session(tmp_path, "ops-chat", ["ok"])
    app, events, busy = _gui({"ops-chat": session})
    continuation = RunContinuation(lambda: fleet.orch, lambda n, r: _run_orch_continuation(app, n, r))
    busy["ops-chat"].set()
    _poll(continuation)
    assert session.provider.calls == [] and run_id in {r.id for r in pending(fleet.orch.store)["ops-chat"]}
    busy["ops-chat"].clear()
    session.variables["orch_auto_continue"] = False
    _poll(continuation)
    assert session.provider.calls == [] and events == [] and pending(fleet.orch.store) == {}


def test_a_session_that_is_not_an_orchestration_chat_is_never_woken(tmp_path, fleet):
    _finish_run(fleet)
    session = agent_session(tmp_path / "ws", steps=["ok"], name="ops-chat")
    app, events, _busy = _gui({"ops-chat": session})
    _poll(RunContinuation(lambda: fleet.orch, lambda n, r: _run_orch_continuation(app, n, r)))
    assert session.provider.calls == [] and events == []
