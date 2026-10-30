"""Loop missions: user-owned goal, increments with evidence, real pause, budgets, done-when
(modes v2 §4.4, L2)."""

from mu.loop import engine
from mu.loop.state import loop_state
from mu.modes.evidence import CheckSpec, run_check
from mu.modes.events import GateError
from mu.session.manager import SessionManager

from mode_helpers import agent_session, call, history_texts


def _nudges(session):
    return [m for m in session.session_manager.history
            if m.get("synthetic") and "LOOP WATCHDOG" in str(m.get("parts"))]


def test_the_agent_cannot_start_a_mission_or_change_its_goal(tmp_path):
    session = agent_session(tmp_path, mode="loop")
    try:
        engine.start_mission(session, "do things", actor="agent")
        raise AssertionError("agent started a mission")
    except GateError as exc:
        assert exc.code == "user_only"
    engine.start_mission(session, "ship v2", actor="user:cli")
    try:
        engine.set_goal(session, "something else", actor="agent")
        raise AssertionError("agent changed the goal")
    except GateError:
        pass
    assert loop_state(session).mission.goal_history[0]["actor"] == "user:cli"


def test_a_message_in_loop_mode_starts_the_mission_with_it_as_the_goal(tmp_path):
    session = agent_session(tmp_path, mode="loop", steps=[
        [("raise_blocker", {"summary": "which suite?", "requested_input": "a path"})]])
    session.send_message("make the integration suite green")
    mission = loop_state(session).mission
    assert mission.goal == "make the integration suite green" and mission.status == "blocked"
    assert mission.goal_history[0]["actor"] == "user:chat"
    capsule_turn = "\n".join(t for t in [p.text for m in session.provider.calls[0]["messages"]
                                         for p in m.parts if getattr(p, "text", None)])
    assert "make the integration suite green" in capsule_turn and "No loop mission" not in capsule_turn


def test_a_message_does_not_replace_a_live_mission(tmp_path):
    session = agent_session(tmp_path, mode="loop", steps=["paused, answering"])
    engine.start_mission(session, "long work", actor="user:cli")
    engine.set_status(session, "paused", actor="user:gui")
    session.send_message("how far along are we?")
    mission = loop_state(session).mission
    assert mission.goal == "long work" and mission.status == "paused"
    assert len(loop_state(session).past) == 0


def test_a_slash_command_in_loop_mode_starts_no_mission_and_nothing_continues(tmp_path):
    session = agent_session(tmp_path, mode="loop", steps=["What should the mission be?"])
    session.send_message("/status please")
    assert loop_state(session).mission is None
    assert not _nudges(session)


def test_increments_need_evidence_and_persist_across_reload(tmp_path):
    session = agent_session(tmp_path, mode="loop", name="loop-reload")
    engine.start_mission(session, "make the suite green", actor="user:cli")
    item = call(session, "todo_write", content="fix flaky test")["data"]["ref"]
    refused = call(session, "loop_increment", objective="fixed it", result="passed")
    assert not refused["ok"] and refused["error_code"] == "evidence_required"
    run = run_check(CheckSpec(command="true"), session)
    ok = call(session, "loop_increment", objective="fix flaky test", result="passed",
              evidence=[run.id], work_item=item, next="wire metrics")
    assert ok["ok"]
    failed = call(session, "loop_increment", objective="add metrics", result="failed")
    assert failed["ok"] and failed["data"]["remediation_item"]
    reloaded = SessionManager(session_name="loop-reload")
    mission = loop_state(reloaded).mission
    assert [i.result for i in mission.increments] == ["passed", "failed"]
    assert mission.increments[0].evidence == [run.id] and mission.increments[0].next == "wire metrics"


def test_a_paused_mission_does_not_continue(tmp_path):
    session = agent_session(tmp_path, mode="loop", steps=["idle", "idle", "idle"])
    engine.start_mission(session, "long work", actor="user:cli")
    engine.set_status(session, "paused", actor="user:gui")
    session.send_message("status?")
    assert not _nudges(session)
    assert len(session.provider.calls) == 1


def test_pausing_mid_turn_stops_at_the_next_iteration_boundary(tmp_path):
    session = agent_session(tmp_path, mode="loop")
    engine.start_mission(session, "long work", actor="user:cli")

    def pause_then_list(*_a, **_k):
        engine.set_status(session, "paused", actor="user:gui")
        return {"ok": True, "message": "listed", "data": {}}

    session.provider.push([("todo_list", {})], [("todo_list", {})], "never reached")
    from mu.tools import _dispatcher
    original = _dispatcher.TOOL_HANDLERS.get("todo_list")
    _dispatcher.TOOL_HANDLERS["todo_list"] = lambda args, ctx: pause_then_list()
    try:
        result = session.send_message("keep going")
    finally:
        if original is not None:
            _dispatcher.TOOL_HANDLERS["todo_list"] = original
        else:
            _dispatcher.TOOL_HANDLERS.pop("todo_list", None)
    assert result["status"] == "mission_paused"
    assert len(session.provider.calls) == 1


