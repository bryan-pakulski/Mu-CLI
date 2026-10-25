"""Security runner gates: no implicit host execution, stale approvals or false fixes."""
from pathlib import Path

import pytest

from mode_helpers import call
from security_helpers import PATCH, VECTOR, finding, fixed_finding, scan_session, verify
from mu.modes.evidence import CheckRefused
from mu.security import engine
from mu.security.state import security_state


def test_host_requires_operator_per_scan_choice(tmp_path):
    session = scan_session(tmp_path)
    report = engine.active_report(session)
    assert engine.runner_for(session, report) == "host"
    engine.create_security_report(owner=session, title="second")
    other = engine.active_report(session)
    assert engine.runner_for(session, other) == "docker"
    with pytest.raises(ValueError, match="only the user"):
        engine.configure_runner(session, "host", actor="ai:tool")
    engine.open_report(session, report.scan_id)
    assert engine.runner_for(session, report) == "host"
    engine.configure_runner(session, "auto", actor="user:gui")
    assert engine.runner_for(session, report) == "docker"


@pytest.mark.parametrize("command,script", [("cat ~/.ssh/id_rsa", ""), ("python3 -B probe.py", "print(open('/home/test/.aws/credentials').read())")])
def test_secret_command_and_script_refused(tmp_path, command, script):
    session = scan_session(tmp_path)
    report, f = finding(session)
    f.proof.command = command
    if script:
        Path(report.workspace_root, "probe.py").write_text(script)
    with pytest.raises(CheckRefused, match="Secret-guard"):
        engine.verify_proof(f, report=report, owner=session)
    assert not f.runs


def test_preview_shows_command_scripts_and_invalidates_changed_file(tmp_path):
    session = scan_session(tmp_path)
    report, f = finding(session)
    preview = engine.prepare_preview(session, f.finding_id)
    assert preview["command"] == f.proof.command and "PROBE-RAN" in preview["scripts"]["probe.py"]
    assert preview["runner"] == "host"
    Path(report.workspace_root, "probe.py").write_text("print('EXPLOIT')")
    result = call(session, "verify_security_proof", finding_id=f.finding_id)
    assert not result["ok"] and result["error_code"] == "approval_stale"
    assert not engine.active_report(session).find(f.finding_id).runs


def test_changed_poc_requires_reproof_and_reproof_clears_fix(tmp_path):
    session = scan_session(tmp_path)
    report, f = fixed_finding(session)
    probe = Path(report.workspace_root, "probe.py")
    probe.write_text(probe.read_text() + "\n# revision\n")
    result = verify(session, "verify_remediation", f.finding_id)
    assert not result["ok"] and "re-proof" in result["message"]
    Path(report.workspace_root, "app.py").write_text("VULNERABLE = True\n")
    assert verify(session, "verify_security_proof", f.finding_id)["ok"]
    refreshed = engine.active_report(session).find(f.finding_id)
    assert refreshed.remediation is None and not refreshed.severity_confirmed
    assert len(refreshed.runs) == 4


@pytest.mark.parametrize("change", ["missing_patch", "crash", "regression", "partial_marker", "missing_probe"])
def test_false_remediation_is_not_verified(tmp_path, change):
    session = scan_session(tmp_path)
    report, f = finding(session)
    if change == "partial_marker":
        f.proof.expected_markers.append("SECOND")
        Path(report.workspace_root, "probe.py").write_text("from app import VULNERABLE\nprint('PROBE-RAN')\nprint('EXPLOIT')\nif VULNERABLE: print('SECOND')\n")
    if change == "missing_probe":
        Path(report.workspace_root, "probe.py").write_text("from app import VULNERABLE\nif VULNERABLE: print('PROBE-RAN EXPLOIT')\n")
    engine.persist(session, report)
    assert verify(session, "verify_security_proof", f.finding_id)["ok"]
    report = engine.active_report(session)
    f = report.find(f.finding_id)
    patch = PATCH
    replacement = "VULNERABLE = False\n"
    if change == "crash":
        replacement = "raise RuntimeError('broken')\n"
        patch = PATCH.replace("VULNERABLE = False", "raise RuntimeError('broken')")
    engine.attach_remediation(f, description="fix", patch_diff=patch,
                              regression_checks=["false" if change == "regression" else "true"])
    engine.persist(session, report)
    if change != "missing_patch":
        Path(report.workspace_root, "app.py").write_text(replacement)
    assert not verify(session, "verify_remediation", f.finding_id)["ok"]
    assert not engine.active_report(session).find(f.finding_id).remediation.verified


