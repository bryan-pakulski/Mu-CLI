"""Security regressions and surfaces (plan §11): CSRF, session-type gating, AI tool limits,
stdlib-only node agent, zipapp, GUI router and CLI."""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from mu.orchestration import vm
from mu.orchestration.commands import AI_COMMANDS, dispatch

from orch_helpers import FakeNode, add_node, drive, make_orch, publish_tool, tool_manifest

ROOT = Path(__file__).resolve().parent.parent
NODE_DIR = ROOT / "mu" / "orchestration" / "node"


# --------------------------------------------------------------------- node package isolation

def test_node_agent_imports_only_the_standard_library():
    stdlib = set(sys.stdlib_module_names)
    for path in NODE_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(), str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:          # relative import within the package
                    continue
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert name.split(".")[0] in stdlib or name == "__future__", f"{path.name} imports {name}"


def test_zipapp_runs_standalone(tmp_path):
    from mu.orchestration.zipapp import build_zipapp
    data, sha = build_zipapp()
    assert build_zipapp()[1] == sha          # deterministic
    pyz = tmp_path / "mucli-node.pyz"
    pyz.write_bytes(data)
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path)}
    out = subprocess.run([sys.executable, "-I", str(pyz), "version"], capture_output=True, text=True, env=env,
                         cwd=str(tmp_path), timeout=60)
    assert out.returncode == 0 and out.stdout.strip()
    pol = tmp_path / "etc"
    pol.mkdir()
    (pol / "policy.yaml").write_text("max_effect: mutating\n")
    out = subprocess.run([sys.executable, "-I", str(pyz), "--etc-dir", str(pol), "policy"], capture_output=True,
                         text=True, env=env, cwd=str(tmp_path), timeout=60)
    assert json.loads(out.stdout)["max_effect"] == "mutating"
    out = subprocess.run([sys.executable, "-I", str(pyz), "--state-dir", str(tmp_path / "s"), "run",
                          "--insecure-no-sandbox"], capture_output=True, text=True, env=env, timeout=60)
    assert out.returncode == 1 and "not enrolled" in out.stderr


# --------------------------------------------------------------------- session-type gating

def test_orch_tools_exist_only_in_orchestration_sessions():
    import mu.tools as tools
    from mu.tools.capabilities import ORCHESTRATION_TOOLS, filter_tools_for_session_type, is_tool_allowed
    orch_tools = [d.definition for d in tools.list_descriptors() if d.definition.name.startswith("orch_")]
    assert len(orch_tools) == 15
    for session_type in ("chat", "workspace", "container"):
        assert filter_tools_for_session_type(orch_tools, session_type) == []
    assert len(filter_tools_for_session_type(orch_tools, "orchestration")) == 15
    for name in ("bash", "write_file", "read_file", "apply_diff", "spawn_agent", "bash_background"):
        assert not is_tool_allowed(name, "orchestration")
    assert {t.name for t in orch_tools} <= ORCHESTRATION_TOOLS


def test_dispatcher_refuses_orch_tools_outside_orchestration_sessions(tmp_path):
    from mu.tools._dispatcher import dispatch as tool_dispatch
    raw = tool_dispatch("orch_catalog_search", {}, None, None, {"session_type": "workspace"})
    assert json.loads(raw)["error_code"] == "session_type_forbidden"


# --------------------------------------------------------------------- AI tools

@pytest.fixture
def ai_orch(tmp_path, monkeypatch):
    orch = make_orch(tmp_path)
    monkeypatch.setattr("mu.orchestration.agent_tools._orch", lambda: orch)
    yield orch
    orch.stop()


def _call(name, args, session_name="studio-1"):
    import mu.tools as tools
    ctx = tools.ToolExecutionContext(folder_context=None, variables={"session_type": "orchestration",
                                                                     "session_name": session_name})
    return tools.execute(name, args, ctx)


