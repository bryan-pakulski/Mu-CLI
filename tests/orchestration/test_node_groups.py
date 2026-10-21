"""Node groups and the node dashboard: a node belongs to a group when it is a member (dragged in)
or the group's selector matches its labels; runs target a group; the dashboard reports health
for the fleet, a group or the ungrouped nodes; group changes are operator-only (never the AI)."""
from __future__ import annotations

import pytest

from orch_helpers import add_node, make_orch

from mu.orchestration import runs, vm
from mu.orchestration.commands import CommandError, CommandRejected, dispatch
from mu.orchestration.node import AGENT_VERSION


def cmd(orch, ctype, actor="operator", **args):
    return dispatch(orch, {"type": ctype, "args": args}, actor=actor).data


@pytest.fixture
def orch(tmp_path):
    o = make_orch(tmp_path)
    yield o
    o.stop()


def healthy(orch, node, **facts):
    base = {"memory": {"total_bytes": 8 * 2**30, "used_pct": 40}, "disks": [{"mount": "/", "used_pct": 30}],
            "uptime": {"uptime_s": 86400, "load": [0.2]}, "cpu_count": 2, "os": {"pretty_name": "Debian 12"}}
    base.update(facts)
    orch.store.update_node(node.id, agent_version=AGENT_VERSION, facts=base, facts_at=orch.store.now(),
                           last_seen=orch.store.now())
    return orch.store.get_node(node.id)


def test_members_and_selectors_both_put_nodes_in_a_group(orch):
    web1, _ = add_node(orch, "web-1", {"role": "web"})
    web2, _ = add_node(orch, "web-2", {"role": "web"})
    db, _ = add_node(orch, "db-1", {"role": "db"})
    cmd(orch, "putGroup", name="web", selector="role=web")
    cmd(orch, "putGroup", name="prod")                                   # members only
    assert runs.node_groups(orch.store, web1) == ["web"]
    assert runs.node_groups(orch.store, db) == []                         # empty selector matches nothing
    cmd(orch, "addToGroup", name="prod", node_ids=[db.id, web1.id])
    assert runs.node_groups(orch.store, orch.store.get_node(db.id)) == ["prod"]
    assert sorted(runs.node_groups(orch.store, orch.store.get_node(web1.id))) == ["prod", "web"]
    # Dragging from one group to another moves the node.
    cmd(orch, "putGroup", name="staging")
    cmd(orch, "addToGroup", name="staging", node_ids=[db.id], **{"from": "prod"})
    assert runs.node_groups(orch.store, orch.store.get_node(db.id)) == ["staging"]
    # Removing a member the selector matches leaves it in (and says so).
    out = cmd(orch, "removeFromGroup", name="web", node_ids=[web2.id])
    assert out["still_matched_by_selector"] == [web2.id]


def test_runs_target_only_the_chosen_group(orch):
    a, _ = add_node(orch, "a")
    b, _ = add_node(orch, "b")
    cmd(orch, "putGroup", name="batch-1", members=[a.id])
    nodes, err = runs.resolve_selector(orch.store, {"groups": ["batch-1"]})
    assert err is None and [n.id for n in nodes] == [a.id]
    _, err = runs.resolve_selector(orch.store, {"groups": ["nope"]})
    assert "unknown group" in err


def test_rename_and_delete_keep_membership_consistent(orch):
    a, _ = add_node(orch, "a")
    cmd(orch, "putGroup", name="old", members=[a.id])
    cmd(orch, "renameGroup", name="old", new_name="new")
    assert runs.node_groups(orch.store, a) == ["new"]
    with pytest.raises(CommandError):
        cmd(orch, "putGroup", name="bad name!")
    with pytest.raises(CommandError):
        cmd(orch, "addToGroup", name="new", node_ids=["no-such-node"])
    cmd(orch, "deleteGroup", name="new")
    cmd(orch, "putGroup", name="new")                                     # re-created: no stale members
    assert runs.node_groups(orch.store, a) == []


