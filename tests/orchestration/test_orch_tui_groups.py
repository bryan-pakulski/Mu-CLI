"""The terminal's node dashboard and groups (/orch dashboard, /orch groups, /orch group …, and the
launcher's Orchestration menu): the same health and membership as the web and the phone, every
change through dispatch as the operator."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from orch_helpers import add_node, make_orch

from mu.commands import orch as tui
from mu.orchestration import runs
from mu.orchestration.node import AGENT_VERSION


@pytest.fixture
def orch(tmp_path, monkeypatch):
    o = make_orch(tmp_path)
    monkeypatch.setattr(tui, "_orch", lambda: o)
    monkeypatch.setattr("mu.orchestration.attention.peek", lambda *a, **k: {"total": 0, "dispatch_paused": False})
    yield o
    o.stop()


def slash(args: str, *, prompt: bool = False):
    result = tui.orch_cmd(SimpleNamespace(ui=None), args, allow_prompt=prompt)
    return result, (result.data or {}).get("text", "") if result.ok else result.message


def healthy(orch, node, **facts):
    base = {"memory": {"total_bytes": 8 * 2**30, "used_pct": 40}, "disks": [{"mount": "/", "used_pct": 30}],
            "uptime": {"uptime_s": 86400, "load": [0.2]}, "cpu_count": 2}
    base.update(facts)
    orch.store.update_node(node.id, agent_version=AGENT_VERSION, facts=base, facts_at=orch.store.now(),
                           last_seen=orch.store.now())
    return orch.store.get_node(node.id)


def test_group_commands_manage_membership_by_name_or_id(orch):
    web = healthy(orch, add_node(orch, "web-1")[0])
    db = healthy(orch, add_node(orch, "db-1")[0])
    ok, text = slash('group create prod --description "live traffic"')
    assert ok.ok and "created" in text
    ok, text = slash(f"group add prod web-1 {db.id}")
    assert ok.ok and "2 node(s) in prod" in text
    assert runs.node_groups(orch.store, web) == ["prod"]
    slash("group create staging")
    ok, text = slash("group add staging db-1 --from prod")                  # a move
    assert ok.ok and "moved out of prod" in text
    assert runs.node_groups(orch.store, orch.store.get_node(db.id)) == ["staging"]
    ok, text = slash("group remove staging db-1")
    assert ok.ok and runs.node_groups(orch.store, orch.store.get_node(db.id)) == []
    ok, text = slash("group rename prod production")
    assert ok.ok and runs.node_groups(orch.store, orch.store.get_node(web.id)) == ["production"]
    ok, text = slash("group delete staging")
    assert ok.ok and orch.store.get_group("staging") is None


def test_group_errors_are_clear(orch):
    add_node(orch, "web-1")
    for args, needle in (("group", "usage"), ("group add nope web-1", "unknown group"),
                         ("group create x --bogus 1", "unknown option"), ("group add x", "usage"),
                         ("group create prod --selector", "needs a value"), ("group frob x", "unknown group action")):
        result, message = slash(args)
        assert not result.ok and needle in message, (args, message)
    slash("group create prod")
    result, message = slash("group add prod ghost")
    assert not result.ok and "unknown node" in message


def test_a_selector_member_stays_when_removed(orch):
    web = healthy(orch, add_node(orch, "web-1", {"role": "web"})[0])
    slash('group create web --selector "role=web"')
    result, text = slash("group remove web web-1")
    assert result.ok and f"{web.id} stays in web" in text


def test_dashboard_shows_health_worst_first_and_the_groups(orch):
    healthy(orch, add_node(orch, "calm-1")[0])
    full = healthy(orch, add_node(orch, "full-1")[0], disks=[{"mount": "/", "used_pct": 97}])
    slash("group create prod")
    slash(f"group add prod {full.id}")
    result, text = slash("dashboard")
    assert result.ok
    lines = text.splitlines()
    assert lines[0].startswith("All nodes · 2 node(s)")
    assert "Health: 1 critical" in text
    nodes = [l for l in lines if "full-1" in l or "calm-1" in l]
    assert "full-1" in nodes[0] and "disk 97% full" in nodes[0]                 # worst first, with why
    assert "critical" in nodes[0] and "healthy" in nodes[1]
    assert any(l.strip().startswith("prod") and "1 critical" in l for l in lines)
    assert any("(ungrouped)" in l for l in lines)

    result, text = slash("dashboard prod")
    assert result.ok and text.startswith("prod · 1 node(s)") and "calm-1" not in text and "Groups —" not in text
    result, text = slash("dashboard -")
    assert result.ok and text.startswith("Ungrouped · 1 node(s)") and "calm-1" in text
    result, message = slash("dashboard nope")
    assert not result.ok and "unknown group" in message


def test_groups_lists_counts_and_ungrouped(orch):
    healthy(orch, add_node(orch, "a")[0])
    result, text = slash("groups")
    assert result.ok and "No groups yet" in text and "(ungrouped)" in text
    slash('group create web --selector "role=web" --description "front ends"')
    result, text = slash("groups")
    assert "web" in text and "role=web" in text and "front ends" in text


def test_the_launcher_menu_reaches_the_dashboard_and_manages_groups(orch, monkeypatch):
    node = healthy(orch, add_node(orch, "web-1")[0])
    printed = []
    monkeypatch.setattr(tui, "_print", lambda lines: printed.extend(lines))
    answers = iter([
        "groups", "__create__",                       # Orchestration → Groups → + Create group
        "add", node.id, "dashboard", "__back__",      # the new group: add a node, its dashboard, back
        "__back__",                                   # groups → back
        "dashboard", "g:prod",                        # Orchestration → Dashboard → prod
        "__back__",                                   # leave the menu
    ])
    picks = []

    def choose(title, options, default=None, **kw):
        value = next(answers)
        picks.append((title, value))
        assert value in [o[0] for o in options], (title, value, [o[0] for o in options])
        return value

    asked = iter(["prod", "", "the live fleet"])
    monkeypatch.setattr("mu.ui.choice_picker.prompt_choice", choose)
    monkeypatch.setattr("mu.ui.choice_picker.prompt_confirm", lambda *a, **k: True)
    monkeypatch.setattr("rich.prompt.Prompt.ask", lambda *a, **k: next(asked))
    tui.run_orchestration_menu()
    assert orch.store.get_group("prod")["description"] == "the live fleet"
    assert runs.node_groups(orch.store, orch.store.get_node(node.id)) == ["prod"]
    assert any(str(l).startswith("prod · 1 node(s)") for l in printed)


def test_the_node_menu_adds_to_and_removes_from_groups(orch, monkeypatch):
    node = healthy(orch, add_node(orch, "web-1")[0])
    slash("group create prod")
    monkeypatch.setattr(tui, "_print", lambda lines: None)
    answers = iter(["__group_add__", "prod", "__group_remove__", "prod"])
    monkeypatch.setattr("mu.ui.choice_picker.prompt_choice", lambda title, options, **kw: next(answers))
    tui._node_menu(orch, node.id)
    assert runs.node_groups(orch.store, orch.store.get_node(node.id)) == ["prod"]
    tui._node_menu(orch, node.id)
    assert runs.node_groups(orch.store, orch.store.get_node(node.id)) == []