def test_ai_designs_tests_and_submits_but_cannot_publish_or_run_unconfirmed(ai_orch):
    """P2 exit criterion, end to end through the AI's tools."""
    orch = ai_orch
    canary, _ = add_node(orch, "canary1", {"env": "prod"}, canary=True)
    script = ("import json, os\nif os.environ['MUCLI_CHECK'] == '1':\n    print('dry run')\n"
              "json.dump({'message': 'ok'}, open(os.environ['MUCLI_RESULT'], 'w'))\n")
    found = _call("orch_catalog_search", {"query": "rotate"})
    assert found["ok"]
    created = _call("orch_tool_draft", {"manifest": tool_manifest("app.rotate", effect="mutating"),
                                         "files": {"main.py": script}, "intent": "rotate app logs"})
    assert created["ok"], created
    ref = created["data"]["ref"]
    assert orch.store.get_version("app.rotate", 1).session_name == "studio-1"
    lint = _call("orch_tool_lint", {"ref": ref})
    assert lint["ok"] and lint["data"]["ok"]
    test = _call("orch_tool_test", {"ref": ref})
    assert test["ok"] and test["data"]["state"] == "proposed"
    run_id = test["data"]["run_id"]
    dispatch(orch, {"type": "approveRun", "args": {"run_id": run_id}})        # the operator approves the test
    drive(orch, [FakeNode(orch, canary)])
    status = _call("orch_run_status", {"run_id": run_id})
    assert status["data"]["state"] == "succeeded"
    submitted = _call("orch_tool_submit", {"ref": ref, "headline": "rotate logs", "risks": ["disk churn"]})
    assert submitted["ok"] and orch.store.get_version("app.rotate", 1).status == "in_review"
    # The AI has no way to publish.
    with pytest.raises(Exception):
        dispatch(orch, {"type": "publishVersion", "args": {"ref": ref}}, actor="ai:studio-1")
    dispatch(orch, {"type": "publishVersion", "args": {"ref": ref}})          # the operator publishes
    # Running the published mutating tool from the AI only proposes it.
    proposed = _call("orch_run_propose", {"target": "app.rotate@1", "selector": {"labels": "env=prod"}})
    assert proposed["ok"] and proposed["data"]["state"] == "proposed"
    orch.tick()
    assert FakeNode(orch, canary).poll()["dispatches"] == []
    actors = {e["actor"] for e in orch.store.audit_entries(limit=10_000)}
    assert "ai:studio-1" in actors


def test_ai_results_are_capped_and_redacted(ai_orch):
    orch = ai_orch
    noisy = ("import json, os\nfor i in range(3000):\n    print('line', i, 'x' * 40)\n"
             "json.dump({'message': 'done'}, open(os.environ['MUCLI_RESULT'], 'w'))\n")
    publish_tool(orch, tool_manifest("app.noisy", limits={"max_output_kb": 512}), files={"main.py": noisy})
    node, _ = add_node(orch, "dev1", {"env": "dev"})
    proposed = _call("orch_run_propose", {"target": "app.noisy@1", "selector": {"labels": "env=dev"}})
    assert proposed["data"]["state"] in ("approved", "running")
    drive(orch, [FakeNode(orch, node)])
    results = _call("orch_run_results", {"run_id": proposed["data"]["run_id"]})
    execution = results["data"]["executions"][0]
    assert execution["output"] == {"message": "done"}
    assert len(execution["log_tail"]) <= vm.AI_LOG_TAIL and execution["log_truncated"]
    assert "log" not in execution and "params" not in execution


def test_ai_dry_run_plan_and_facts(ai_orch):
    orch = ai_orch
    publish_tool(orch)
    node, _ = add_node(orch, "dev1", {"env": "dev"}, facts={"system": "linux", "systemd": True, "provides": [],
                                                             "note": "key AKIAABCDEFGHIJKLMNOP"})
    plan = _call("orch_run_propose", {"target": "demo.echo@1", "selector": {"labels": "env=dev"}, "dry_run": True})
    assert plan["ok"] and plan["data"]["nodes"] == [node.id] and orch.store.list_runs() == []
    facts = _call("orch_node_facts", {"node_id": node.id})
    assert "AKIAABCDEFGHIJKLMNOP" not in json.dumps(facts)
    listed = _call("orch_nodes_list", {"selector": "env=dev"})
    assert [n["name"] for n in listed["data"]["nodes"]] == ["dev1"]


