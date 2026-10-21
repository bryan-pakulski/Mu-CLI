"""Master and real node agents over HTTPS long-poll on loopback (plan §11 integration, P0/P1).

Three ``NodeAgent``s enrol with a pinned TLS fingerprint, are accepted, run a signed tool and
report results. Tampered blobs, bad signatures, replays, out-of-policy tools and invalid
parameters are each refused *on the node*.
"""

from __future__ import annotations

import copy
import os
import socket
import time

import pytest

from mu.orchestration import enroll
from mu.orchestration.node.agent import NodeAgent, enroll as node_enroll
from mu.orchestration.node.transport import AuthRejected, TransportError
from mu.orchestration.server import NodeEndpoint

from orch_helpers import make_orch, publish_tool, tool_manifest


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def master(tmp_path):
    orch = make_orch(tmp_path, clock=time.time)
    port = _free_port()
    orch.store.set_setting("network", {"bind": "127.0.0.1", "port": port})
    endpoint = NodeEndpoint(orch)
    status = endpoint.start("127.0.0.1", port)
    assert status["running"], status
    yield orch, endpoint, status
    endpoint.stop()
    orch.stop()


def _agent(orch, status, tmp_path, name, *, labels=None, policy="canary: true\nmax_effect: destructive\n",
           accept=True):
    base = tmp_path / name
    (base / "etc").mkdir(parents=True)
    (base / "etc" / "policy.yaml").write_text(policy)
    token = enroll.create_join_token(orch.store, labels=labels or {"env": "dev"})
    identity = node_enroll(master=status["url"], token=token["token"], fingerprint=status["fingerprint"],
                           state_dir=str(base / "state"), etc_dir=str(base / "etc"), name=name)
    if accept:
        enroll.set_status(orch.store, identity["node_id"], "active", actor="operator")
    agent = NodeAgent(state_dir=str(base / "state"), etc_dir=str(base / "etc"), sandbox="none", poll_wait_s=0.5,
                      log=lambda msg: None)
    return agent


def _cycle(orch, agents, rounds=8):
    for _ in range(rounds):
        orch.tick()
        for agent in agents:
            agent.poll_once(wait_s=0.2)
            agent.wait_idle(20)
            agent.flush_events()
        orch.tick()


def test_enrol_three_nodes_and_run_a_signed_tool(master, tmp_path):
    orch, _endpoint, status = master
    publish_tool(orch)
    agents = [_agent(orch, status, tmp_path, f"node{i}") for i in range(3)]
    assert {n.status for n in orch.store.list_nodes()} == {"active"}
    run = orch.create_run(actor="operator", kind="tool", target="demo.echo@1", selector={"labels": "env=dev"},
                          params={"message": "over https"}, strategy={"canary": 1, "batch": 2})
    _cycle(orch, agents)
    run = orch.store.get_run(run.id)
    assert run.state == "succeeded", (run.reason, [e.reason for e in orch.store.list_executions(run_id=run.id)])
    outputs = [e.output for e in orch.store.list_executions(run_id=run.id)]
    assert outputs == [{"message": "over https"}] * 3
    for node in orch.store.list_nodes():
        assert node.facts.get("system") == "linux" and node.policy.get("sandbox") == "none"
        # History charts: the real agent put utilisation samples on its polls.
        samples = orch.store.list_metrics([node.id], 0, orch.store.now() + 1, 0)
        assert samples and "mem_pct" in samples[0][2] and 0 <= samples[0][2]["mem_pct"] <= 100


def test_pending_nodes_get_no_work_and_revoked_nodes_are_refused(master, tmp_path):
    orch, _endpoint, status = master
    publish_tool(orch)
    agent = _agent(orch, status, tmp_path, "pending1", accept=False)
    reply = agent.poll_once(wait_s=0)
    assert reply["status"] == "pending" and reply["dispatches"] == []
    enroll.set_status(orch.store, agent.node_id, "revoked", actor="operator")
    with pytest.raises(AuthRejected):
        agent.poll_once(wait_s=0)