def test_the_ai_can_never_change_groups(orch):
    a, _ = add_node(orch, "a")
    for ctype, args in (("putGroup", {"name": "x"}), ("addToGroup", {"name": "x", "node_ids": [a.id]}),
                        ("removeFromGroup", {"name": "x", "node_ids": [a.id]}), ("renameGroup", {"name": "x", "new_name": "y"}),
                        ("deleteGroup", {"name": "x"})):
        with pytest.raises(CommandRejected):
            cmd(orch, ctype, actor="ai:fleet-chat", **args)


def test_dashboard_reports_health_with_reasons_for_fleet_group_and_ungrouped(orch):
    ok = healthy(orch, add_node(orch, "ok-1")[0])
    full = healthy(orch, add_node(orch, "full-1")[0], disks=[{"mount": "/", "used_pct": 97}])
    warm = healthy(orch, add_node(orch, "warm-1")[0], memory={"total_bytes": 2**30, "used_pct": 91},
                   reboot={"required": True, "reasons": ["kernel"]})
    add_node(orch, "new-1", status="pending")
    cmd(orch, "putGroup", name="prod", members=[full.id, warm.id])

    fleet = vm.fleet_dashboard(orch.store)
    assert fleet["totals"]["health"] == {"critical": 1, "warning": 1, "pending": 1, "ok": 1}
    assert [n["name"] for n in fleet["nodes"]][:2] == ["full-1", "warm-1"]         # worst first
    by_name = {n["name"]: n for n in fleet["nodes"]}
    assert by_name["full-1"]["health"]["reasons"] == ["disk 97% full"]
    assert set(by_name["warm-1"]["health"]["reasons"]) == {"memory 91% used", "needs a reboot"}
    assert by_name["ok-1"]["health"]["style"]["label"] == "healthy"
    assert fleet["resources"]["disk_used_max"] == 97

    prod = vm.fleet_dashboard(orch.store, "prod")
    assert prod["scope"]["label"] == "prod" and {n["name"] for n in prod["nodes"]} == {"full-1", "warm-1"}
    assert {g["name"]: g["health"]["critical"] for g in prod["groups"]} == {"prod": 1}   # list shows every group
    ungrouped = vm.fleet_dashboard(orch.store, "-")
    assert {n["name"] for n in ungrouped["nodes"]} == {"ok-1", "new-1"}
    assert fleet["ungrouped"]["nodes"] == 2
    with pytest.raises(KeyError):
        vm.fleet_dashboard(orch.store, "nope")
    assert ok.id in {n["id"] for n in fleet["nodes"]}


def test_offline_nodes_are_critical(orch):
    from mu.orchestration.attention import OFFLINE_AFTER_S
    node = healthy(orch, add_node(orch, "gone")[0])
    orch.store.set_setting("master_started_at", orch.store.now() - 10 * OFFLINE_AFTER_S)   # past the connect grace
    orch.store.update_node(node.id, last_seen=orch.store.now() - 2 * OFFLINE_AFTER_S)
    level = vm.node_health(orch.store, orch.store.get_node(node.id), orch.store.now())
    assert level["level"] == "critical" and level["reasons"][0].startswith("offline")


def test_the_terminal_manages_groups_and_shows_the_dashboard(orch, tmp_path, monkeypatch, capsys):
    from mu.orchestration import cli
    a = healthy(orch, add_node(orch, "web-1")[0])
    monkeypatch.setattr("mu.orchestration.service.Orchestrator", lambda *a, **k: orch)
    monkeypatch.setattr(orch, "stop", lambda: None, raising=False)
    assert cli.main(["groups", "create", "web", "--description", "front ends"]) == 0
    assert cli.main(["groups", "add", "web", a.id]) == 0
    assert runs.node_groups(orch.store, a) == ["web"]
    capsys.readouterr()
    assert cli.main(["groups", "list"]) == 0
    listing = capsys.readouterr().out
    assert "web" in listing and "front ends" in listing and "(ungrouped)" in listing
    assert cli.main(["dashboard", "--group", "web"]) == 0
    board = capsys.readouterr().out
    assert board.startswith("web: 1 node(s)") and "web-1" in board and "healthy" in board
    assert cli.main(["--json", "dashboard"]) == 0
    import json
    assert json.loads(capsys.readouterr().out)["totals"]["nodes"] == 1
    assert cli.main(["groups", "rename", "web", "frontend"]) == 0
    assert runs.node_groups(orch.store, a) == ["frontend"]
