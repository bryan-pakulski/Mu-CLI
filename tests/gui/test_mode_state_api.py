"""Mode routers on the shared shell: each serves the requested session's own records, user actions
go through the engines with the surface as the actor (modes v2 §5.5)."""

import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mu.gui.app import session_by_name
from mu.gui.routers import debug as debug_router
from mu.gui.routers import loop as loop_router
from mu.gui.routers import mode_state as mode_state_router
from mu.gui.routers import teacher as teacher_router
from mu.loop.engine import start_mission
from mu.modes.evidence import CheckSpec, run_check

from mode_helpers import agent_session, call


def _client(sessions):
    app = FastAPI()
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
