"""Mode state is typed, session-owned and persisted with the session (modes v2 §3.1)."""

import threading

from mu.modes.state import ModeStates, current_mode_states
from mu.modes.work import work_state
from mu.session.manager import SessionManager

from mode_helpers import agent_session, call


def test_mode_state_survives_a_session_reload(tmp_path):
    session = agent_session(tmp_path, name="persist-me")
    assert call(session, "todo_write", content="write the parser")["ok"]
    assert call(session, "raise_blocker", summary="need creds", requested_input="a token")["ok"]

    reloaded = SessionManager(session_name="persist-me")
    items = work_state(reloaded).list()
    assert [i.title for i in items] == ["write the parser"]
    blockers = reloaded.mode_state.get("blockers").open_blockers()
    assert [b.summary for b in blockers] == ["need creds"]


def test_unknown_state_names_round_trip_untouched():
    states = ModeStates()
    states.load_dict({"from_the_future": {"x": 1}, "work": {"items": []}})
    states.get("work")
    assert states.to_dict()["from_the_future"] == {"x": 1}


def test_corrupt_state_loads_empty_instead_of_failing():
    states = ModeStates()
    states.load_dict({"work": {"items": "not-a-list", "counter": "zz"}})
    assert states.get("work").list() == []


def test_concurrent_sessions_keep_separate_states(tmp_path):
    alpha = agent_session(tmp_path / "a")
    beta = agent_session(tmp_path / "b")
    barrier = threading.Barrier(2)

    def run(session, title):
        barrier.wait(timeout=5)
        for n in range(5):
            call(session, "todo_write", content=f"{title} {n}")

    threads = [threading.Thread(target=run, args=(alpha, "alpha")),
               threading.Thread(target=run, args=(beta, "beta"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert {i.title.split()[0] for i in work_state(alpha).list()} == {"alpha"}
    assert {i.title.split()[0] for i in work_state(beta).list()} == {"beta"}
    # Nothing leaked into the process-wide fallback.
    assert not current_mode_states().get("work").list()
