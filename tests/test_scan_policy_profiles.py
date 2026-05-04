import json
from types import SimpleNamespace

import core.tools as tools
from core.tools import execute_tool
from core.workspace import FolderContext


def _ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "HISTORY_DIR", str(tmp_path / ".mucli"))
    manager = SimpleNamespace(current_session_name="scan-profile")
    session = SimpleNamespace(session_manager=manager)
    folder = FolderContext()
    folder.add_folder(str(tmp_path))
    return folder, session


def test_default_policy_profile_is_safe_local(tmp_path, monkeypatch):
    folder, session = _ctx(tmp_path, monkeypatch)
    out = json.loads(execute_tool("get_scan_policy_profile", {}, folder, session=session))
    assert out["ok"] is True
    assert out["data"]["policy_profile"] == "safe_local"


def test_set_policy_profile_and_enforce_network_guardrails(tmp_path, monkeypatch):
    folder, session = _ctx(tmp_path, monkeypatch)
    set_out = json.loads(execute_tool("set_scan_policy_profile", {"profile": "network_opt_in"}, folder, session=session))
    assert set_out["ok"] is True

    blocked = json.loads(execute_tool("attach_scan_artifact", {
        "finding_id": "missing", "artifact_name": "nmap.log", "content": "nmap https://example.com"
    }, folder, session=session))
    assert blocked["ok"] is False
    assert "allow_network=true" in blocked["message"]


def test_safe_local_blocks_remote_scan_commands(tmp_path, monkeypatch):
    folder, session = _ctx(tmp_path, monkeypatch)
    out = json.loads(execute_tool("run_sast_scan", {"command": "semgrep --config auto https://example.com"}, folder, session=session))
    assert out["ok"] is False
    assert "policy profile" in out["message"].lower()
