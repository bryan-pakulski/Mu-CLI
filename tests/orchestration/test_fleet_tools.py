"""Fleet results from every part of μcli: read-only fleet_* tools for any interactive session,
`/orch results` in the terminal, and "Ask about this" handing a results table to Chat."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from mu.orchestration import attention, fleet_tools

from orch_helpers import FakeNode, add_node, make_orch, publish_tool, tool_manifest

ROOT = Path(__file__).resolve().parents[2]
DISK = {"type": "object", "properties": {
    "disks": {"type": "array", "items": {"type": "object", "properties": {
        "mount": {"type": "string"}, "used_pct": {"type": "number"}}}},
    "max_used_pct": {"type": ["number", "null"]}}}


def _call(name, args=None):
    from mu.tools.descriptors import TOOL_DESCRIPTORS  # noqa: F401 — registry import
    import mu.tools  # noqa: F401
    from mu.tools._dispatcher import execute_tool  # noqa: F401
    handler = getattr(fleet_tools, name)
    return handler(args or {}, SimpleNamespace(session=None, variables={}))


def test_every_orchestration_and_fleet_tool_registers():
    # A registration error in the package is swallowed (logged) at import, which would silently
    # remove every orch_* tool; this pins that they all register.
    import mu.tools  # noqa: F401
    from mu.tools.capabilities import FLEET_TOOLS, ORCHESTRATION_TOOLS
    from mu.tools.descriptors import TOOL_DESCRIPTORS
    for name in sorted(n for n in ORCHESTRATION_TOOLS if n.startswith(("orch_", "fleet_"))):
        assert name in TOOL_DESCRIPTORS, name
    for name in FLEET_TOOLS:
        desc = TOOL_DESCRIPTORS[name]
        assert desc.phase == "fleet" and desc.execution_kind == "read" and not desc.definition.requires_approval


def test_fleet_tools_reach_every_session_type_but_orch_tools_do_not():
    from mu.tools.capabilities import is_tool_allowed
    for session_type in ("chat", "workspace", "container", "orchestration"):
        assert is_tool_allowed("fleet_results", session_type) and is_tool_allowed("fleet_overview", session_type)
    for session_type in ("chat", "workspace", "container"):
        assert not is_tool_allowed("orch_run_propose", session_type)
        assert not is_tool_allowed("orch_run_results", session_type)


def test_unused_orchestration_is_never_created(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    attention._cache.clear()
    out = _call("fleet_results", {"tool": "sys.disk-usage"})
    assert not out["ok"] and out["error_code"] == "not_set_up"
    assert not _call("fleet_overview")["ok"]
    assert not (tmp_path / "home" / "orchestration").exists()
    # And the fleet phase stays off: no fleet schemas in a session on this machine.
    from mu.tools.descriptors import infer_contextual_tool_phases
    assert "fleet" not in infer_contextual_tool_phases(SimpleNamespace(variables={"session_type": "workspace"}))


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    attention._cache.clear()
    orch = make_orch(tmp_path / "home" / "orchestration")
    monkeypatch.setattr("mu.orchestration.agent_tools._orch", lambda: orch)
    monkeypatch.setattr("mu.orchestration.attention.peek", lambda *a, **k: {"total": 0})
    publish_tool(orch, tool_manifest("sys.disk", outputs=DISK))
    fakes = [FakeNode(orch, add_node(orch, n, {"env": "dev"})[0]) for n in ("web-1", "db-1", "cache-1")]
    use = {"web-1": 41.0, "db-1": 93.5, "cache-1": 77.0}
    run = orch.create_run(actor="operator", kind="tool", target="sys.disk@1", selector={"labels": "env=dev"})
    for _ in range(20):
        orch.tick()
        for fake in fakes:
            for env in fake.poll()["dispatches"]:
                pct = use[fake.node.name]
                fake.finish(env, output={"disks": [{"mount": "/", "used_pct": pct}, {"mount": "/var", "used_pct": pct / 2}],
                                         "max_used_pct": pct})
        orch.tick()
        if orch.store.get_run(run.id).state in ("succeeded", "failed", "paused"):
            break
    return orch, run


def test_fleet_results_sort_filter_and_views(fleet):
    orch, run = fleet
    out = _call("fleet_results", {"tool": "sys.disk"})
    assert out["ok"], out
    data = out["data"]
    assert data["columns"][:2] == ["node", "state"] and data["views"] == ["summary", "disks"]
    assert [r[0] for r in data["rows"]] == ["db-1", "cache-1", "web-1"]          # fullest first
    over = _call("fleet_results", {"tool": "sys.disk", "view": "disks", "where": ["Used % > 40"]})["data"]
    assert sorted((r[0], r[2]) for r in over["rows"]) == [("cache-1", "/"), ("db-1", "/"), ("db-1", "/var"), ("web-1", "/")]
    assert over["units"]["Used %"] == "percent" and over["total"] == 6
    one = _call("fleet_results", {"tool": "sys.disk", "where": ["node ~ cache"], "limit": 1})["data"]
    assert one["rows"][0][0] == "cache-1" and one["matched"] == 1
    by_run = _call("fleet_results", {"run_id": run.id, "view": "disks", "sort": "mount", "descending": False})["data"]
    assert by_run["run_id"] == run.id and by_run["rows"][0][2] == "/"
    bad = _call("fleet_results", {"tool": "sys.disk", "where": ["nonsense"]})["data"]
    assert "notes" in bad and bad["matched"] == 3
    assert not _call("fleet_results", {})["ok"]
    assert _call("fleet_results", {"tool": "nope"})["error_code"] == "not_found"


def test_fleet_overview_lists_nodes_runs_and_tools_with_results(fleet):
    out = _call("fleet_overview")
    assert out["ok"], out
    data = out["data"]
    assert {n["name"] for n in data["nodes"]} == {"web-1", "db-1", "cache-1"}
    assert data["tools_with_results"][0]["tool"] == "sys.disk" and data["recent_runs"]
    assert set(data["needs_you"]) == {"approvals", "paused_runs", "reviews", "pending_nodes", "offline_nodes"}


def test_fleet_overview_reports_groups_and_health_read_only(fleet):
    from mu.orchestration.commands import dispatch
    orch, _ = fleet
    db = next(n for n in orch.store.list_nodes() if n.name == "db-1")
    dispatch(orch, {"type": "putGroup", "args": {"name": "data", "members": [db.id]}}, actor="operator")
    data = _call("fleet_overview")["data"]
    by_name = {n["name"]: n for n in data["nodes"]}
    assert by_name["db-1"]["groups"] == ["data"] and by_name["web-1"]["groups"] == []
    assert by_name["db-1"]["health"] in ("ok", "warning", "critical") and isinstance(by_name["db-1"]["health_reasons"], list)
    assert [g["name"] for g in data["groups"]] == ["data"] and data["groups"][0]["nodes"] == 1
    assert data["ungrouped"]["nodes"] == 2


def test_terminal_orch_results(fleet, monkeypatch):
    orch, run = fleet
    monkeypatch.setattr("mu.commands.orch._orch", lambda: orch)
    monkeypatch.setattr("mu.orchestration.attention.peek", lambda *a, **k: {"total": 0})
    from mu.commands import dispatch

    class UI:
        info, errors = [], []
        def show_info(self, t): self.info.append(str(t))
        def show_error(self, t): self.errors.append(str(t))
    ui = UI()
    result = dispatch(SimpleNamespace(ui=ui), "/orch results sys.disk disks")
    assert result.ok, ui.errors
    text = ui.info[-1]
    assert "Disks" in text and "db-1" in text and "93.5%" in text and "Other views: summary" in text
    result = dispatch(SimpleNamespace(ui=ui), f"/orch results {run.id}")
    assert result.ok and "db-1" in ui.info[-1]

