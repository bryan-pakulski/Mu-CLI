"""Debug cases: typed, engine-checked, persisted (modes v2 §4.2, G1/G2/G4)."""

import subprocess

from mu.debug.state import debug_state
from mu.session.manager import SessionManager

from mode_helpers import agent_session, call


def _project(tmp_path, *, buggy=True):
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    body = "return d['id']" if buggy else "return d.get('id')"
    (ws / "lib.py").write_text(f"def get_id(d):\n    {body}\n")
    (ws / "test_lib.py").write_text(
        "from lib import get_id\n\ndef test_missing():\n    assert get_id({}) is None\n\n"
        "def test_present():\n    assert get_id({'id': 3}) == 3\n")
    return ws


def _fix(ws):
    (ws / "lib.py").write_text("def get_id(d):\n    return d.get('id')\n")


REPRO = "python3 -m pytest -q -p no:cacheprovider test_lib.py::test_missing"
SUITE = "python3 -m pytest -q -p no:cacheprovider test_lib.py"


def _open(session):
    return call(session, "open_debug_case", title="KeyError in get_id", symptom="test_missing raises KeyError")


def test_a_repro_that_passes_is_refused(tmp_path):
    ws = _project(tmp_path, buggy=False)
    session = agent_session(tmp_path, mode="debug", workspace=ws)
    _open(session)
    result = call(session, "record_repro", command=REPRO)
    assert not result["ok"] and result["error_code"] == "repro_not_failing"
    assert debug_state(session).current_case().status == "open"


def test_full_ladder_to_closed_with_root_cause(tmp_path):
    ws = _project(tmp_path)
    session = agent_session(tmp_path, mode="debug", workspace=ws)
    _open(session)
    repro = call(session, "record_repro", command=REPRO, expect_marker="KeyError")
    assert repro["ok"] and repro["data"]["status"] == "reproduced"
    hyp = call(session, "add_hypothesis", statement="get_id indexes a missing key")["data"]["id"]

    refused = call(session, "update_hypothesis", id=hyp, status="confirmed",
                   evidence=[{"kind": "note", "ref": "it seems likely", "stance": "supports"}])
    assert not refused["ok"] and refused["error_code"] == "evidence_required"
    confirmed = call(session, "update_hypothesis", id=hyp, status="confirmed",
                     evidence=[{"kind": "location", "ref": "lib.py:2", "stance": "supports"}])
    assert confirmed["ok"] and confirmed["data"]["case_status"] == "root_caused"

    early = call(session, "close_debug_case", cause="indexing a missing key")
    assert not early["ok"] and early["error_code"] == "not_verified"

    _fix(ws)
    verified = call(session, "verify_fix", summary="use dict.get", suite_command=SUITE, repeat=2)
    assert verified["ok"] and verified["data"]["status"] == "verified"
    closed = call(session, "close_debug_case", cause="get_id indexed d['id'] without a default",
                  signature="KeyError: 'id' in get_id")
    assert closed["ok"] and closed["data"]["status"] == "closed"
    assert "no regression test" in " ".join(closed["data"]["gaps"])
    case = debug_state(session).cases["d1"]
    assert case.root_cause["cause"].startswith("get_id indexed")


def test_verify_fix_fails_when_the_suite_fails(tmp_path):
    ws = _project(tmp_path)
    session = agent_session(tmp_path, mode="debug", workspace=ws)
    _open(session)
    call(session, "record_repro", command=REPRO, expect_marker="KeyError")
    _fix(ws)
    (ws / "test_other.py").write_text("def test_broken():\n    assert False\n")
    result = call(session, "verify_fix", summary="use get",
                  suite_command="python3 -m pytest -q -p no:cacheprovider .")
    assert not result["ok"] and result["data"]["status"] == "fixed"