def test_ai_allowlist_is_exactly_what_the_plan_allows():
    # runbook_editor.md §3: validate, new version, test proposals, the upgrade report and the
    # selector preview join; discarding, publishing and approving stay with the operator.
    assert AI_COMMANDS == {"createDraft", "updateDraft", "lintDraft", "submitReview", "planRun", "proposeRun",
                           "proposeTest", "saveRunbookDraft", "validateRunbook", "newRunbookVersion",
                           "proposeRunbookTest", "upgradeRunbookTools", "previewSelector"}
    assert not {"discardRunbookDraft", "publishRunbook", "approveRun", "verifyNode"} & AI_COMMANDS


# --------------------------------------------------------------------- GUI router

@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from mu.gui.app import create_app
    from mu.gui.routers import orchestration as router
    orch = make_orch(tmp_path)
    app = create_app(args=SimpleNamespace(provider=None, model=None), build_session_fn=lambda *a, **k: None)
    app.state.orchestrator = orch
    monkeypatch.setattr(router, "_CURSOR", {"seq": -1})
    c = TestClient(app)
    yield c, orch
    orch.stop()


def _token(c):
    return c.get("/api/orchestration/csrf").json()["token"]


def test_every_mutating_orchestration_route_requires_csrf(client):
    c, _orch = client
    routes = [r for r in c.app.routes if getattr(r, "path", "").startswith("/api/orchestration")
              and {"POST", "PUT", "PATCH", "DELETE"} & set(getattr(r, "methods", set()))]
    assert {r.path for r in routes} >= {"/api/orchestration/cmd", "/api/orchestration/plan",
                                        "/api/orchestration/import", "/api/orchestration/studio/session"}
    for route in routes:
        method = sorted({"POST", "PUT", "PATCH", "DELETE"} & route.methods)[0]
        r = c.request(method, route.path, json={})
        assert r.status_code == 403, route.path
        r = c.request(method, route.path, json={}, headers={"X-MuCLI-CSRF": "forged.0000"})
        assert r.status_code == 403, route.path


def test_page_and_commands_through_the_router(client):
    c, orch = client
    page = c.get("/orchestration")
    assert page.status_code == 200
    token = re.search(r'data-csrf="([^"]+)"', page.text) or re.search(r'csrf[^"]*"\s*content="([^"]+)"', page.text)
    headers = {"X-MuCLI-CSRF": _token(c)}
    r = c.post("/api/orchestration/cmd", json={"type": "installStarterPack"}, headers=headers)
    assert r.status_code == 200 and r.json()["data"]["installed"]
    r = c.post("/api/orchestration/cmd", json={"type": "nope"}, headers=headers)
    assert r.status_code == 400
    r = c.post("/api/orchestration/cmd", json={"type": "acceptNode", "args": {"node_id": "n_missing"}}, headers=headers)
    assert r.status_code == 400
    node, _ = add_node(orch, "a", {"env": "dev"})
    r = c.post("/api/orchestration/cmd", json={"type": "acceptNode", "args": {"node_id": node.id}}, headers=headers)
    assert r.status_code == 409 and "awaiting acceptance" in r.json()["detail"]
    plan = c.post("/api/orchestration/plan", json={"target": "sys.memory@1", "selector": {"labels": "env=dev"}},
                  headers=headers).json()["data"]["plan"]
    assert plan["ok"] and plan["nodes"] == [node.id]
    assert c.get(f"/api/orchestration/nodes/{node.id}").json()["name"] == "a"
    assert c.get("/api/orchestration/tools/sys.memory/1").json()["status"] == "published"
    for path in ("/api/orchestration/overview", "/api/orchestration/approvals", "/api/orchestration/settings",
                 "/api/orchestration/runs", "/api/orchestration/audit?subject=sys", "/api/orchestration/attention"):
        assert c.get(path).status_code == 200, path
    assert c.get("/api/orchestration/audit/verify").json()["ok"]
    settings = c.get("/api/orchestration/settings").json()
    assert "private_key" not in json.dumps(settings["overlay"])


