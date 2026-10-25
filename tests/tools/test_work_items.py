"""Work items replace scratchpad todos (modes v2 §3.3)."""

from mu.modes.evidence import CheckSpec, run_check
from mu.modes.work import work_state

from mode_helpers import agent_session, call


def test_one_item_in_progress_starting_another_pauses_the_first(tmp_path):
    session = agent_session(tmp_path)
    call(session, "todo_write", content="first", status="in_progress")
    second = call(session, "todo_write", content="second")["data"]["id"]
    assert call(session, "todo_set_status", id=second, status="in_progress")["ok"]
    statuses = {i.title: i.status for i in work_state(session).list()}
    assert statuses == {"first": "pending", "second": "in_progress"}


def test_items_are_never_evicted_and_survive_the_scratchpad_wipe(tmp_path):
    session = agent_session(tmp_path, steps=["ok", "ok"])
    for n in range(80):
        call(session, "todo_write", content=f"task {n}")
    session.send_message("next turn please")
    assert len(work_state(session).list()) == 80
    listed = call(session, "todo_list")
    assert listed["data"]["count"] == 80


def test_completed_is_an_alias_of_done_and_evidence_must_have_passed(tmp_path):
    session = agent_session(tmp_path)
    item = call(session, "todo_write", content="ship it")["data"]["id"]
    failing = run_check(CheckSpec(command="exit 1"), session)
    refused = call(session, "todo_set_status", id=item, status="completed", evidence=[failing.id])
    assert not refused["ok"] and refused["error_code"] == "evidence_failed"
    passing = run_check(CheckSpec(command="true"), session)
    done = call(session, "todo_set_status", id=item, status="completed", evidence=[passing.id])
    assert done["ok"] and done["data"]["status"] == "done"
    assert done["data"]["evidence"] == [passing.id]


def test_loop_mode_requires_evidence_for_done(tmp_path):
    session = agent_session(tmp_path, mode="loop")
    item = call(session, "todo_write", content="increment")["data"]["id"]
    refused = call(session, "todo_set_status", id=item, status="done")
    assert not refused["ok"] and refused["error_code"] == "evidence_required"
    run = run_check(CheckSpec(command="true"), session)
    assert call(session, "todo_set_status", id=item, status="done", evidence=[run.id])["ok"]


def test_a_user_override_is_recorded_with_its_reason(tmp_path):
    session = agent_session(tmp_path, mode="loop")
    state = work_state(session)
    item = state.add("needs a human call")
    state.set_status(item.id, "done", actor="user:gui", require_evidence=True,
                     override_reason="verified by hand on staging")
    overrides = [e for e in state.events if e.kind == "override"]
    assert overrides and overrides[0].actor == "user:gui"
    assert overrides[0].detail == "verified by hand on staging"
