import json
from types import SimpleNamespace

import core.tools as tools
from core.tools import execute_tool
from core.workspace import FolderContext


def _ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "HISTORY_DIR", str(tmp_path / ".mucli"))
    manager = SimpleNamespace(current_session_name="scan-policy")
    session = SimpleNamespace(session_manager=manager)
    folder = FolderContext()
    folder.add_folder(str(tmp_path))
    return folder, session


def _finding(status="confirmed"):
    return {
        "id": "FX-1", "title": "XSS", "severity": "high", "confidence": "high", "cwe": "CWE-79",
        "affected_files": ["a.py"], "affected_functions": ["render"], "preconditions": ["input reflected"],
        "exploit_steps": ["inject script"], "evidence": ["trace"], "fix_recommendation": "escape",
        "verification_steps": ["rerun"], "status": status,
    }


def test_confirmed_requires_artifact(tmp_path, monkeypatch):
    folder, session = _ctx(tmp_path, monkeypatch)
    res = json.loads(execute_tool("create_scan_finding", _finding("confirmed"), folder, session=session))
    assert res["ok"] is False


def test_report_requires_repro_and_fix_verification(tmp_path, monkeypatch):
    folder, session = _ctx(tmp_path, monkeypatch)
    # create as unconfirmed first
    assert json.loads(execute_tool("create_scan_finding", _finding("unconfirmed"), folder, session=session))["ok"] is True
    # attach only repro success
    assert json.loads(execute_tool("attach_scan_artifact", {
        "finding_id": "FX-1", "artifact_name": "repro.log", "content": "ok", "artifact_role": "repro", "success": True
    }, folder, session=session))["ok"] is True
    # promote to confirmed now has artifact
    assert json.loads(execute_tool("create_scan_finding", _finding("confirmed"), folder, session=session))["ok"] is True
    blocked = json.loads(execute_tool("generate_scan_report", {}, folder, session=session))
    assert blocked["ok"] is False
    # attach verification success and pass
    assert json.loads(execute_tool("attach_scan_artifact", {
        "finding_id": "FX-1", "artifact_name": "verify.log", "content": "fixed", "artifact_role": "fix_verification", "success": True
    }, folder, session=session))["ok"] is True
    report = json.loads(execute_tool("generate_scan_report", {}, folder, session=session))
    assert report["ok"] is True


def test_report_exports_json_and_markdown(tmp_path, monkeypatch):
    folder, session = _ctx(tmp_path, monkeypatch)
    # create unconfirmed + evidence + promote confirmed + verification
    assert json.loads(execute_tool("create_scan_finding", _finding("unconfirmed"), folder, session=session))["ok"] is True
    for role, content in [("repro", "repro ok"), ("fix_verification", "verify ok")]:
        assert json.loads(execute_tool("attach_scan_artifact", {
            "finding_id": "FX-1", "artifact_name": f"{role}.log", "content": content, "artifact_role": role, "success": True
        }, folder, session=session))["ok"] is True
    assert json.loads(execute_tool("create_scan_finding", _finding("confirmed"), folder, session=session))["ok"] is True

    json_report = json.loads(execute_tool("generate_scan_report", {"format": "json"}, folder, session=session))
    assert json_report["ok"] is True
    assert "findings" in json_report

    md_report = json.loads(execute_tool("generate_scan_report", {"format": "markdown"}, folder, session=session))
    assert md_report["ok"] is True
    assert "# Security Scan Report" in md_report["markdown"]



def test_unconfirmed_when_repro_fails_and_minimal_poc_attached(tmp_path, monkeypatch):
    folder, session = _ctx(tmp_path, monkeypatch)
    f = _finding("unconfirmed")
    assert json.loads(execute_tool("create_scan_finding", f, folder, session=session))["ok"] is True
    attach = json.loads(execute_tool("attach_scan_artifact", {
        "finding_id": "FX-1", "artifact_name": "poc.sh", "content": "echo poc", "artifact_role": "repro", "success": False
    }, folder, session=session))
    assert attach["ok"] is True
    listed = json.loads(execute_tool("list_scan_findings", {}, folder, session=session))
    finding = listed["data"]["findings"][0]
    assert finding["status"] == "unconfirmed"
    assert finding["artifacts"][0]["artifact_name"] == "poc.sh"
