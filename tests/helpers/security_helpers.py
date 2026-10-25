"""Small real workspace with a reproducible bug and a regression check."""
import subprocess

from mode_helpers import agent_session, call
from mu.security import engine

VECTOR = "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"
PATCH = "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-VULNERABLE = True\n+VULNERABLE = False\n"


def scan_session(tmp_path):
    session = agent_session(tmp_path, mode="security")
    root = tmp_path / "ws"
    (root / "app.py").write_text("VULNERABLE = True\n")
    (root / "probe.py").write_text("from app import VULNERABLE\nprint('PROBE-RAN')\nif VULNERABLE: print('EXPLOIT')\n")
    for args in (("init", "-q"), ("add", "app.py", "probe.py"),
                 ("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "baseline")):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    engine.create_security_report(owner=session, title="test audit")
    report = engine.active_report(session)
    engine.set_scope(report, in_scope_paths=["."], entry_points=["test input"], trust_boundaries=["input to app"],
                     assets=["fabricated data"], attacker="unauthenticated caller")
    engine.persist(session, report)
    engine.configure_runner(session, "host", actor="user:cli")
    return session


def finding(session):
    report = engine.active_report(session)
    f = engine.add_finding(report, title="test bug", cwe="CWE-20", exploit_path="input reaches app",
                           affected_paths=["app.py:1"], cvss_vector=VECTOR, severity_rationale="test impact")
    engine.attach_proof(f, command="python3 -B probe.py", expected_markers=["EXPLOIT"], files=["probe.py"], probe_marker="PROBE-RAN")
    engine.persist(session, report)
    return report, f


def verify(session, name, finding_id):
    engine.prepare_preview(session, finding_id, remediation=name == "verify_remediation")
    return call(session, name, finding_id=finding_id)


def fixed_finding(session):
    report, f = finding(session)
    assert verify(session, "verify_security_proof", f.finding_id)["ok"]
    report = engine.active_report(session)
    f = report.find(f.finding_id)
    engine.confirm_severity(f, cvss_vector=VECTOR, rationale="matches proof", demonstrated_impact="fabricated data exposed")
    engine.attach_remediation(f, description="validate input", patch_diff=PATCH,
                              regression_checks=["python3 -B -c 'from app import VULNERABLE; assert not VULNERABLE'"])
    engine.persist(session, report)
    from pathlib import Path
    Path(report.workspace_root, "app.py").write_text("VULNERABLE = False\n")
    assert verify(session, "verify_remediation", f.finding_id)["ok"]
    report = engine.active_report(session)
    return report, report.find(f.finding_id)
