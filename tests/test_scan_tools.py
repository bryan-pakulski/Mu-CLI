import json
from types import SimpleNamespace

import core.tools as tools
from core.tools import ToolExecutionContext, _handle_create_scan_finding, _handle_attach_scan_artifact, _handle_list_scan_findings, execute_tool
from core.workspace import FolderContext


def _ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "HISTORY_DIR", str(tmp_path / ".mucli"))
    manager = SimpleNamespace(current_session_name="scan-test")
    session = SimpleNamespace(session_manager=manager)
    folder = FolderContext()
    folder.add_folder(str(tmp_path))
    return ToolExecutionContext(folder_context=folder, session=session)


def test_create_scan_finding_requires_schema_fields(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, monkeypatch)
    out = _handle_create_scan_finding({"id": "F-1"}, ctx)
    assert "Missing required fields" in out


def test_scan_finding_and_artifact_are_persisted(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, monkeypatch)
    finding = {
        "id": "F-1",
        "title": "Path traversal",
        "severity": "high",
        "confidence": "high",
        "cwe": "CWE-22",
        "cvss": 8.8,
        "affected_files": ["app.py"],
        "affected_functions": ["read_user_path"],
        "preconditions": ["attacker can control filename"],
        "exploit_steps": ["send ../etc/passwd"],
        "evidence": ["failing test output"],
        "fix_recommendation": "normalize and enforce root",
        "verification_steps": ["re-run exploit test"],
        "status": "unconfirmed",
    }
    created = json.loads(_handle_create_scan_finding(finding, ctx))
    assert created["status"] == "ok"

    artifact = json.loads(
        _handle_attach_scan_artifact(
            {
                "finding_id": "F-1",
                "artifact_name": "repro.log",
                "artifact_type": "command_output",
                "content": "Traceback...",
            },
            ctx,
        )
    )
    assert artifact["status"] == "ok"
    assert artifact["artifact"]["sha256"]
    assert artifact["artifact"]["timestamp"].endswith("Z")

    finding["status"] = "confirmed"
    promoted = json.loads(_handle_create_scan_finding(finding, ctx))
    assert promoted["status"] == "ok"

    listed = json.loads(_handle_list_scan_findings({}, ctx))
    assert len(listed["findings"]) == 1
    assert listed["findings"][0]["id"] == "F-1"
    assert listed["findings"][0]["artifacts"][0]["artifact_name"] == "repro.log"


def test_scan_tools_work_via_execute_tool_envelopes(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, monkeypatch)
    finding_args = {
        "id": "F-2",
        "title": "Command injection",
        "severity": "critical",
        "confidence": "high",
        "cwe": "CWE-78",
        "cvss": 9.8,
        "affected_files": ["runner.py"],
        "affected_functions": ["run_cmd"],
        "preconditions": ["user controls shell arg"],
        "exploit_steps": ["inject ';id'"],
        "evidence": ["command output shows id"],
        "fix_recommendation": "use subprocess list args",
        "verification_steps": ["re-run exploit after patch"],
        "status": "unconfirmed",
    }
    created = json.loads(execute_tool("create_scan_finding", finding_args, ctx.folder_context, session=ctx.session))
    assert created["ok"] is True

    promote_args = dict(finding_args)
    promote_args["status"] = "confirmed"
    blocked_promote = json.loads(execute_tool("create_scan_finding", promote_args, ctx.folder_context, session=ctx.session))
    assert blocked_promote["ok"] is False

    attach = json.loads(execute_tool(
        "attach_scan_artifact",
        {"finding_id": "F-2", "artifact_name": "repro.log", "content": "safe", "artifact_role": "repro", "success": True},
        ctx.folder_context,
        session=ctx.session,
    ))
    assert attach["ok"] is True
    promoted = json.loads(execute_tool("create_scan_finding", promote_args, ctx.folder_context, session=ctx.session))
    assert promoted["ok"] is True

    blocked = json.loads(execute_tool(
        "attach_scan_artifact",
        {"finding_id": "F-2", "artifact_name": "net.log", "content": "nmap https://example.com"},
        ctx.folder_context,
        session=ctx.session,
    ))
    assert blocked["ok"] is False

    listed = json.loads(execute_tool("list_scan_findings", {}, ctx.folder_context, session=ctx.session))
    assert listed["ok"] is True
    assert listed["data"]["findings"][0]["id"] == "F-2"
