"""Mode routers on the shared shell: each serves the requested session's own records, user actions
go through the engines with the surface as the actor (modes v2 §5.5)."""

import threading
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mu.gui.app import session_by_name
from mu.gui.routers import debug as debug_router
from mu.gui.routers import loop as loop_router
from mu.gui.routers import mode_state as mode_state_router
from mu.gui.routers import modes as modes_router
from mu.gui.routers import teacher as teacher_router
from mu.loop.engine import start_mission
from mu.loop.state import loop_state
from mu.modes.evidence import CheckSpec, run_check

from mode_helpers import agent_session, call


class _Bus:
    def __init__(self):
        self.events = []

    async def publish(self, event):
        self.events.append(event)


def _client(sessions):
    app = FastAPI()
    app.state.bus = _Bus()
    app.state.session_busy = {name: threading.Event() for name in sessions}
    app.state.session_busy_for = lambda name=None: app.state.session_busy[name or next(iter(sessions))]
    app.state.sessions = dict(sessions)
    app.state.session_locks = {name: threading.Lock() for name in sessions}
    app.state.current_session_name = next(iter(sessions))
    app.state._fallback_lock = threading.Lock()
    app.state.session_by_name = lambda name=None: session_by_name(app, name)
    app.state.session_lock_for = lambda name=None: app.state.session_locks.get(name) or app.state._fallback_lock
    app.include_router(debug_router.router, prefix="/api/debug")
    app.include_router(loop_router.router, prefix="/api/loop")
    app.include_router(teacher_router.router, prefix="/api/teacher")
    app.include_router(mode_state_router.router, prefix="/api/mode-state")
    app.include_router(modes_router.router, prefix="/api/modes")
    return TestClient(app)


def test_debug_state_is_per_session_and_active_only_in_debug_mode(tmp_path):
    alpha = agent_session(tmp_path / "a", mode="debug")
    beta = agent_session(tmp_path / "b", mode="default")
    call(alpha, "open_debug_case", title="alpha bug", symptom="boom")
    call(alpha, "raise_blocker", summary="need logs", requested_input="the log")
    run_check(CheckSpec(command="true", ref="d1", label="probe"), alpha)
    client = _client({"alpha": alpha, "beta": beta})
    a = client.get("/api/debug/state", params={"session_name": "alpha"}).json()
    assert a["active"] and a["case"]["title"] == "alpha bug"
    assert [r["id"] for r in a["runs"]] == ["r1"] and a["runs"][0]["predicate_text"] == "exit 0"
    assert [b["summary"] for b in a["blockers"]] == ["need logs"]
    b = client.get("/api/debug/state", params={"session_name": "beta"}).json()
    assert not b["active"] and b["case"] is None and b["blockers"] == []


def test_check_run_and_full_output_routes(tmp_path):
    session = agent_session(tmp_path)
    run = run_check(CheckSpec(command="echo hello-output"), session)
    client = _client({"s": session})
    assert client.get(f"/api/mode-state/checks/{run.id}", params={"session_name": "s"}).json()["passed"]
    out = client.get(f"/api/mode-state/checks/{run.id}/output", params={"session_name": "s"}).json()
    assert "hello-output" in out["output"]
    assert client.get("/api/mode-state/checks/r99", params={"session_name": "s"}).status_code == 404


def test_blocker_resolution_records_the_surface(tmp_path):
    session = agent_session(tmp_path, mode="loop")
    call(session, "raise_blocker", summary="key?", requested_input="a key")
    client = _client({"s": session})
    blocker = client.get("/api/mode-state/blockers", params={"session_name": "s"}).json()["open"][0]
    web = client.post(f"/api/mode-state/blockers/{blocker['id']}/resolve", params={"session_name": "s"},
                      json={"resolution": "use vault"}).json()
    assert web["blocker"]["resolved_by"] == "user:gui"
    call(session, "raise_blocker", summary="again?", requested_input="x")
    other = client.get("/api/mode-state/blockers", params={"session_name": "s"}).json()["open"][0]
    mobile = client.post(f"/api/mode-state/blockers/{other['id']}/resolve", params={"session_name": "s"},
                         headers={"Authorization": "Bearer tok"}, json={"resolution": "ok"}).json()
    assert mobile["blocker"]["resolved_by"] == "user:mobile"