def test_wrong_fingerprint_refuses_to_enrol(master, tmp_path):
    orch, _endpoint, status = master
    token = enroll.create_join_token(orch.store)
    with pytest.raises(TransportError, match="fingerprint"):
        node_enroll(master=status["url"], token=token["token"], fingerprint="SHA256:" + "0" * 64,
                    state_dir=str(tmp_path / "s"), etc_dir=str(tmp_path / "e"))
    # The token was not consumed.
    with orch.store.conn() as conn:
        assert conn.execute("SELECT used_at FROM join_tokens").fetchone()["used_at"] is None


def test_plain_http_is_refused_to_non_loopback():
    from mu.orchestration.node.transport import MasterClient
    with pytest.raises(TransportError):
        MasterClient("http://10.0.0.5:30312")


def test_on_node_refusals(master, tmp_path):
    """A tampered blob, bad signature, replay, out-of-policy tool and invalid params: all refused on the node."""
    orch, _endpoint, status = master
    publish_tool(orch)
    publish_tool(orch, tool_manifest("demo.change", effect="mutating"))
    agent = _agent(orch, status, tmp_path, "victim", policy="max_effect: read_only\n")
    run = orch.create_run(actor="operator", kind="tool", target="demo.echo@1", selector={"all": True})
    orch.tick()
    reply = agent.client.post("/api/orchestration/node/poll", {"wait_s": 0, "running": []})
    envelope = reply["dispatches"][0]
    results = {}

    def refused_reason(env):
        agent.handle_dispatch(env)
        agent.wait_idle(20)
        agent.flush_events()
        return orch.store.get_execution(env["exec_id"])

    # 1. Invalid params: re-sign a dispatch with bad params as a compromised master would — the node
    #    validates against the manifest schema locally.
    bad_params = orch.keys.sign_dispatch(dict(copy.deepcopy(envelope), params={"message": 42}, nonce="p" * 32))
    agent.handle_dispatch(bad_params)
    agent.flush_events()
    results["params"] = orch.store.get_execution(envelope["exec_id"])
    assert results["params"].state == "refused" and "invalid parameters" in results["params"].reason

    # The remaining checks need fresh executions.
    def fresh(target="demo.echo@1"):
        r = orch.create_run(actor="operator", kind="tool", target=target, selector={"all": True})
        if r.state == "proposed":
            orch.approve_run(r.id, actor="operator")
        orch.tick()
        return agent.client.post("/api/orchestration/node/poll", {"wait_s": 0, "running": []})["dispatches"][0]

    # 2. Bad signature.
    env = fresh()
    forged = dict(env, params={"message": "forged"})
    agent.handle_dispatch(forged)
    agent.flush_events()
    ex = orch.store.get_execution(env["exec_id"])
    assert ex.state == "refused" and "signature" in ex.reason

    # 3. Replay of a genuine envelope that already ran.
    env = fresh()
    ex = refused_reason(env)
    assert ex.state == "succeeded"
    # Replaying goes nowhere: the node refuses and the master ignores the late result.
    agent.handle_dispatch(env)
    agent.flush_events()
    late = [e for e in orch.store.audit_recent(20) if e["action"] == "execution.late_result"]
    assert late and late[0]["payload"]["state"] == "refused"

    # 4. Out-of-policy tool (mutating on a read-only node).
    env = fresh("demo.change@1")
    ex = refused_reason(env)
    assert ex.state == "refused" and "max_effect" in ex.reason

    # 5. Tampered blob: the master serves the right digest, but the node's cache was corrupted.
    env = fresh()
    sha = env["blobs"]["main.py"]
    agent.store.put_blob(sha, orch.store.get_blob(sha))
    with open(agent.store.blob_path(sha), "wb") as fh:
        fh.write(b"print('evil')\n")
    ex = refused_reason(env)
    # The tampered cache entry is detected, deleted and re-fetched (verified) from the master.
    assert ex.state == "succeeded" and ex.output == {"message": "hi"}
    # A blob whose digest does not match what the master serves is never stored.
    with pytest.raises(ValueError):
        agent.store.put_blob(sha, b"print('evil')\n")
    assert run.id


