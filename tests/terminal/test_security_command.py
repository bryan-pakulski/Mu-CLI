"""The operator can inspect scans, opt into host runs and reopen explicitly."""
from security_helpers import scan_session
from mu.commands.security import security_cmd
from mu.security import engine


def test_security_command_runner_scope_and_open(tmp_path):
    session = scan_session(tmp_path)
    report = engine.active_report(session)
    assert report.scan_id in security_cmd(session, "status", allow_prompt=False).message
    assert "attacker" in security_cmd(session, "scope", allow_prompt=False).message
    assert security_cmd(session, "runner docker", allow_prompt=False).ok
    assert engine.runner_for(session, report) == "docker"
    assert security_cmd(session, "runner host", allow_prompt=False).ok
    assert engine.runner_for(session, report) == "host"
    assert security_cmd(session, "scan another", allow_prompt=False).ok
    assert security_cmd(session, f"open {report.scan_id}", allow_prompt=False).ok
    session.variables["plan_mode"] = True
    assert not security_cmd(session, "runner docker", allow_prompt=False).ok
    assert not security_cmd(session, "report", allow_prompt=False).ok
    assert security_cmd(session, "coverage", allow_prompt=False).ok