def test_an_exhausted_budget_pauses_the_mission(tmp_path):
    session = agent_session(tmp_path, mode="loop")
    engine.start_mission(session, "bounded", budget={"max_increments": 2}, actor="user:cli")
    for n in range(2):
        call(session, "loop_increment", objective=f"step {n}", result="partial")
    mission = loop_state(session).mission
    assert mission.status == "paused" and "increments budget" in mission.status_reason
    try:
        engine.set_status(session, "running", actor="user:gui")
        raise AssertionError("resumed past the budget")
    except GateError as exc:
        assert exc.code == "budget_exhausted"
    engine.set_budget(session, {"max_increments": 5}, actor="user:gui")
    assert engine.set_status(session, "running", actor="user:gui").status == "running"


def test_turn_budget_is_charged_per_turn(tmp_path):
    session = agent_session(tmp_path, mode="loop", steps=["a", "b"])
    engine.start_mission(session, "bounded", budget={"max_turns": 1}, actor="user:cli")
    session.send_message("go one")
    mission = loop_state(session).mission
    assert mission.spent["turns"] == 1 and mission.status == "paused"


def test_done_when_criteria_passing_finish_the_mission(tmp_path):
    session = agent_session(tmp_path, mode="loop")
    (tmp_path / "ws" / "flag").write_text("")
    engine.start_mission(session, "create the marker files", actor="user:cli", done_when=[
        {"text": "flag exists", "check": {"command": "test -f flag"}},
        {"text": "done marker exists", "check": {"command": "test -f done"}},
    ])
    first = call(session, "loop_check_done")
    assert first["data"]["status"] == "running"
    assert [c["met"] for c in first["data"]["criteria"]] == [True, False]
    (tmp_path / "ws" / "done").write_text("")
    second = call(session, "loop_check_done")
    assert second["data"]["status"] == "done"
    assert not engine.may_continue(session)


def test_history_holds_the_users_text_only(tmp_path):
    session = agent_session(tmp_path, mode="loop", steps=["ok"])
    engine.start_mission(session, "goal", actor="user:cli")
    session.send_message("please continue the work")
    user_texts = [m["parts"][0]["text"] for m in session.session_manager.history
                  if m.get("role") == "user" and not m.get("synthetic")]
    assert user_texts == ["please continue the work"]
    assert not any("MODE STATE" in t for t in history_texts(session))


def _interrupting(provider):
    original = provider.generate

    def generate(*a, **k):
        provider.generate = original
        raise KeyboardInterrupt
    provider.generate = generate


def test_stopping_a_turn_pauses_the_mission_and_the_next_message_resumes_it(tmp_path):
    session = agent_session(tmp_path, mode="loop", name="loop-interrupt")
    engine.start_mission(session, "long work", actor="user:gui")
    _interrupting(session.provider)
    assert session.send_message("keep going")["status"] == "interrupted"
    mission = loop_state(session).mission
    assert (mission.status, mission.status_reason) == ("paused", engine.INTERRUPTED)
    reloaded = SessionManager()
    reloaded._load_session("loop-interrupt")
    assert reloaded.mode_state.get("loop").mission.status == "paused"

    session.provider.push([("raise_blocker", {"summary": "which suite?", "requested_input": "a path"})])
    session.send_message("continue")
    mission = loop_state(session).mission
    assert mission.goal == "long work" and mission.status == "blocked"
    assert [(e.kind, e.actor) for e in loop_state(session).events if e.kind == "running"][-1] == ("running", "user:chat")


def test_leaving_loop_mode_pauses_the_mission_until_a_loop_message_resumes_it(tmp_path):
    from mu.commands.mode import mode_cmd

    session = agent_session(tmp_path, mode="loop", steps=["answered"])
    engine.start_mission(session, "long work", actor="user:cli")
    mode_cmd(session, "default", allow_prompt=False)
    assert (loop_state(session).mission.status, loop_state(session).mission.status_reason) == ("paused", engine.MODE_OFF)
    session.send_message("unrelated question")
    assert loop_state(session).mission.status == "paused"
    mode_cmd(session, "loop", allow_prompt=False)
    session.provider.push([("raise_blocker", {"summary": "next?", "requested_input": "a choice"})])
    session.send_message("carry on")
    assert loop_state(session).mission.status == "blocked"


def test_a_mission_left_running_outside_loop_mode_is_paused_by_the_next_turn(tmp_path):
    session = agent_session(tmp_path, mode="loop", steps=["answered"])
    engine.start_mission(session, "long work", actor="user:cli")
    session.variables["agent_mode"] = "default"
    session.send_message("something else")
    assert (loop_state(session).mission.status, loop_state(session).mission.status_reason) == ("paused", engine.MODE_OFF)


def test_a_verify_run_names_its_check_run_so_a_work_item_can_close_on_it(tmp_path):
    from mu.modes.work import work_state

    session = agent_session(tmp_path, mode="loop", steps=[
        [("bash", {"command": "true", "purpose": "verify"})],
        [("todo_set_status", {"id": 1, "status": "done", "evidence": ["r1"]})],
        [("raise_blocker", {"summary": "done?", "requested_input": "a yes"})],
    ])
    work_state(session).add("measure fusion", actor="agent")
    session.send_message("measure the fusion win")
    bash_results = [str(p.get("tool_result")) for m in session.session_manager.history for p in m.get("parts", [])
                    if p.get("tool_name") == "bash"]
    assert any("check run r1 (passed)" in r for r in bash_results)
    item = work_state(session).get("w1")
    assert item.status == "done" and item.evidence == ["r1"]