def test_cancel_over_the_wire(master, tmp_path):
    orch, _endpoint, status = master
    slow = "import time\nprint('working', flush=True)\ntime.sleep(30)\n"
    publish_tool(orch, tool_manifest("demo.slow"), files={"main.py": slow})
    agent = _agent(orch, status, tmp_path, "slowpoke")
    run = orch.create_run(actor="operator", kind="tool", target="demo.slow@1", selector={"all": True})
    orch.tick()
    agent.poll_once(wait_s=0)
    deadline = time.time() + 10
    while time.time() < deadline and not agent.executor._procs:
        time.sleep(0.05)
    from mu.orchestration.commands import dispatch
    dispatch(orch, {"type": "cancelRun", "args": {"run_id": run.id}})
    agent.poll_once(wait_s=0)          # receives the signed cancel
    assert agent.wait_idle(15)
    agent.flush_events()
    ex = orch.store.list_executions(run_id=run.id)[0]
    assert ex.state == "cancelled"


def test_long_poll_returns_as_soon_as_work_is_queued(master, tmp_path):
    orch, _endpoint, status = master
    publish_tool(orch)
    agent = _agent(orch, status, tmp_path, "waiter")
    import threading
    out = {}
    thread = threading.Thread(target=lambda: out.setdefault("r", agent.client.post(
        "/api/orchestration/node/poll", {"wait_s": 10, "running": []}, timeout=30)))
    started = time.time()
    thread.start()
    time.sleep(1.0)
    orch.create_run(actor="operator", kind="tool", target="demo.echo@1", selector={"all": True})
    orch.tick()
    thread.join(timeout=15)
    assert out["r"]["dispatches"] and time.time() - started < 6


def test_agent_download_and_installer(master):
    orch, _endpoint, status = master
    from mu.orchestration.node.transport import MasterClient
    import hashlib
    client = MasterClient(status["url"], cafile=orch.keys.tls_identity(["127.0.0.1"]).cert_path,
                          fingerprint=status["fingerprint"])
    pyz = client.get("/api/orchestration/node/agent.pyz", raw=True)
    script = client.get("/api/orchestration/node/install.sh", raw=True).decode()
    assert hashlib.sha256(pyz).hexdigest() in script
    assert status["fingerprint"] in script and "visudo -cf" in script and "mucli-node-exec" in script


def test_endpoint_refuses_public_binds(tmp_path, monkeypatch):
    from mu.orchestration.overlay import ALLOW_PUBLIC_ENV, BindRefused
    monkeypatch.delenv(ALLOW_PUBLIC_ENV, raising=False)
    orch = make_orch(tmp_path)
    endpoint = NodeEndpoint(orch)
    for host in ("0.0.0.0", "::", "8.8.8.8"):
        with pytest.raises(BindRefused):
            endpoint.start(host, _free_port())
        assert not endpoint.status()["running"] and endpoint.status()["error"]
    orch.stop()


def test_enrol_rate_limit(master):
    orch, _endpoint, status = master
    from mu.orchestration.node.transport import MasterClient
    client = MasterClient(status["url"], cafile=orch.keys.tls_identity(["127.0.0.1"]).cert_path,
                          fingerprint=status["fingerprint"])
    codes = []
    for _ in range(12):
        try:
            client.post("/api/orchestration/node/enroll", {"token": "mjt_bad"})
        except AuthRejected:
            codes.append(403)
        except TransportError as exc:
            codes.append(429 if "429" in str(exc) else 0)
    assert 429 in codes and codes[0] == 403
