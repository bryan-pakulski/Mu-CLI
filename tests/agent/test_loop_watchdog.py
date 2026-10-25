"""Loop watchdog and the core raise_blocker tool (modes v2 L1, §3.4)."""

from mu.loop.engine import start_mission
from mu.loop.state import loop_state
from mu.modes.blockers import blocker_state

from mode_helpers import agent_session


def _with_mission(session, goal="keep the service healthy"):
    start_mission(session, goal, actor="user:cli")
    return session


def _watchdog_nudges(session):
    return [m for m in session.session_manager.history
            if m.get("synthetic") and "LOOP WATCHDOG" in str(m.get("parts"))]


def test_raise_blocker_is_offered_in_every_mode(tmp_path):
    for mode in ("default", "debug", "loop", "feature", "security", "teacher", "research"):
        session = agent_session(tmp_path / mode, mode=mode, steps=["hi"])
        session.send_message("hello there agent")
        assert "raise_blocker" in session.provider.calls[0]["tools"], mode


def test_a_raised_blocker_stops_the_watchdog_and_the_reply_resolves_it(tmp_path):
    session = _with_mission(agent_session(tmp_path, mode="loop", steps=[
        [("raise_blocker", {"summary": "need a deploy key", "requested_input": "the key"})],
        "I need the deploy key to continue.",
    ]))
    session.send_message("keep the service deployed")
    assert not _watchdog_nudges(session)
    assert loop_state(session).mission.status == "blocked"
    blocker = blocker_state(session).open_blockers()[0]
    assert blocker.summary == "need a deploy key" and blocker.mode == "loop"

    session.provider.push("thanks, continuing")
    session.send_message("here is the key: use the vault entry")
    resolved = blocker_state(session).get(blocker.id)
    assert not resolved.open and resolved.resolved_by == "user:chat"
    assert "vault entry" in resolved.resolution
    assert loop_state(session).mission.status == "running"


def test_watchdog_budget_resets_each_turn(tmp_path):
    # Each turn: the model stops without tools 9 times; the watchdog nudges at most 8.
    session = _with_mission(agent_session(tmp_path, mode="loop", steps=["idle"] * 9))
    session.variables["max_iterations"] = 50
    session.send_message("first long mission turn")
    first = len(_watchdog_nudges(session))
    assert first == 8
    session.provider.push(*(["idle"] * 9))
    session.send_message("second long mission turn")
    assert len(_watchdog_nudges(session)) == first + 8
