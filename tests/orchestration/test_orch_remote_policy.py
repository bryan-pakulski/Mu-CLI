"""Editing a node's policy.yaml from the master: operator only (never the AI), audited, no
approval step. The master signs a policy order; the node accepts it only when its policy in force
allows remote edits, checks it again in the root helper, refuses invalid or tampered text and
replays, and installs it atomically with the previous file kept."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mu.orchestration import commands, node_policy
from mu.orchestration.commands import CommandError, CommandRejected
from mu.orchestration.node import remote_policy
from mu.orchestration.node.envelope import ReplayCache
from mu.orchestration.node.policy import parse_policy, remote_policy_allowed
from mu.orchestration.zipapp import DEFAULT_POLICY

from orch_helpers import add_node, make_orch

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def setup(tmp_path):
    orch = make_orch(tmp_path / "master")
    node, _ = add_node(orch, "web-1", {"env": "prod"})
    etc = tmp_path / "etc"
    etc.mkdir()
    return orch, node, etc


def _write(etc, text):
    (etc / "policy.yaml").write_text(text)


def _summary(etc):
    text = remote_policy.current_text(str(etc))
    parsed = parse_policy(text)
    return dict(parsed.summary(), sandbox="systemd", text=text, sha256=remote_policy.text_digest(text),
                remote_policy=remote_policy_allowed(parsed))


def _beat(orch, node, etc, **extra):
    return orch.hub.heartbeat(orch.store.get_node(node.id), dict({"policy": _summary(etc)}, **extra))


def cmd(orch, ctype, actor="operator", **args):
    return commands.dispatch(orch, {"type": ctype, "args": args}, actor=actor).data


def test_who_may_edit_remotely_follows_the_policy_in_force():
    assert remote_policy_allowed(parse_policy(DEFAULT_POLICY))                    # new installs: explicit opt-in
    assert remote_policy_allowed(parse_policy("allow_agent_updates: true\n"))     # unset follows agent updates
    assert not remote_policy_allowed(parse_policy("allow_agent_updates: false\n"))
    assert not remote_policy_allowed(parse_policy("allow_agent_updates: true\nallow_remote_policy: false\n"))
    assert not remote_policy_allowed(parse_policy("max_effect: nonsense\nallow_remote_policy: true\n"))  # invalid


def test_edit_round_trip_is_signed_rechecked_and_atomic(setup):
    orch, node, etc = setup
    _write(etc, "max_effect: read_only\nallow_agent_updates: true\n")
    assert "policy_update" not in _beat(orch, node, etc)
    new = "max_effect: mutating\nallow_agent_updates: true\n"
    check = cmd(orch, "validateNodePolicy", node_id=node.id, text=new)
    assert check["ok"] and check["changes"] == [{"key": "max_effect", "from": "read_only", "to": "mutating"}]
    cmd(orch, "setNodePolicy", node_id=node.id, text=new)
    order = _beat(orch, node, etc)["policy_update"]
    assert order["kind"] == "policy_update" and order["sha256"] == node_policy.digest(new)
    replay = ReplayCache()
    ok, message = remote_policy.apply(order, etc_dir=str(etc), node_id=node.id,
                                      signing_key=orch.keys.signing_public, replay=replay)
    assert ok, message
    assert (etc / "policy.yaml").read_text() == new
    assert (etc / "policy.yaml.prev").read_text().startswith("max_effect: read_only")
    assert remote_policy.apply(order, etc_dir=str(etc), node_id=node.id, signing_key=orch.keys.signing_public,
                               replay=replay)[1].startswith("replayed")
    reply = _beat(orch, node, etc, policy_result={"nonce": order["nonce"], "ok": True})
    assert "policy_update" not in reply and node_policy.pending(orch.store, node.id)["state"] == "applied"
    actions = [e["action"] for e in orch.store.audit_entries(limit=200)]
    assert "node.policy.set" in actions and "node.policy.applied" in actions


def test_nodes_refuse_what_they_should(setup):
    orch, node, etc = setup
    _write(etc, "allow_agent_updates: true\n")
    _beat(orch, node, etc)
    cmd(orch, "setNodePolicy", node_id=node.id, text="max_effect: mutating\nallow_agent_updates: true\n")
    order = _beat(orch, node, etc)["policy_update"]
    key = orch.keys.signing_public
    for bad, why in ((dict(order, text="max_effect: destructive\n"), "bad policy signature"),
                     (dict(order, node_id="n_other"), "another node"),
                     (dict(order, sig="AAAA"), "bad policy signature")):
        ok, reason = remote_policy.check(bad, etc_dir=str(etc), node_id=node.id, signing_key=key, replay=None)
        assert not ok and why in reason, reason
    # The owner turned remote edits off on the node after the order was signed: refused there.
    _write(etc, "allow_agent_updates: true\nallow_remote_policy: false\n")
    ok, reason = remote_policy.check(order, etc_dir=str(etc), node_id=node.id, signing_key=key, replay=None)
    assert not ok and "keeps its policy local" in reason
    reply = _beat(orch, node, etc, policy_result={"nonce": order["nonce"], "ok": False, "reason": reason})
    assert "policy_update" not in reply and node_policy.pending(orch.store, node.id)["state"] == "refused"


def test_the_master_refuses_invalid_unchanged_and_unwelcome_edits(setup):
    orch, node, etc = setup
    _write(etc, "allow_agent_updates: false\n")
    _beat(orch, node, etc)
    with pytest.raises(CommandError, match="keeps its policy local"):
        cmd(orch, "setNodePolicy", node_id=node.id, text="max_effect: mutating\n")
    _write(etc, "allow_remote_policy: true\n")
    _beat(orch, node, etc)
    with pytest.raises(CommandError, match="invalid policy"):
        cmd(orch, "setNodePolicy", node_id=node.id, text="max_effect: everything\n")
    with pytest.raises(CommandError, match="already the node's policy"):
        cmd(orch, "setNodePolicy", node_id=node.id, text="allow_remote_policy: true\n")
    warn = cmd(orch, "validateNodePolicy", node_id=node.id, text="allow_remote_policy: false\n")
    assert any("keeps its policy local" in w for w in warn["warnings"])
    cmd(orch, "setNodePolicy", node_id=node.id, text="allow_remote_policy: true\nmax_effect: mutating\n")
    cmd(orch, "cancelNodePolicy", node_id=node.id)
    assert "policy_update" not in _beat(orch, node, etc)
    # An agent that predates remote edits (no text/sha256 in its report) is never sent one.
    old, _ = add_node(orch, "old-1", {"env": "prod"})
    orch.hub.heartbeat(orch.store.get_node(old.id), {"policy": parse_policy("allow_agent_updates: true\n").summary()})
    with pytest.raises(CommandError, match="predates remote policy"):
        cmd(orch, "setNodePolicy", node_id=old.id, text="max_effect: mutating\n")


def test_the_ai_can_never_edit_policy(setup):
    orch, node, etc = setup
    _write(etc, "allow_remote_policy: true\n")
    _beat(orch, node, etc)
    for ctype in ("setNodePolicy", "cancelNodePolicy"):
        with pytest.raises(CommandRejected):
            cmd(orch, ctype, actor="ai:chat", node_id=node.id, text="max_effect: destructive\n")
    assert "setNodePolicy" not in commands.AI_COMMANDS and "validateNodePolicy" not in commands.AI_COMMANDS

