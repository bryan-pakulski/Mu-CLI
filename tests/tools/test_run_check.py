"""run_check runs commands under the bash controls and records evidence (modes v2 §3.2)."""

import os

import pytest

from mu.modes.evidence import CheckRefused, CheckSpec, checks_state, read_output, run_check

from mode_helpers import agent_session


def test_a_passing_command_is_recorded_with_its_output(tmp_path):
    session = agent_session(tmp_path)
    run = run_check(CheckSpec(command="echo hello && exit 0", purpose="verify"), session)
    assert run.passed and run.exit_code == 0
    assert run.id == "r1" and checks_state(session).get("r1") is run
    assert "hello" in run.excerpt and "hello" in read_output(run)
    assert run.cwd == os.path.abspath(session.folder_context.folders[0])


def test_marker_predicates_and_failure_detail(tmp_path):
    session = agent_session(tmp_path)
    present = run_check(CheckSpec(command="echo PWNED; exit 3",
                                  predicate={"markers": ["PWNED"]}), session)
    absent = run_check(CheckSpec(command="echo PWNED",
                                 predicate={"markers": ["PWNED"], "absent": True}), session)
    nonzero = run_check(CheckSpec(command="exit 2", predicate={"exit_nonzero": True}), session)
    assert present.passed and nonzero.passed
    assert not absent.passed and "PWNED" in absent.detail


def test_plan_mode_refuses_to_run(tmp_path):
    session = agent_session(tmp_path)
    session.variables["plan_mode"] = True
    marker = tmp_path / "ran"
    with pytest.raises(CheckRefused) as err:
        run_check(CheckSpec(command=f"touch {marker}"), session)
    assert err.value.code == "plan_mode_blocked"
    assert not marker.exists()


def test_secret_guard_refuses_secret_paths(tmp_path):
    session = agent_session(tmp_path)
    with pytest.raises(CheckRefused) as err:
        run_check(CheckSpec(command="cat ~/.ssh/id_rsa"), session)
    assert err.value.code == "secret_guard_blocked"


def test_credentials_do_not_reach_the_command(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("SOME_SERVICE_TOKEN", "tok-should-not-leak")
    session = agent_session(tmp_path)
    run = run_check(CheckSpec(command="printenv PATH; printenv OPENAI_API_KEY; "
                                      "printenv SOME_SERVICE_TOKEN; echo end"), session)
    output = read_output(run)
    assert "should-not-leak" not in output
    assert "/" in output.splitlines()[0] and output.strip().endswith("end")


def test_no_login_shell_profile_is_sourced(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bash_profile").write_text("echo PROFILE_RAN\n")
    (home / ".bashrc").write_text("echo RC_RAN\n")
    monkeypatch.setenv("HOME", str(home))
    session = agent_session(tmp_path)
    run = run_check(CheckSpec(command="echo body"), session)
    assert "PROFILE_RAN" not in read_output(run) and "RC_RAN" not in read_output(run)


def test_timeouts_are_recorded_as_failures(tmp_path):
    session = agent_session(tmp_path)
    run = run_check(CheckSpec(command="sleep 5", timeout_s=1), session)
    assert run.timed_out and not run.passed and run.exit_code is None


def test_pytest_failures_become_data(tmp_path):
    session = agent_session(tmp_path)
    ws = tmp_path / "ws"
    (ws / "test_sample.py").write_text(
        "def test_ok():\n    assert True\n\ndef test_bad():\n    assert 1 == 2\n")
    run = run_check(CheckSpec(command="python3 -m pytest -q -p no:cacheprovider test_sample.py",
                              purpose="suite"), session)
    assert not run.passed
    assert run.tests["passed"] == 1 and run.tests["failed"] == 1
    assert any("test_bad" in failing for failing in run.tests["failing"])


def test_hashes_of_dependent_files_are_kept(tmp_path):
    session = agent_session(tmp_path)
    (tmp_path / "ws" / "poc.sh").write_text("echo hi\n")
    run = run_check(CheckSpec(command="bash poc.sh", hash_paths=["poc.sh", "gone.sh"]), session)
    assert len(run.hashes["poc.sh"]) == 64 and run.hashes["gone.sh"] == "missing"