def test_cross_origin_and_rebinding_are_refused(client):
    c, _orch = client
    token = _token(c)
    r = c.post("/api/orchestration/cmd", json={"type": "pauseDispatch"},
               headers={"X-MuCLI-CSRF": token, "Origin": "http://evil.example"})
    assert r.status_code == 403
    assert c.get("/orchestration", headers={"Host": "attacker.example"}).status_code == 403
    assert c.get("/api/orchestration/csrf", headers={"Host": "attacker.example"}).status_code == 403


def test_sse_streams_changes(client):
    c, orch = client
    c.get("/api/orchestration/overview")
    r = c.get("/api/orchestration/events?once=1")
    assert r.status_code == 200 and "event: hello" in r.text
    from mu.orchestration import enroll
    enroll.create_join_token(orch.store)
    r = c.get("/api/orchestration/events?once=1&after=0")
    assert '"scope": "node"' in r.text


def test_sidebar_links_the_page(client):
    c, _orch = client
    assert 'href="/orchestration"' in c.get("/orchestration").text


# --------------------------------------------------------------------- CLI

def test_cli_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    from mu.orchestration.cli import main
    assert main(["tools", "starter"]) == 0
    assert main(["tools", "list"]) == 0
    assert "sys.disk-usage@1" in capsys.readouterr().out
    assert main(["nodes", "token", "--labels", "env=dev"]) == 0
    assert "join token" in capsys.readouterr().out
    assert main(["pause", "--reason", "maintenance"]) == 0 and main(["resume"]) == 0
    assert main(["--json", "runs", "plan", "sys.memory@1", "--labels", "env=dev"]) == 0
    assert main(["audit", "verify"]) == 0
    assert "audit chain OK" in capsys.readouterr().out
    out_dir = tmp_path / "ops"
    assert main(["export", "--dir", str(out_dir)]) == 0 and (out_dir / "manifest.json").exists()
    assert main(["export", str(tmp_path / "b.mucli-orch.tar.zst")]) == 0
    assert main(["import", "--yes", str(out_dir)]) == 0
    assert main(["backup", str(tmp_path / "backup.tgz")]) == 0
    assert main(["nodes", "accept", "n_missing"]) == 1


def test_mucli_entry_point_dispatches_orch(tmp_path):
    env = dict(os.environ, MUCLI_HOME=str(tmp_path / "home"))
    out = subprocess.run([sys.executable, str(ROOT / "mucli.py"), "orch", "audit", "verify"], capture_output=True,
                         text=True, env=env, timeout=120, cwd=str(ROOT))
    assert out.returncode == 0 and "audit chain OK" in out.stdout


def test_orchestration_sessions_see_the_orch_tools_with_lazy_phases():
    import mu.tools as tools
    from mu.tools.descriptors import (filter_tools_by_phase, infer_contextual_tool_phases,
                                      resolve_active_tool_phases)
    from mu.tools.capabilities import filter_tools_for_session_type
    defs = [d.definition for d in tools.list_descriptors()]
    for session_type, expected in (("orchestration", 15), ("workspace", 0)):
        variables = {"session_type": session_type, "agent_mode": "default", "active_tool_phases": ["core"]}
        phases = resolve_active_tool_phases(variables, infer_contextual_tool_phases(SimpleNamespace(variables=variables)))
        visible = filter_tools_for_session_type(filter_tools_by_phase(defs, phases), session_type)
        assert len([t for t in visible if t.name.startswith("orch_")]) == expected