def test_regression_test_must_fail_before_the_fix(tmp_path):
    ws = _project(tmp_path)
    for args in (["init", "-q"], ["add", "."], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"]):
        subprocess.run(["git", *args], cwd=ws, check=True, capture_output=True)
    session = agent_session(tmp_path, mode="debug", workspace=ws)
    _open(session)
    call(session, "record_repro", command=REPRO, expect_marker="KeyError")
    _fix(ws)
    (ws / "test_regress.py").write_text(
        "from lib import get_id\n\ndef test_no_key():\n    assert get_id({'x': 1}) is None\n")
    recorded = call(session, "record_regression_test", path="test_regress.py", name="test_no_key")
    assert recorded["ok"], recorded["message"]
    (ws / "test_useless.py").write_text("def test_trivial():\n    assert True\n")
    useless = call(session, "record_regression_test", path="test_useless.py", name="test_trivial")
    assert not useless["ok"] and "passes on HEAD" in useless["message"]


def test_bisect_finds_the_breaking_commit(tmp_path):
    ws = _project(tmp_path, buggy=False)
    git = lambda *a: subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *a], cwd=ws,
                                    check=True, capture_output=True, text=True).stdout.strip()
    git("init", "-q"); git("add", "."); git("commit", "-qm", "good")
    good = git("rev-parse", "HEAD")
    (ws / "notes.txt").write_text("x\n"); git("add", "."); git("commit", "-qm", "unrelated")
    (ws / "lib.py").write_text("def get_id(d):\n    return d['id']\n"); git("commit", "-qam", "break it")
    breaking = git("rev-parse", "HEAD")
    session = agent_session(tmp_path, mode="debug", workspace=ws)
    _open(session)
    call(session, "record_repro", command=REPRO, expect_marker="KeyError")
    result = call(session, "bisect_repro", good=good)
    assert result["ok"] and breaking.startswith(result["data"]["first_bad"])
    # The user's tree was never checked out: same HEAD, no tracked changes.
    assert git("rev-parse", "HEAD") == breaking
    assert git("status", "--porcelain", "--untracked-files=no") == ""


def test_state_survives_a_session_reload(tmp_path):
    ws = _project(tmp_path)
    session = agent_session(tmp_path, mode="debug", workspace=ws, name="debug-reload")
    _open(session)
    call(session, "add_hypothesis", statement="missing key")
    call(session, "add_suspect", path="lib.py", line=2, reason="indexes d['id']")
    session.provider.push("noted")
    session.send_message("next turn — scratchpad wipe happens here")
    reloaded = SessionManager(session_name="debug-reload")
    case = debug_state(reloaded).current_case()
    assert case.title == "KeyError in get_id"
    assert [h.statement for h in case.hypotheses] == ["missing key"]
    assert case.suspects[0].path == "lib.py" and case.suspects[0].line == 2


def test_suspect_paths_are_validated(tmp_path):
    session = agent_session(tmp_path, mode="debug", workspace=_project(tmp_path))
    _open(session)
    assert not call(session, "add_suspect", path="nope.py", reason="x")["ok"]
    assert not call(session, "add_suspect", path="lib.py", line=99, reason="x")["ok"]


# ------------------------------------------------------------------ G2 gates


def test_first_edit_without_a_repro_is_refused_then_allowed(tmp_path):
    ws = _project(tmp_path)
    session = agent_session(tmp_path, mode="debug", workspace=ws, steps=[
        [("open_debug_case", {"title": "bug", "symptom": "KeyError"})],
        [("write_file", {"filename": str(ws / "lib.py"), "content": "x = 1\n"})],
        [("write_file", {"filename": str(ws / "lib.py"), "content": "x = 2\n"})],
        "done",
    ])
    session.send_message("fix the KeyError in lib.py")
    results = [str(m) for m in session.session_manager.history if m.get("role") in ("tool", "function", "user")]
    assert any("debug_repro_required" in r for r in results)
    assert (ws / "lib.py").read_text() == "x = 2\n"


def test_require_repro_blocks_every_edit(tmp_path):
    ws = _project(tmp_path)
    session = agent_session(tmp_path, mode="debug", workspace=ws, steps=[
        [("open_debug_case", {"title": "bug", "symptom": "KeyError"})],
        [("write_file", {"filename": str(ws / "lib.py"), "content": "x = 1\n"})],
        [("write_file", {"filename": str(ws / "lib.py"), "content": "x = 2\n"})],
        "done",
    ])
    session.variables["debug_require_repro"] = True
    session.send_message("fix the KeyError in lib.py")
    assert "get_id" in (ws / "lib.py").read_text()


def test_runs_are_refused_in_plan_mode_but_the_case_still_works(tmp_path):
    ws = _project(tmp_path)
    session = agent_session(tmp_path, mode="debug", workspace=ws)
    session.variables["plan_mode"] = True
    assert _open(session)["ok"]
    assert call(session, "add_hypothesis", statement="missing key")["ok"]
    refused = session._execute_tool_with_memory("record_repro", {"command": REPRO})
    assert isinstance(refused, dict) and refused["error_code"] == "plan_mode_blocked"
    from mu.debug.engine import capsule
    assert "Plan mode" in capsule(session)
