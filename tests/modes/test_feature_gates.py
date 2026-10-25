"""Feature plans: draft → user approval → evidence-backed tasks → review → user completion,
with every surface going through the engine (modes v2 §4.3, F1–F4)."""

import subprocess

import pytest

from mu.feature import engine
from mu.modes.events import GateError
from mu.session.manager import SessionManager

from mode_helpers import agent_session, call, history_texts

PHASES = [{"title": "Core", "tasks": [
    {"title": "Add greet", "objective": "greet() returns hello",
     "exit_criteria": [{"text": "greet test passes", "check": "python3 -c 'import app; assert app.greet()==\"hello\"'"}]},
    {"title": "Document greet", "exit_criteria": ["README mentions greet"]},
]}]


def _session(tmp_path, name="", git=True):
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "app.py").write_text("def greet():\n    return 'hi'\n")
    if git:
        for args in (["init", "-q"], ["add", "."], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"]):
            subprocess.run(["git", *args], cwd=ws, check=True, capture_output=True)
    return agent_session(tmp_path, mode="feature", workspace=ws, name=name)


def _propose(session):
    result = call(session, "propose_feature_plan", name="Greeting", goal="Add a greeting", phases=PHASES)
    assert result["ok"], result["message"]
    return result


def _gui(session):
    import threading

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from mu.gui.routers import feature as feature_router

    app = FastAPI()
    app.state.session_by_name = lambda name=None: session
    app.state.session_lock_for = lambda name=None: threading.Lock()
    app.include_router(feature_router.router, prefix="/api/feature")
    return TestClient(app)


def test_the_model_cannot_approve_its_own_plan(tmp_path):
    session = _session(tmp_path)
    _propose(session)
    assert session.provider is not None
    from mu.tools.descriptors import TOOLS
    names = {t.name for t in TOOLS}
    assert not names & {"approve_feature_task", "decide_task_diff", "approve_feature_plan", "complete_feature"}
    with pytest.raises(GateError) as err:
        engine.approve(session, actor="agent")
    assert err.value.code == "user_only"
    refused = call(session, "start_task", task_id=1)
    assert not refused["ok"] and refused["error_code"] == "not_approved"


def test_approval_records_actor_and_hash_and_revisions_need_reapproval(tmp_path):
    session = _session(tmp_path)
    _propose(session)
    plan = engine.approve(session, actor="user:gui")
    assert plan.approval["actor"] == "user:gui" and plan.approval["plan_hash"] == engine.plan_hash(plan)
    call(session, "start_task", task_id=1)
    call(session, "check_criterion", task_id=1, criterion="c1")  # fails: greet returns hi
    revised = call(session, "revise_feature_plan", update_tasks=[{"id": 2, "title": "Document greet()",
                                                                  "exit_criteria": ["README mentions greet()"]}])
    assert revised["ok"] and revised["data"]["needs_approval"]
    plan = engine.require_plan(session)
    assert plan.status == "draft" and plan.revision == 2
    # The unchanged task keeps its progress; only the changed one reset.
    assert plan.task(1).status == "in_progress" and plan.task(2).status == "not_started"
    assert not call(session, "complete_task", task_id=1)["ok"]


def test_a_task_completes_only_with_evidence_and_one_runs_at_a_time(tmp_path):
    session = _session(tmp_path)
    _propose(session)
    engine.approve(session, actor="user:cli")
    assert call(session, "start_task", task_id=1)["ok"]
    two = call(session, "start_task", task_id=2)
    assert not two["ok"] and two["error_code"] == "task_in_progress"
    refused = call(session, "complete_task", task_id=1)
    assert not refused["ok"] and refused["error_code"] == "criteria_unmet"
    failed = call(session, "check_criterion", task_id=1, criterion="c1")
    assert not failed["ok"] and failed["error_code"] == "check_failed"
    (tmp_path / "ws" / "app.py").write_text("def greet():\n    return 'hello'\n")
    assert call(session, "check_criterion", task_id=1, criterion="c1")["ok"]
    done = call(session, "complete_task", task_id=1)
    assert done["ok"] and "app.py" in done["data"]["diff_stat"]


def test_gui_transition_cannot_complete_without_evidence_but_a_user_override_is_recorded(tmp_path):
    session = _session(tmp_path)
    _propose(session)
    client = _gui(session)
    assert client.post("/api/feature/approve").status_code == 200
    assert client.post("/api/feature/tasks/1/transition", json={"to_status": "in_progress"}).status_code == 200
    blocked = client.post("/api/feature/tasks/1/transition", json={"to_status": "completed"})
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "override_reason_required"
    ok = client.post("/api/feature/tasks/1/complete", json={"override_reason": "verified manually"})
    assert ok.status_code == 200
    task = ok.json()["plan"]["tasks"][0]
    assert task["status"] == "completed" and task["override"]["actor"] == "user:gui"
    events = [e for e in ok.json()["plan"]["event_log"] if e["kind"] == "override"]
    assert events and events[0]["actor"] == "user:gui"


def test_command_move_routes_through_the_engine(tmp_path):
    session = _session(tmp_path)
    _propose(session)
    from mu.commands import dispatch
    assert not dispatch(session, "/feature move 1 in_progress", allow_prompt=False).ok  # not approved
    assert dispatch(session, "/feature approve", allow_prompt=False).ok
    assert dispatch(session, "/feature move 1 in_progress", allow_prompt=False).ok
    assert not dispatch(session, "/feature move 1 completed", allow_prompt=False).ok


def test_review_needs_a_passing_check_after_the_last_edit_and_completion_needs_a_suite(tmp_path):
    session = _session(tmp_path)
    _propose(session)
    engine.approve(session, actor="user:cli")
    ws = tmp_path / "ws"
    for task_id in (1, 2):
        call(session, "start_task", task_id=task_id)
        if task_id == 1:
            session._execute_tool_with_memory("write_file", {"filename": str(ws / "app.py"),
                                                             "content": "def greet():\n    return 'hello'\n"})
            assert call(session, "check_criterion", task_id=1, criterion="c1")["ok"]
        else:
            session._execute_tool_with_memory("write_file", {"filename": str(ws / "README.md"),
                                                             "content": "greet says hello\n"})
            assert call(session, "cite_evidence", task_id=2, criterion="c1", file="README.md",
                        justification="the README section documents greet")["ok"]
        assert call(session, "complete_task", task_id=task_id)["ok"]
    empty = call(session, "review_task", task_id=1, summary="")
    assert not empty["ok"]
    no_check = call(session, "review_task", task_id=1, summary="matches the objective")
    assert not no_check["ok"] and no_check["error_code"] == "evidence_required"
    from mu.modes.evidence import CheckSpec, run_check
    run_check(CheckSpec(command="python3 -c 'import app'"), session)
    assert call(session, "review_task", task_id=1, summary="greet returns hello")["ok"]
    assert call(session, "review_task", task_id=2, summary="README updated",
                issues=[{"title": "add an example", "severity": "follow_up"}])["ok"]
    plan = engine.require_plan(session)
    followup = plan.tasks[-1]
    assert followup.origin == "review" and plan.approval is not None  # follow-ups don't need re-approval
    engine.archive_task(session, followup.id, actor="user:gui")
    not_ready = call(session, "request_feature_completion")
    assert not not_ready["ok"] and any("suite" in g for g in not_ready["data"]["gaps"])
    with pytest.raises(GateError):
        engine.complete_feature(session, actor="user:gui")
    assert call(session, "run_feature_suite", command="python3 -c 'import app'")["ok"]
    assert call(session, "request_feature_completion")["ok"]
    with pytest.raises(GateError):
        engine.complete_feature(session, actor="agent")
    done = engine.complete_feature(session, actor="user:gui")
    assert done.status == "done" and session.session_manager.active_feature_id is None


def test_edits_before_approval_are_warned_once_or_blocked(tmp_path):
    session = _session(tmp_path)
    ws = tmp_path / "ws"
    session.provider.push(
        [("propose_feature_plan", {"name": "Greeting", "goal": "Add a greeting", "phases": PHASES})],
        [("write_file", {"filename": str(ws / "a.txt"), "content": "1"})],
        [("write_file", {"filename": str(ws / "a.txt"), "content": "2"})],
        "waiting for approval")
    session.send_message("add a greeting feature")
    assert (ws / "a.txt").read_text() == "2"
    assert any("feature_not_approved" in str(m) for m in session.session_manager.history)
    session.variables["feature_require_approval"] = True
    session.provider.push([("write_file", {"filename": str(ws / "b.txt"), "content": "1"})],
                          [("write_file", {"filename": str(ws / "b.txt"), "content": "2"})], "ok")
    session.send_message("go ahead and edit")
    assert not (ws / "b.txt").exists()


def test_history_holds_only_the_users_text_and_the_capsule_rides_the_request(tmp_path):
    session = _session(tmp_path)
    _propose(session)
    session.provider.push("Plan proposed; please approve.")
    session.send_message("what is the plan status?")
    user_texts = [m["parts"][0]["text"] for m in session.session_manager.history
                  if m.get("role") == "user" and not m.get("synthetic")]
    assert user_texts == ["what is the plan status?"]
    assert not any("MODE STATE" in t for t in history_texts(session))
    request = [p.text for m in session.provider.calls[-1]["messages"] for p in m.parts if getattr(p, "text", None)]
    assert any("MODE STATE — feature" in t and "approve" in t for t in request)


def test_l3_shows_the_next_task(tmp_path):
    session = _session(tmp_path)
    _propose(session)
    assert "- next_task: t1 Add greet" in session._build_active_goal_context(include_goal=False)


def test_plan_files_survive_reload_and_old_plans_still_load(tmp_path):
    session = _session(tmp_path, name="feat-reload")
    _propose(session)
    reloaded = SessionManager(session_name="feat-reload")
    plan = engine.read_feature_plan(reloaded.feature_state["metadata_path"])
    assert plan.task(1).criteria[0].check["command"].startswith("python3")
    legacy = tmp_path / "legacy.json"
    import json
    legacy.write_text(json.dumps({
        "feature_id": "old", "feature_name": "Old", "feature_request": "x", "directory": str(tmp_path),
        "approved": True, "review_status": "pending", "unknown_key": 1,
        "tasks": [{"id": 1, "title": "t", "status": "completed", "exit_criteria": ["a", "b"],
                   "verified_exit_criteria": ["a"], "objectives": ["o"]}]}))
    old = engine.load_feature_plan(str(legacy))
    assert old.approval["actor"] == "legacy" and old.status == "executing"
    assert [c.met for c in old.task(1).criteria] == [True, False]
