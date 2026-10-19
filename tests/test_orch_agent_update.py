"""Node agent self-update: node-local opt-in, a signed order with its own domain tag, the root
helper's checks (digest, smoke test, ``.prev``), the ``agent_update`` run kind (exclusions with
reasons, access policy, digest freeze), the agent's install-and-restart path and the automatic
update job."""

from __future__ import annotations

import json
import os
import sys
import time

import pytest

from mu.orchestration import runs, vm
from mu.orchestration.commands import CommandError, CommandRejected, dispatch
from mu.orchestration.node import AGENT_VERSION, update as agent_update
from mu.orchestration.node.envelope import ReplayCache, verify_dispatch, verify_update
from mu.orchestration.node.policy import decide_update, parse_policy
from mu.orchestration.zipapp import DEFAULT_POLICY, build_zipapp

from orch_helpers import Clock, FakeNode, add_node, make_orch


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def orch(tmp_path, clock):
    o = make_orch(tmp_path, clock)
    yield o
    o.stop()


def cmd(orch, ctype, actor="operator", **args):
    return dispatch(orch, {"type": ctype, "args": args}, actor=actor).data


def order(orch, node_id="n_1", version=AGENT_VERSION, sha=None, **extra):
    _data, real = build_zipapp()
    body = {"kind": "agent_update", "exec_id": "x_1", "run_id": "r_1", "node_id": node_id, "version": version,
            "sha256": sha or real, "size": len(_data), "issued_at": time.time(), "deadline": time.time() + 600,
            "nonce": "n" * 24}
    body.update(extra)
    return orch.keys.sign_update(body)


# --------------------------------------------------------------------- node policy and the order

def test_updates_are_off_unless_the_node_opts_in():
    assert parse_policy("").allow_agent_updates is False
    on = parse_policy("allow_agent_updates: true\n")
    assert on.allow_agent_updates and decide_update(on, now=time.time()).allowed
    assert "allow_agent_updates" in decide_update(parse_policy(""), now=time.time()).reason
    assert not decide_update(parse_policy("bogus: 1\n"), now=time.time()).allowed      # invalid policy
    quiet = parse_policy("allow_agent_updates: true\nquiet_hours: ['Mon-Sun 00:00-23:59']\n")
    assert "quiet hours" in decide_update(quiet, now=time.time()).reason
    # The installer's template opts in, visibly, and parses cleanly.
    template = parse_policy(DEFAULT_POLICY)
    assert template.allow_agent_updates and not template.errors and "allow_agent_updates: true" in DEFAULT_POLICY


def test_update_orders_are_signed_and_cannot_cross_kinds(orch):
    key = orch.keys.signing_public
    o = order(orch)
    assert verify_update(o, node_id="n_1", signing_key=key, replay=ReplayCache()).ok
    assert "another node" in verify_update(o, node_id="n_2", signing_key=key, replay=None).reason
    assert "signature" in verify_update(dict(o, version="9.9.9"), node_id="n_1", signing_key=key, replay=None).reason
    seen = ReplayCache()
    assert verify_update(o, node_id="n_1", signing_key=key, replay=seen).ok
    assert "replayed" in verify_update(o, node_id="n_1", signing_key=key, replay=seen).reason
    # A dispatch signature never authorises an update, and an update order is not a dispatch.
    forged = orch.keys.sign_dispatch({k: v for k, v in o.items() if k != "sig"})
    assert "signature" in verify_update(forged, node_id="n_1", signing_key=key, replay=None).reason
    assert not verify_dispatch(o, node_id="n_1", signing_key=key, test_key=None, accept_test=False,
                               replay=ReplayCache()).ok


def test_root_side_install_checks_everything_and_keeps_prev(orch, tmp_path):
    data, sha = build_zipapp()
    staged = tmp_path / "staged.pyz"
    staged.write_bytes(data)
    target = tmp_path / "lib" / "mucli-node.pyz"
    target.parent.mkdir()
    target.write_bytes(b"old agent")
    on = parse_policy("allow_agent_updates: true\n")
    key = orch.keys.signing_public
    common = dict(node_id="n_1", signing_key=key, agent_path=str(target))
    ok, msg = agent_update.install(order(orch, sha="0" * 64), str(staged), policy=on, replay=ReplayCache(), **common)
    assert not ok and "digest mismatch" in msg and target.read_bytes() == b"old agent"
    ok, msg = agent_update.install(order(orch), str(staged), policy=parse_policy(""), replay=ReplayCache(), **common)
    assert not ok and "allow_agent_updates" in msg
    ok, msg = agent_update.install(order(orch, version="9.9.9"), str(staged), policy=on, replay=ReplayCache(), **common)
    assert not ok and "reports version" in msg                       # the smoke test runs the new agent
    ok, msg = agent_update.install(order(orch), str(staged), policy=on, replay=ReplayCache(), **common)
    assert ok, msg
    assert target.read_bytes() == data and (tmp_path / "lib" / "mucli-node.pyz.prev").read_bytes() == b"old agent"
    assert os.stat(target).st_mode & 0o777 == 0o755