def test_loop_controls_act_on_the_mission(tmp_path):
    session = agent_session(tmp_path, mode="loop")
    client = _client({"s": session})
    started = client.post("/api/loop/start", params={"session_name": "s"},
                          json={"goal": "ship", "budget": {"max_cost_usd": 5}}).json()
    assert started["mission"]["status"] == "running" and started["mission"]["goal_history"][0]["actor"] == "user:gui"
    paused = client.post("/api/loop/control", params={"session_name": "s"}, json={"action": "pause"}).json()
    assert paused["mission"]["status"] == "paused"
    # Earlier clients send {active: bool}.
    resumed = client.post("/api/loop/control", params={"session_name": "s"}, json={"active": True}).json()
    assert resumed["mission"]["status"] == "running"
    assert resumed["budget_meter"]["kind"] == "cost_usd"


def _wait_idle(app, name, session, turns):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        done = [e for e in app.state.bus.events if e["kind"] == "turn_complete"]
        if len(done) >= turns and not app.state.session_busy[name].is_set():
            return
        time.sleep(0.05)
    raise AssertionError("the turn never finished")


def test_starting_or_resuming_a_mission_from_the_panel_starts_work(tmp_path):
    session = agent_session(tmp_path, mode="loop", name="s",
                            steps=[[("raise_blocker", {"summary": "which GPU?", "requested_input": "id"})],
                                   [("raise_blocker", {"summary": "still which GPU?", "requested_input": "id"})]])
    with _client({"s": session}) as client:
        client.post("/api/loop/start", params={"session_name": "s"}, json={"goal": "profile the GPU path"})
        _wait_idle(client.app, "s", session, 1)
        started = [e for e in client.app.state.bus.events if e["kind"] == "turn_started"]
        assert [e["text"] for e in started] == ["profile the GPU path"]
        users = [m for m in session.session_manager.history if m.get("role") == "user" and not m.get("synthetic")]
        assert "profile the GPU path" in str(users[0]["parts"])

        client.post("/api/loop/control", params={"session_name": "s"}, json={"action": "pause"})
        resumed = client.post("/api/loop/control", params={"session_name": "s"}, json={"action": "resume"}).json()
        assert resumed["mission"]["status"] == "running"
        _wait_idle(client.app, "s", session, 2)
        started = [e for e in client.app.state.bus.events if e["kind"] == "turn_started"]
        assert [e["text"] for e in started] == ["profile the GPU path", loop_router.RESUME_TEXT]


def test_answering_a_blocker_that_holds_the_mission_resumes_it_with_the_answer(tmp_path):
    session = agent_session(tmp_path, mode="loop", name="s", steps=["working", "back on it"])
    with _client({"s": session}) as client:
        client.post("/api/loop/start", params={"session_name": "s"}, json={"goal": "profile the GPU path"})
        _wait_idle(client.app, "s", session, 1)
        call(session, "raise_blocker", summary="fp16?", requested_input="May inputs be fp16?")
        assert loop_state(session).mission.status == "blocked"
        blocker = client.get("/api/mode-state/blockers", params={"session_name": "s"}).json()["open"][0]
        out = client.post(f"/api/mode-state/blockers/{blocker['id']}/resolve", params={"session_name": "s"},
                          json={"resolution": "yes, fp16 is fine"}).json()
        assert out["resumed"] and loop_state(session).mission.status == "running"
        _wait_idle(client.app, "s", session, 2)
        started = [e["text"] for e in client.app.state.bus.events if e["kind"] == "turn_started"]
        assert started == ["profile the GPU path", "yes, fp16 is fine"]


def test_answering_a_blocker_outside_a_mission_starts_no_turn(tmp_path):
    session = agent_session(tmp_path, mode="default", name="s")
    with _client({"s": session}) as client:
        call(session, "raise_blocker", summary="key?", requested_input="a key")
        blocker = client.get("/api/mode-state/blockers", params={"session_name": "s"}).json()["open"][0]
        out = client.post(f"/api/mode-state/blockers/{blocker['id']}/resolve", params={"session_name": "s"},
                          json={"resolution": "here"}).json()
        assert not out["resumed"]
        assert not [e for e in client.app.state.bus.events if e["kind"] == "turn_started"]


def test_the_panel_does_not_start_a_second_turn_while_one_runs(tmp_path):
    session = agent_session(tmp_path, mode="loop", name="s")
    with _client({"s": session}) as client:
        client.app.state.session_busy["s"].set()
        started = client.post("/api/loop/start", params={"session_name": "s"}, json={"goal": "ship"}).json()
        assert started["mission"]["status"] == "running"
        assert not [e for e in client.app.state.bus.events if e["kind"] == "turn_started"]
        assert session.provider.calls == []


