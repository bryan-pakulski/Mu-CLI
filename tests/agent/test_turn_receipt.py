"""Default mode's turn receipt: edits, checks after the last edit, and the one-time verify
nudge (modes v2 §4.1, D1)."""

from mu.modes.receipt import receipt_state

from mode_helpers import agent_session


def _write(ws, name, content):
    return ("write_file", {"filename": str(ws / name), "content": content})


def _receipt(session):
    return receipt_state(session).latest()


def _nudges(session):
    return [m for m in session.session_manager.history
            if m.get("synthetic") and "without a passing check" in str(m.get("parts"))]


def test_an_edit_followed_by_a_passing_pytest_run_is_verified(tmp_path):
    ws = tmp_path / "ws"
    session = agent_session(tmp_path, steps=[
        [_write(ws, "test_ok.py", "def test_ok():\n    assert True\n")],
        [("bash", {"command": "python3 -m pytest -q -p no:cacheprovider test_ok.py"})],
        "done",
    ])
    session.send_message("add a passing test")
    receipt = _receipt(session)
    assert receipt["status"] == "verified" and receipt["files"] == ["test_ok.py"]
    assert receipt["checks"][0]["passed"] and receipt["checks"][0]["after_edit"]
    assert not _nudges(session)


def test_an_edit_without_a_check_gets_one_nudge_then_an_unverified_receipt(tmp_path):
    ws = tmp_path / "ws"
    session = agent_session(tmp_path, steps=[[_write(ws, "a.py", "x = 1\n")], "done", "still done"])
    session.send_message("change a.py")
    assert len(_nudges(session)) == 1
    receipt = _receipt(session)
    assert receipt["status"] == "unverified" and receipt["nudged"]


def test_a_check_before_the_edit_does_not_count(tmp_path):
    ws = tmp_path / "ws"
    (ws).mkdir(parents=True, exist_ok=True)
    (ws / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    session = agent_session(tmp_path, workspace=ws, steps=[
        [("bash", {"command": "python3 -m pytest -q -p no:cacheprovider test_ok.py"})],
        [_write(ws, "b.py", "y = 2\n")],
        "done", "done",
    ])
    session.send_message("run tests then edit")
    assert _receipt(session)["status"] == "unverified"


def test_bash_purpose_verify_records_any_command_and_explore_never_does(tmp_path):
    ws = tmp_path / "ws"
    session = agent_session(tmp_path, steps=[
        [_write(ws, "c.txt", "hello\n")],
        [("bash", {"command": "grep -q hello c.txt", "purpose": "verify"})],
        [("bash", {"command": "pytest --version", "purpose": "explore"})],
        "done",
    ])
    session.send_message("write c.txt and check it")
    receipt = _receipt(session)
    assert receipt["status"] == "verified"
    assert [c["command"] for c in receipt["checks"]] == ["grep -q hello c.txt"]


def test_the_nudge_can_be_turned_off(tmp_path):
    ws = tmp_path / "ws"
    session = agent_session(tmp_path, steps=[[_write(ws, "d.py", "z = 3\n")], "done"])
    session.variables["verify_nudge"] = False
    session.send_message("edit d.py")
    assert not _nudges(session) and _receipt(session)["status"] == "unverified"