# --------------------------------------------------------------------- the update job on the master

def _fleet(orch, monkeypatch):
    """Pretend this master ships 1.2.0; nodes report what they run."""
    monkeypatch.setattr("mu.orchestration.node.AGENT_VERSION", "1.2.0")
    allow = {"allow_agent_updates": True, "sandbox": "systemd"}
    nodes = {}
    for name, version, policy, kind in (("ready", "1.1.0", allow, "host"), ("closed", "1.1.0", {"sandbox": "systemd"}, "host"),
                                        ("old", "1.0.1", allow, "host"), ("box", "1.1.0", dict(allow, sandbox="none"), "container"),
                                        ("new", "1.2.0", allow, "host")):
        node, _ = add_node(orch, name, {"env": "dev"})
        orch.store.update_node(node.id, agent_version=version, policy=policy, kind=kind)
        nodes[name] = orch.store.get_node(node.id)
    return nodes


def test_update_job_plans_with_reasons_and_is_operator_only(orch, monkeypatch):
    nodes = _fleet(orch, monkeypatch)
    assert [n.name for n in orch.outdated_nodes()] == ["ready"]
    status = vm.node_vm(orch.store, nodes["closed"])["agent"]
    assert status["outdated"] and not status["updatable"] and "allow_agent_updates" in status["reason"]
    run = cmd(orch, "proposeAgentUpdate", nodes=[n.id for n in nodes.values()])["run"]
    assert run["kind"] == "agent_update" and run["state"] == "proposed" and run["nodes"] == [nodes["ready"].id]
    reasons = {x["name"]: x["reason"] for x in run["excluded"]}
    assert "allow_agent_updates" in reasons["closed"] and "update it once by hand" in reasons["old"]
    assert "sandbox none" in reasons["box"] and "already runs agent 1.2.0" in reasons["new"]
    assert run["decision"]["level"] == "confirm"                      # mutating: the default policy asks you
    with pytest.raises(CommandRejected, match="requires the operator"):
        cmd(orch, "proposeAgentUpdate", actor="ai:s")
    with pytest.raises(CommandError, match="proposeAgentUpdate"):
        cmd(orch, "proposeRun", kind="agent_update", target="mucli-node@0", selector={"all": True})


def test_dispatch_sends_a_signed_order_and_refuses_a_changed_agent(orch, monkeypatch):
    nodes = _fleet(orch, monkeypatch)
    fake = FakeNode(orch, nodes["ready"])
    run = cmd(orch, "proposeAgentUpdate")["run"]
    cmd(orch, "approveRun", run_id=run["id"])
    orch.tick()
    reply = fake.poll()
    [o] = reply["dispatches"]
    assert o["kind"] == "agent_update" and o["version"] == "1.2.0" and o["sha256"] == build_zipapp()[1]
    assert verify_update(o, node_id=fake.node.id, signing_key=orch.keys.signing_public, replay=None).ok
    fake.event(type="result", exec_id=o["exec_id"], result={"exec_id": o["exec_id"], "state": "succeeded",
                                                             "exit_code": 0, "output": {"from": "1.1.0", "to": "1.2.0"}})
    orch.tick()
    assert orch.store.get_run(run["id"]).state == "succeeded"
    # A master whose agent changed after approval never sends the old order.
    orch.store.update_node(nodes["ready"].id, agent_version="1.1.0",       # the fake's heartbeat overwrote these
                           policy={"allow_agent_updates": True, "sandbox": "systemd"})
    run = cmd(orch, "proposeAgentUpdate")["run"]
    cmd(orch, "approveRun", run_id=run["id"])
    monkeypatch.setattr("mu.orchestration.zipapp.build_zipapp", lambda: (b"other", "f" * 64))
    orch.tick()
    assert fake.poll()["dispatches"] == []
    [ex] = orch.store.list_executions(run_id=run["id"])
    assert ex.state == "failed" and "agent changed since this update was approved" in ex.reason


