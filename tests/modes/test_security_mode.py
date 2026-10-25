"""Security lifecycle: scoped hypotheses, severity, coverage and report evidence."""
from pathlib import Path

import pytest

from mode_helpers import call
from security_helpers import VECTOR, finding, fixed_finding, scan_session
from mu.security import engine
from mu.security.cvss import score_vector
from mu.security.state import security_state


def test_scoped_finding_deduplicates_and_validates_locations(tmp_path):
    session = scan_session(tmp_path)
    report, f = finding(session)
    same = engine.add_finding(report, title="duplicate", cwe=f.cwe, exploit_path=f.exploit_path,
                              affected_paths=f.affected_paths, cvss_vector=VECTOR, severity_rationale="same")
    assert same is f and len(report.findings) == 1
    assert f.severity == "critical" and f.cvss_score == 9.8
    with pytest.raises(ValueError, match="location"):
        engine.add_finding(report, title="bad", cwe="CWE-20", exploit_path="x", affected_paths=["app.py:900"],
                           cvss_vector=VECTOR, severity_rationale="x")
    with pytest.raises(ValueError, match="scope"):
        engine.set_scope(report, in_scope_paths=["app.py"], entry_points=[], trust_boundaries=["a"], assets=["b"], attacker="c")


@pytest.mark.parametrize("vector,score,level", [
    (VECTOR, 9.8, "critical"),
    ("CVSS:3.1/AV:N/AC:H/PR:N/UI:R/S:U/C:L/I:L/A:N", 4.2, "medium"),
    ("CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:C/C:L/I:L/A:N", 6.1, "medium"),
    ("CVSS:3.1/AV:P/AC:H/PR:H/UI:R/S:U/C:N/I:N/A:N", 0, "info"),
])
def test_cvss_base_score(vector, score, level):
    assert score_vector(vector) == (score, level)


@pytest.mark.parametrize("vector", ["CVSS:3.0/AV:N", VECTOR + "/AV:L", VECTOR + "/E:H", VECTOR.replace("C:H", "C:X")])
def test_invalid_cvss_refused(vector):
    with pytest.raises(ValueError):
        score_vector(vector)


def test_coverage_and_report_separate_observations_and_refutations(tmp_path):
    session = scan_session(tmp_path)
    report, f = finding(session)
    Path(report.workspace_root, "sink.py").write_text("@app.get('/')\ndef entry(x):\n    return eval(x)\n")
    assert engine.scan_attack_surface(report) == {"total": 2, "reviewed": 0, "sinks": 1, "sinks_reviewed": 0}
    sink = next(s for s in report.surface if s["kind"] == "sink")
    engine.review_surface_item(report, sink["id"], "observation", "hardening only")
    entry = next(s for s in report.surface if s["kind"] == "entry")
    engine.review_surface_item(report, entry["id"], "reviewed_clean", "test fixture route")
    engine.refute_finding(f, reason="input is not attacker controlled")
    engine.persist(session, report)
    assert report.status == "complete" and engine.coverage(report)["reviewed"] == 2
    report_text = Path(engine.generate_report(report)).read_text()
    assert "Refuted hypotheses" in report_text and "input is not attacker controlled" in report_text
    assert "Observations" in report_text and "hardening only" in report_text
    assert engine.scan_attack_surface(report)["reviewed"] == 2
    Path(report.workspace_root, "sink.py").write_text("@app.get('/')\ndef entry(x):\n    return eval(x + '1')\n")
    assert engine.scan_attack_surface(report)["reviewed"] == 1


def test_full_lifecycle_approve_and_report(tmp_path):
    session = scan_session(tmp_path)
    report, f = fixed_finding(session)
    assert len(f.runs) == 3 and all(Path(report.directory, r["evidence_path"], "output.txt").is_file() for r in f.runs)
    assert call(session, "approve_security_finding", finding_id=f.finding_id)["ok"]
    report = engine.active_report(session)
    assert report.status == "complete" and report.find(f.finding_id).status == "approved"
    assert not call(session, "refute_security_finding", finding_id=f.finding_id, reason="change mind")["ok"]
    report_text = Path(engine.generate_report(report)).read_text()
    assert "9.8 critical" in report_text and "findings/f001.md" in report_text
    finding_text = Path(report.directory, "findings/f001.md").read_text()
    assert "fabricated data exposed" in finding_text and "../proofs/f001/" in finding_text


def test_session_reload_reopens_only_its_scan(tmp_path):
    from mu.modes.state import ModeStates
    session = scan_session(tmp_path)
    report, _ = finding(session)
    raw = session.session_manager.mode_state.to_dict()
    session.session_manager.mode_state = ModeStates()
    session.session_manager.mode_state.load_dict(raw)
    assert engine.active_report(session).scan_id == report.scan_id
    assert security_state(session).to_dict() == raw["security"]
    report_json = Path(report.metadata_path)
    import json
    data = json.loads(report_json.read_text())
    data["unknown_future_field"] = True
    data["findings"][0]["proof"]["future"] = "ignored"
    report_json.write_text(json.dumps(data))
    assert engine.active_report(session).findings[0].proof.command == "python3 -B probe.py"


def test_empty_reviewed_surface_can_complete_without_inventing_a_finding(tmp_path):
    session = scan_session(tmp_path)
    report = engine.active_report(session)
    assert engine.scan_attack_surface(report, patterns=["no-such-sink"])['total'] == 0
    engine.persist(session, report)
    assert report.status == 'complete' and report.findings == []