def test_panel_edits_during_a_running_turn_neither_wait_nor_stall_the_server(tmp_path):
    # A turn holds the session lock for its whole run. Pausing must still work
    # (the loop body stops at the next boundary) and the event loop must stay free.
    session = agent_session(tmp_path, mode="loop", name="s")
    start_mission(session, "long work", actor="user:cli")
    with _client({"s": session}) as client:
        client.app.state.session_busy["s"].set()
        client.app.state.session_locks["s"].acquire()
        try:
            began = time.monotonic()
            paused = client.post("/api/loop/control", params={"session_name": "s"}, json={"action": "pause"})
            item = client.post("/api/loop/backlog", params={"session_name": "s"}, json={"content": "profile"})
            assert time.monotonic() - began < 5
            assert paused.json()["mission"]["status"] == "paused"
            assert item.json()["backlog"][0]["title"] == "profile"
            switched = client.post("/api/modes/debug", params={"session_name": "s"})
            assert switched.status_code == 200 and session.variables["agent_mode"] == "debug"
            assert time.monotonic() - began < 5
            session.variables["agent_mode"] = "loop"
        finally:
            client.app.state.session_locks["s"].release()
            client.app.state.session_busy["s"].clear()
        # The edit lives in the turn's in-memory state; the turn's save persists it.
        session.session_manager.save_history(session.folder_context)
        from mu.session.manager import SessionManager

        assert loop_state(SessionManager(session_name="s")).mission.status == "paused"


def test_teacher_approve_is_the_learners_and_reported_in_the_payload(tmp_path):
    session = agent_session(tmp_path, mode="teacher")
    call(session, "create_course", subject="Go")
    call(session, "record_diagnostic", background=["Python"])
    call(session, "propose_curriculum", modules=[{"module_id": "m1", "title": "Basics", "lessons": [
        {"lesson_id": "l1", "title": "Types", "learning_objectives": ["declare a type"]}]}])
    client = _client({"s": session})
    before = client.get("/api/teacher/state", params={"session_name": "s"}).json()
    assert before["course"]["status"] == "curriculum_proposed"
    assert before["course"]["lessons"][0]["gate"]["required"] == 2
    after = client.post("/api/teacher/approve", params={"session_name": "s"}).json()
    assert after["course"]["curriculum_approval"]["actor"] == "user:gui"
    assert client.post("/api/teacher/approve", params={"session_name": "s"}).status_code == 409


def test_turn_receipts_are_served_for_a_reload(tmp_path):
    ws = tmp_path / "ws"
    session = agent_session(tmp_path, steps=[[("write_file", {"filename": str(ws / "x.py"), "content": "x=1\n"})],
                                             "done", "done"])
    session.send_message("edit x.py")
    client = _client({"s": session})
    receipts = client.get("/api/mode-state/receipts", params={"session_name": "s"}).json()["receipts"]
    assert receipts[-1]["status"] == "unverified" and receipts[-1]["files"] == ["x.py"]


def test_leaving_loop_mode_from_a_surface_pauses_the_mission(tmp_path):
    session = agent_session(tmp_path, mode="loop", name="s")
    start_mission(session, "profile the GPU path", actor="user:gui")
    client = _client({"s": session})
    client.post("/api/modes/default", params={"session_name": "s"}, headers={"x-mucli-client": "mobile"})
    state = client.get("/api/loop/state", params={"session_name": "s"}).json()
    assert state["mission"]["status"] == "paused" and not state["active"]
    assert loop_state(session).events[-1].actor == "user:mobile"


def test_the_user_unblocks_finishes_and_drops_work_items(tmp_path):
    from mu.modes.work import work_state

    session = agent_session(tmp_path, mode="loop", name="s")
    work = work_state(session)
    for title in ("measure fusion", "write report", "old idea"):
        work.add(title, actor="agent")
    work.set_status("w1", "blocked", actor="agent")
    client = _client({"s": session})
    post = lambda item, body: client.post(f"/api/mode-state/work/{item}", params={"session_name": "s"}, json=body)

    unblocked = post("w1", {"status": "pending"})
    assert unblocked.status_code == 200 and work.get("w1").status == "pending"
    # Loop mode: done needs a passing run or a recorded reason.
    assert post("w2", {"status": "done"}).status_code == 409
    run = run_check(CheckSpec(command="true", label="report exists"), session)
    assert post("w1", {"status": "done", "evidence": [run.id]}).status_code == 200
    assert post("w2", {"status": "done", "override_reason": "report reviewed by hand"}).status_code == 200
    assert post("w3", {"status": "dropped"}).status_code == 200
    assert post("w9", {"status": "done"}).status_code == 404
    items = {i["id"]: i for i in client.get("/api/mode-state/work", params={"session_name": "s"}).json()["items"]}
    assert [items[k]["status"] for k in ("w1", "w2", "w3")] == ["done", "done", "dropped"]
    assert items["w1"]["evidence"] == [run.id]
    assert [(e.kind, e.actor) for e in work.events if e.ref == "w2" and e.kind == "override"] == [("override", "user:gui")]