def test_automatic_updates_propose_one_job(orch, monkeypatch, clock):
    nodes = _fleet(orch, monkeypatch)
    assert orch.auto_update_agents(clock()) is None                  # off by default
    cmd(orch, "setAgentUpdates", auto=True)
    run = orch.auto_update_agents(clock())
    assert run is not None and run.proposed_by == "schedule:agent-updates" and run.nodes == [nodes["ready"].id]
    assert orch.auto_update_agents(clock()) is None                  # one open job at a time
    cmd(orch, "rejectRun", run_id=run.id)
    assert orch.auto_update_agents(clock()) is None                  # not re-proposed for a day
    clock.advance(25 * 3600)
    assert orch.auto_update_agents(clock()) is not None


# --------------------------------------------------------------------- the agent's side

class _Client:
    def __init__(self, data):
        self.data, self.events = data, []

    def get(self, path, **kw):
        assert path == "/api/orchestration/node/agent.pyz"
        return self.data

    def post(self, path, body, **kw):
        self.events += body.get("events") or []
        return {}


def test_agent_installs_restarts_and_the_new_agent_reports(orch, tmp_path, monkeypatch):
    from mu.orchestration.node import agent as agent_mod
    from mu.orchestration.node.store import NodeStore
    from mu.orchestration.node.envelope import b64e
    data, sha = build_zipapp()
    state, etc, target = tmp_path / "state", tmp_path / "etc", tmp_path / "lib" / "mucli-node.pyz"
    etc.mkdir()
    target.parent.mkdir()
    target.write_bytes(b"old")
    (etc / "policy.yaml").write_text("allow_agent_updates: true\n")
    NodeStore(str(state)).save_identity({"node_id": "n_1", "credential": "c", "master": "https://127.0.0.1:1",
                                         "fingerprint": "", "cafile": "", "signing_key": b64e(orch.keys.signing_public),
                                         "test_key": ""})
    # Stand-in for the root helper: the real install() with test paths.
    helper = tmp_path / "helper.py"
    helper.write_text(
        "import json, sys\nsys.path.insert(0, %r)\n" % os.getcwd() +
        "from mu.orchestration.node import update\nfrom mu.orchestration.node.envelope import ReplayCache, b64d\n"
        "from mu.orchestration.node.policy import parse_policy\n"
        "spec = json.load(open(sys.argv[1]))\n"
        "ok, msg = update.install(spec['update'], spec['pyz'], node_id='n_1', signing_key=b64d(%r), "
        "policy=parse_policy('allow_agent_updates: true'), replay=ReplayCache(), agent_path=%r)\n"
        "print(msg); sys.exit(0 if ok else 125)\n" % (b64e(orch.keys.signing_public), str(target)))
    monkeypatch.setattr(agent_mod, "AGENT_VERSION", "1.0.9")          # the old agent
    old = agent_mod.NodeAgent(state_dir=str(state), etc_dir=str(etc), helper=[sys.executable, str(helper)])
    old.client = _Client(data)
    old.handle_dispatch(order(orch, version=AGENT_VERSION))
    assert old.updated and target.read_bytes() == data, old.client.events
    assert [e["type"] for e in old.client.events] == ["running"]      # the result comes from the new agent
    monkeypatch.setattr(agent_mod, "AGENT_VERSION", AGENT_VERSION)    # systemd started the new one
    client = _Client(data)
    monkeypatch.setattr(agent_mod.NodeAgent, "_client", lambda self: client)
    agent_mod.NodeAgent(state_dir=str(state), etc_dir=str(etc), helper=[sys.executable, str(helper)])
    [result] = client.events
    assert result["result"]["state"] == "succeeded" and result["result"]["output"] == {"from": "1.0.9", "to": AGENT_VERSION}


def test_agent_refuses_without_opt_in_or_root_helper(orch, tmp_path):
    from mu.orchestration.node import agent as agent_mod
    from mu.orchestration.node.store import NodeStore
    from mu.orchestration.node.envelope import b64e
    state, etc = tmp_path / "state", tmp_path / "etc"
    etc.mkdir()
    NodeStore(str(state)).save_identity({"node_id": "n_1", "credential": "c", "master": "https://127.0.0.1:1",
                                         "fingerprint": "", "cafile": "", "signing_key": b64e(orch.keys.signing_public),
                                         "test_key": ""})
    a = agent_mod.NodeAgent(state_dir=str(state), etc_dir=str(etc))
    a.client = _Client(b"")
    a.handle_dispatch(order(orch, version="9.9.9", nonce="a" * 24))
    assert "allow_agent_updates" in a.client.events[-1]["result"]["reason"]
    (etc / "policy.yaml").write_text("allow_agent_updates: true\n")
    b = agent_mod.NodeAgent(state_dir=str(state), etc_dir=str(etc), sandbox="none")
    b.client = _Client(b"")
    b.handle_dispatch(order(orch, version="9.9.9", nonce="b" * 24))
    assert "sandbox none" in b.client.events[-1]["result"]["reason"] and not b.updated