def test_nonzero_exit_timeout_and_missing_marker_do_not_prove(tmp_path):
    session = scan_session(tmp_path)
    report, f = finding(session)
    for command in ("echo EXPLOIT; exit 7", "echo unrelated", "echo EXPLOIT; sleep 2"):
        engine.attach_proof(f, command=command, expected_markers=["EXPLOIT"])
        proof = engine.verify_proof(f, report=report, owner=session, timeout=1)
        assert not proof.verified
    assert len(f.runs) == 3


def test_host_environment_has_no_credentials_or_startup_script(tmp_path, monkeypatch):
    session = scan_session(tmp_path)
    report, f = finding(session)
    startup = Path(report.workspace_root, "startup.sh")
    startup.write_text("touch unexpected\n")
    monkeypatch.setenv("BASH_ENV", str(startup))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-credential")
    engine.attach_proof(f, command="python3 -c 'import os; print(\"CLEAN\" if \"ANTHROPIC_API_KEY\" not in os.environ else \"LEAK\")'", expected_markers=["CLEAN"])
    assert engine.verify_proof(f, report=report, owner=session).verified
    assert not Path(report.workspace_root, "unexpected").exists()


def test_plan_mode_blocks_every_security_mutation(tmp_path):
    from mu.tools.security.handlers import _SPECS
    session = scan_session(tmp_path)
    session.variables["plan_mode"] = True
    before = security_state(session).to_dict()
    for name, spec in _SPECS.items():
        if spec[3]:
            assert call(session, name)["ok"]
        else:
            result = call(session, name)
            assert not result["ok"] and result["error_code"] == "plan_mode_blocked", name
    assert security_state(session).to_dict() == before


def test_approval_rechecks_fix_integrity(tmp_path):
    session = scan_session(tmp_path)
    report, f = fixed_finding(session)
    Path(report.workspace_root, "app.py").write_text("VULNERABLE = False\n# changed\n")
    assert not call(session, "approve_security_finding", finding_id=f.finding_id)["ok"]


def test_yolo_still_requests_poc_approval_with_full_preview(tmp_path):
    session = scan_session(tmp_path)
    report, f = finding(session)
    previews = []
    def approve(**kwargs):
        previews.append(kwargs['display_args'])
        return 'y', ''
    session._request_tool_approval = approve
    session.provider.push([('verify_security_proof', {'finding_id': f.finding_id})], 'done')
    session.send_message('Verify the hypothesis')
    assert len(previews) == 1 and previews[0]['command'] == f.proof.command
    assert 'PROBE-RAN' in previews[0]['scripts']['probe.py']
    assert engine.active_report(session).find(f.finding_id).proof.verified


def test_extensionless_poc_is_frozen_even_when_files_omitted(tmp_path):
    session = scan_session(tmp_path)
    report, f = finding(session)
    Path(report.workspace_root, "probe").write_text("print('PROBE-RAN EXPLOIT')\n")
    engine.attach_proof(f, command="python3 -B probe", expected_markers=["EXPLOIT"], probe_marker="PROBE-RAN")
    proof = engine.verify_proof(f, report=report, owner=session)
    assert proof.verified and "probe" in proof.hashes
    bundle = Path(proof.last_run_evidence_path, "bundle.json").read_text()
    assert "PROBE-RAN EXPLOIT" in bundle
