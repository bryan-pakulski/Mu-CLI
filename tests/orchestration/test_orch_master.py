"""Master-side flows with in-process nodes: catalog, planning, approvals, runs, runbooks,
rollback, kill switch, schedules and restart recovery (plan §3–§5, phases P1–P3, P5)."""

from __future__ import annotations

import pytest

from mu.orchestration import catalog, runs
from mu.orchestration.commands import AI_COMMANDS, CommandError, CommandRejected, dispatch
from mu.orchestration.service import Orchestrator

from orch_helpers import Clock, FakeNode, add_node, drive, make_orch, publish_tool, tool_manifest


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


# --------------------------------------------------------------------- catalog lifecycle

def test_draft_review_publish_and_immutability(orch):
    tv = catalog.create_draft(orch.store, actor="ai:s", manifest=tool_manifest("demo.echo"),
                              files={"main.py": "print('x')\n"}, intent="echo things")
    assert tv.status == "draft" and tv.stage == "spec"
    with pytest.raises(Exception):
        catalog.publish(orch.store, orch.keys, tv.tool_id, tv.version, actor="operator")   # not reviewed
    tv = catalog.submit_for_review(orch.store, tv.tool_id, tv.version, actor="ai:s", headline="echo",
                                   risks=["none"])
    assert tv.status == "in_review" and tv.review["headline"] == "echo"
    with pytest.raises(catalog.CatalogError, match="operator"):
        catalog.publish(orch.store, orch.keys, tv.tool_id, tv.version, actor="ai:s")
    tv = catalog.publish(orch.store, orch.keys, tv.tool_id, tv.version, actor="operator")
    assert tv.status == "published" and tv.signature and tv.key_id == orch.keys.signing_key_id
    assert orch.keys.verify_version(tv.tool_id, tv.version, tv.manifest_digest, tv.blobs, tv.signature)
    with pytest.raises(catalog.CatalogError, match="only drafts"):
        catalog.update_draft(orch.store, tv.tool_id, tv.version, actor="operator", files={"main.py": "evil"})
    nxt = catalog.new_version_from(orch.store, tv.tool_id, tv.version, actor="operator")
    assert nxt.version == 2 and nxt.status == "draft" and nxt.blobs == tv.blobs


def test_lint_errors_block_review_and_destructive_needs_typed_publish(orch):
    bad = catalog.create_draft(orch.store, actor="ai:s", manifest=tool_manifest("demo.bad"),
                               files={"main.py": "import os\nos.system('rm -rf /')\n"})
    with pytest.raises(catalog.CatalogError, match="lint"):
        catalog.submit_for_review(orch.store, bad.tool_id, bad.version, actor="ai:s")
    m = tool_manifest("demo.nuke", effect="destructive")
    tv = catalog.create_draft(orch.store, actor="operator", manifest=m, files={"main.py": "print(1)\n"})
    catalog.submit_for_review(orch.store, tv.tool_id, tv.version, actor="operator")
    with pytest.raises(catalog.CatalogError, match="typing the tool id"):
        catalog.publish(orch.store, orch.keys, tv.tool_id, tv.version, actor="operator")
    assert catalog.publish(orch.store, orch.keys, tv.tool_id, tv.version, actor="operator",
                           typed="demo.nuke").status == "published"


def test_references_must_be_published_and_preconditions_read_only(orch):
    mutating = publish_tool(orch, tool_manifest("demo.mutate", effect="mutating"))
    m = tool_manifest("demo.uses", preconditions=[mutating.ref])
    tv = catalog.create_draft(orch.store, actor="operator", manifest=m, files={"main.py": "print(1)\n"})
    catalog.submit_for_review(orch.store, tv.tool_id, tv.version, actor="operator")
    with pytest.raises(catalog.CatalogError, match="read-only"):
        catalog.publish(orch.store, orch.keys, tv.tool_id, tv.version, actor="operator")
    m2 = tool_manifest("demo.uses2", rollback="demo.missing@1")
    tv2 = catalog.create_draft(orch.store, actor="operator", manifest=m2, files={"main.py": "print(1)\n"})
    catalog.submit_for_review(orch.store, tv2.tool_id, tv2.version, actor="operator")
    with pytest.raises(catalog.CatalogError, match="published tool"):
        catalog.publish(orch.store, orch.keys, tv2.tool_id, tv2.version, actor="operator")


def test_one_open_draft_per_tool_and_search(orch):
    publish_tool(orch, tool_manifest("disk.usage", title="Disk usage report"))
    catalog.create_draft(orch.store, actor="ai:s", manifest=tool_manifest("disk.usage"), files={"main.py": "x=1\n"})
    with pytest.raises(catalog.CatalogError, match="open draft"):
        catalog.create_draft(orch.store, actor="ai:s", manifest=tool_manifest("disk.usage"), files={})
    found = catalog.search(orch.store, "disk")
    assert [tv.ref for tv in found] == ["disk.usage@1"]


# --------------------------------------------------------------------- planning

def test_plan_excludes_nodes_with_reasons(orch):
    publish_tool(orch, tool_manifest("demo.echo", targets={"requires": ["nginx"]}))
    web, _ = add_node(orch, "web1", {"env": "dev"}, facts={"system": "linux", "systemd": True, "provides": ["nginx"]})
    add_node(orch, "db1", {"env": "dev"})
    add_node(orch, "new1", {"env": "dev"}, status="pending")
    add_node(orch, "q1", {"env": "dev"}, status="quarantined")
    plan = runs.build_plan(orch.store, actor="operator", kind="tool", target="demo.echo@1",
                           selector={"labels": "env=dev"})
    assert plan["ok"] and plan["nodes"] == [web.id]
    reasons = {x["name"]: x["reason"] for x in plan["excluded"]}
    assert reasons == {"db1": "missing nginx", "new1": "node is pending", "q1": "node is quarantined"}


def test_plan_errors(orch):
    publish_tool(orch)
    add_node(orch, "a", {"env": "dev"})
    with pytest.raises(runs.RunError, match="does not exist"):
        runs.build_plan(orch.store, actor="operator", kind="tool", target="demo.missing@1", selector={"all": True})
    plan = runs.build_plan(orch.store, actor="operator", kind="tool", target="demo.echo@1", selector={})
    assert not plan["ok"] and "choose target" in plan["errors"][0]
    plan = runs.build_plan(orch.store, actor="operator", kind="tool", target="demo.echo@1",
                           selector={"all": True}, params={"message": 5})
    assert not plan["ok"] and "params.message" in plan["errors"][0]
    plan = runs.build_plan(orch.store, actor="operator", kind="tool", target="demo.echo@1",
                           selector={"labels": "env=prod"})
    assert plan["nodes"] == [] and plan["ok"]  # nothing matched: nothing excluded, nothing to do
    draft = catalog.create_draft(orch.store, actor="ai:s", manifest=tool_manifest("demo.draft"), files={})
    with pytest.raises(runs.RunError, match="draft"):
        runs.build_plan(orch.store, actor="operator", kind="tool", target=draft.ref, selector={"all": True})


def test_max_nodes_from_the_manifest(orch):
    publish_tool(orch, tool_manifest("demo.small", rollout={"max_nodes": 2}))
    for i in range(3):
        add_node(orch, f"n{i}", {"env": "dev"})
    plan = runs.build_plan(orch.store, actor="operator", kind="tool", target="demo.small@1",
                           selector={"labels": "env=dev"})
    assert not plan["ok"] and "rollout limit" in plan["errors"][0]


# --------------------------------------------------------------------- access decisions and approvals

def test_ai_read_only_runs_follow_the_access_policy(orch):
    publish_tool(orch)
    dev, _ = add_node(orch, "dev1", {"env": "dev"})
    prod, _ = add_node(orch, "prod1", {"env": "prod"})
    run = orch.create_run(actor="ai:s", kind="tool", target="demo.echo@1", selector={"labels": "env=dev"})
    assert run.state == "approved" and run.approved_by.startswith("policy:")
    run = orch.create_run(actor="ai:s", kind="tool", target="demo.echo@1", selector={"labels": "env=prod"})
    assert run.state == "proposed" and run.decision["level"] == "confirm"
    # The AI can never approve, even its own proposal.
    with pytest.raises(CommandRejected):
        cmd(orch, "approveRun", actor="ai:s", run_id=run.id)
    assert cmd(orch, "approveRun", run_id=run.id)["run"]["state"] == "approved"


def test_deny_rules_refuse_the_proposal(orch):
    publish_tool(orch)
    add_node(orch, "pay1", {"env": "dev", "role": "payments"})
    cmd(orch, "saveAccessPolicy", rules=[
        {"name": "AI never touches the payment hosts", "match": {"actor": "ai", "nodes": "role=payments"},
         "decision": "deny"}])
    with pytest.raises(CommandError, match="denied"):
        cmd(orch, "proposeRun", actor="ai:s", target="demo.echo@1", selector={"labels": "role=payments"})
    assert any(e["action"] == "run.refused" for e in orch.store.audit_recent(10))


def test_mutating_runs_wait_and_destructive_need_the_typed_id(orch):
    publish_tool(orch, tool_manifest("demo.change", effect="mutating"))
    publish_tool(orch, tool_manifest("demo.nuke", effect="destructive"))
    add_node(orch, "a", {"env": "dev"})
    run = orch.create_run(actor="operator", kind="tool", target="demo.change@1", selector={"all": True})
    assert run.state == "proposed"
    run = orch.create_run(actor="operator", kind="tool", target="demo.nuke@1", selector={"all": True})
    assert run.decision["level"] == "typed"
    with pytest.raises(CommandError, match="type the tool id"):
        cmd(orch, "approveRun", run_id=run.id, typed="nope")
    assert cmd(orch, "approveRun", run_id=run.id, typed="demo.nuke")["run"]["state"] == "approved"


def test_proposals_expire(orch, clock):
    publish_tool(orch, tool_manifest("demo.change", effect="mutating"))
    add_node(orch, "a", {})
    run = orch.create_run(actor="operator", kind="tool", target="demo.change@1", selector={"all": True})
    clock.advance(runs.PROPOSAL_TTL_S + 1)
    orch.tick()
    assert orch.store.get_run(run.id).state == "expired"


def test_ai_command_allowlist_is_enforced_for_every_other_command(orch):
    from mu.orchestration.commands import HANDLERS
    publish_tool(orch)
    for ctype in sorted(set(HANDLERS) - AI_COMMANDS):
        with pytest.raises(CommandRejected, match="operator"):
            dispatch(orch, {"type": ctype, "args": {}}, actor="ai:test")
    # The forbidden list includes everything that publishes, approves, dispatches or touches secrets.
    for forbidden in ("publishVersion", "approveRun", "continueRun", "saveAccessPolicy", "putSecret",
                      "unlockSecrets", "acceptNode", "setNetwork", "importBundle", "resumeDispatch",
                      "publishRunbook", "addTrustedKey"):
        assert forbidden not in AI_COMMANDS


# --------------------------------------------------------------------- execution through the hub

def test_read_only_tool_runs_on_three_nodes(orch, clock):
    publish_tool(orch)
    fakes = [FakeNode(orch, add_node(orch, f"n{i}", {"env": "dev"})[0]) for i in range(3)]
    run = orch.create_run(actor="operator", kind="tool", target="demo.echo@1", selector={"labels": "env=dev"},
                          params={"message": "hello"})
    drive(orch, fakes, clock=clock)
    run = orch.store.get_run(run.id)
    assert run.state == "succeeded", run.reason
    execs = orch.store.list_executions(run_id=run.id)
    assert len(execs) == 3 and {e.output["message"] for e in execs} == {"hello"}
    assert run.summary["nodes"]["done"] == 3


def test_nodes_refuse_out_of_policy_tools_and_the_run_reports_it(orch, clock):
    publish_tool(orch, tool_manifest("demo.change", effect="mutating"))
    node, _ = add_node(orch, "strict", {})
    fake = FakeNode(orch, node, policy_text="max_effect: read_only\n")
    run = orch.create_run(actor="operator", kind="tool", target="demo.change@1", selector={"all": True})
    cmd(orch, "approveRun", run_id=run.id)
    drive(orch, [fake], clock=clock)
    ex = orch.store.list_executions(run_id=run.id)[0]
    assert ex.state == "refused" and "max_effect" in ex.reason
    assert orch.store.get_run(run.id).state == "failed"


def test_secrets_are_injected_on_the_node_and_redacted_everywhere(orch, clock):
    script = ("import json, os\nv = os.environ['MUCLI_SECRET_API_TOKEN']\nprint('token', v)\n"
              "json.dump({'message': 'got ' + v}, open(os.environ['MUCLI_RESULT'], 'w'))\n")
    publish_tool(orch, tool_manifest("demo.secret", secrets=["api_token"]), files={"main.py": script})
    cmd(orch, "putSecret", name="api_token", value="tok-SUPER-secret-123")
    fake = FakeNode(orch, add_node(orch, "a", {})[0])
    run = orch.create_run(actor="operator", kind="tool", target="demo.secret@1", selector={"all": True})
    drive(orch, [fake], clock=clock)
    ex = orch.store.list_executions(run_id=run.id)[0]
    assert ex.state == "succeeded"
    assert "tok-SUPER-secret-123" not in ex.log and "tok-SUPER-secret-123" not in str(ex.output)
    assert "secrets" not in ex.envelope
    import json as _json
    audit_text = _json.dumps(orch.store.audit_entries(limit=10_000))
    assert "tok-SUPER-secret-123" not in audit_text
    from mu.orchestration import vm
    assert "tok-SUPER-secret-123" not in _json.dumps(vm.execution_vm(ex, for_ai=True))


def test_locked_secrets_stop_dispatch(orch, clock):
    publish_tool(orch, tool_manifest("demo.secret", secrets=["api_token"]))
    cmd(orch, "putSecret", name="api_token", value="tok-123456")
    cmd(orch, "setSecretPassphrase", passphrase="long enough")
    cmd(orch, "lockSecrets")
    add_node(orch, "a", {})
    run = orch.create_run(actor="operator", kind="tool", target="demo.secret@1", selector={"all": True})
    assert run.state == "proposed"   # auto-approval failed: secrets are locked
    with pytest.raises(CommandError, match="locked"):
        cmd(orch, "approveRun", run_id=run.id)
    cmd(orch, "unlockSecrets", passphrase="long enough")
    assert cmd(orch, "approveRun", run_id=run.id)["run"]["state"] == "approved"
    orch.secrets.lock()


def test_kill_switch_holds_dispatch(orch, clock):
    publish_tool(orch)
    fake = FakeNode(orch, add_node(orch, "a", {})[0])
    cmd(orch, "pauseDispatch", reason="incident")
    run = orch.create_run(actor="operator", kind="tool", target="demo.echo@1", selector={"all": True})
    orch.tick()
    assert fake.poll()["dispatches"] == []
    cmd(orch, "resumeDispatch")
    drive(orch, [fake], clock=clock)
    assert orch.store.get_run(run.id).state == "succeeded"


def test_cancel_sends_signed_cancels_for_running_work(orch, clock):
    publish_tool(orch)
    fake = FakeNode(orch, add_node(orch, "a", {})[0])
    run = orch.create_run(actor="operator", kind="tool", target="demo.echo@1", selector={"all": True})
    orch.tick()
    envelope = fake.poll()["dispatches"][0]
    fake.running = [envelope["exec_id"]]
    fake.event(type="running", exec_id=envelope["exec_id"])
    cmd(orch, "cancelRun", run_id=run.id)
    cancels = fake.poll()["cancels"]
    assert [c["exec_id"] for c in cancels] == [envelope["exec_id"]]
    from mu.orchestration.node.envelope import verify_cancel
    assert verify_cancel(cancels[0], node_id=fake.node.id, signing_key=orch.keys.signing_public).ok
    fake.finish(envelope, "cancelled")
    assert orch.store.get_execution(envelope["exec_id"]).state == "cancelled"


def test_nodes_cannot_report_for_other_nodes(orch, clock):
    publish_tool(orch)
    a = FakeNode(orch, add_node(orch, "a", {})[0])
    b = FakeNode(orch, add_node(orch, "b", {})[0])
    run = orch.create_run(actor="operator", kind="tool", target="demo.echo@1", selector={"nodes": [a.node.id]})
    orch.tick()
    envelope = a.poll()["dispatches"][0]
    assert b.finish(envelope, "succeeded", output={"message": "forged"}) == {"accepted": 0}
    assert orch.hub.blob(b.node, envelope["blobs"]["main.py"]) is None
    assert orch.hub.blob(a.node, envelope["blobs"]["main.py"]) is not None
    assert orch.store.get_execution(envelope["exec_id"]).state == "dispatched"


def test_master_redacts_results_again(orch, clock):
    publish_tool(orch)
    fake = FakeNode(orch, add_node(orch, "a", {})[0])
    orch.create_run(actor="operator", kind="tool", target="demo.echo@1", selector={"all": True})
    orch.tick()
    envelope = fake.poll()["dispatches"][0]
    fake.finish(envelope, output={"message": "key AKIAABCDEFGHIJKLMNOP"}, log="ghp_" + "x" * 36)
    ex = orch.store.get_execution(envelope["exec_id"])
    assert "AKIAABCDEFGHIJKLMNOP" not in str(ex.output) and "ghp_" not in ex.log and ex.redactions >= 2


def test_output_schema_is_rechecked_on_the_master(orch):
    publish_tool(orch)
    fake = FakeNode(orch, add_node(orch, "a", {})[0])
    orch.create_run(actor="operator", kind="tool", target="demo.echo@1", selector={"all": True})
    orch.tick()
    envelope = fake.poll()["dispatches"][0]
    fake.finish(envelope, output={"message": 123})
    ex = orch.store.get_execution(envelope["exec_id"])
    assert ex.state == "failed" and ex.output_errors


# --------------------------------------------------------------------- liveness

def test_offline_node_idempotent_redispatch_and_non_idempotent_unreachable(orch, clock):
    publish_tool(orch, tool_manifest("demo.idem"))
    publish_tool(orch, tool_manifest("demo.once", effect="mutating", idempotent=False))
    a = FakeNode(orch, add_node(orch, "a", {})[0])
    r1 = orch.create_run(actor="operator", kind="tool", target="demo.idem@1", selector={"all": True})
    r2 = orch.create_run(actor="operator", kind="tool", target="demo.once@1", selector={"all": True})
    cmd(orch, "approveRun", run_id=r2.id)
    orch.tick()
    first = a.poll()["dispatches"]
    assert len(first) == 2
    clock.advance(200)   # the node goes silent mid-execution
    orch.tick()
    states = {e.tool_id: e for e in orch.store.list_executions()}
    assert states["demo.idem"].state == "queued" and states["demo.once"].state == "unreachable"
    assert "decide" in states["demo.once"].reason
    second = a.poll()["dispatches"]
    assert [d["tool_id"] for d in second] == ["demo.idem"] and second[0]["nonce"] != first[0]["nonce"]
    # A human retries the unreachable node.
    orch.tick()
    assert orch.store.get_run(r2.id).state == "failed"


def test_timeouts_mark_executions_and_request_cancel(orch, clock):
    publish_tool(orch, tool_manifest("demo.slow", limits={"timeout_s": 10}))
    a = FakeNode(orch, add_node(orch, "a", {})[0])
    run = orch.create_run(actor="operator", kind="tool", target="demo.slow@1", selector={"all": True})
    orch.tick()
    env = a.poll()["dispatches"][0]
    a.event(type="running", exec_id=env["exec_id"])
    clock.advance(45)
    a.poll()   # still online
    orch.tick()
    ex = orch.store.get_execution(env["exec_id"])
    assert ex.state == "timed_out" and ex.cancel_requested
    a.running = [env["exec_id"]]
    assert [c["exec_id"] for c in a.poll()["cancels"]] == [env["exec_id"]]
    assert orch.store.get_run(run.id).state == "failed"


def test_master_restart_mid_rollout_resumes(tmp_path, clock):
    orch = make_orch(tmp_path, clock)
    publish_tool(orch)
    nodes = [add_node(orch, f"n{i}", {"env": "dev"})[0] for i in range(5)]
    run = orch.create_run(actor="operator", kind="tool", target="demo.echo@1", selector={"labels": "env=dev"},
                          strategy={"canary": 1, "batch": 2})
    fakes = [FakeNode(orch, n) for n in nodes]
    orch.tick()
    for f in fakes:
        f.run_all()
    orch.tick()                               # canary done, batch advanced
    orch.tick()                               # next batch queued
    claimed = [d for f in fakes for d in f.poll()["dispatches"]]
    assert claimed                            # batch 2 dispatched, results not yet in
    orch.stop()
    # "Kill" the master: a brand new instance on the same store finishes the rollout.
    fresh = Orchestrator(str(tmp_path / "orch"), clock=clock)
    for f in fakes:
        f.orch = fresh
    by_exec = {d["exec_id"]: f for f in fakes for d in claimed if d["node_id"] == f.node.id}
    for d in claimed:
        by_exec[d["exec_id"]].execute(d)
    drive(fresh, fakes, clock=clock)
    run = fresh.store.get_run(run.id)
    assert run.state == "succeeded" and run.summary["nodes"]["done"] == 5
    fresh.stop()


# --------------------------------------------------------------------- tests of drafts (Studio)

def test_draft_tests_run_only_on_canaries_with_the_test_key(orch, clock):
    canary_node, _ = add_node(orch, "canary1", {"env": "dev"}, canary=True)
    plain, _ = add_node(orch, "plain1", {"env": "dev"})
    tv = catalog.create_draft(orch.store, actor="ai:s", manifest=tool_manifest("demo.new", tests=[
        {"name": "dry", "params": {"message": "t"}, "check_mode": True, "expect": {"exit_code": 0, "output_schema": True}}]),
        files={"main.py": "import json, os\njson.dump({'message': 'ok'}, open(os.environ['MUCLI_RESULT'], 'w'))\n"})
    with pytest.raises(runs.RunError, match="canary"):
        runs.propose_test(orch.store, actor="ai:s", tool_id=tv.tool_id, version=tv.version, node_ids=[plain.id])
    data = cmd(orch, "proposeTest", actor="ai:s", ref=tv.ref, test_name="dry")
    run = orch.store.get_run(data["run"]["id"])
    assert run.state == "proposed" and run.kind == "test"    # every test waits for the operator
    cmd(orch, "approveRun", run_id=run.id)
    assert orch.store.get_version(tv.tool_id, tv.version).status == "testing"
    fake = FakeNode(orch, canary_node)
    drive(orch, [fake], clock=clock)
    env = orch.store.list_executions(run_id=run.id)[0].envelope
    assert env["test"] is True and env["check_mode"] is True
    tests = orch.store.get_version(tv.tool_id, tv.version).review["tests"]
    assert tests[-1]["passed"] and tests[-1]["name"] == "dry"
    # A non-canary node refuses a test-signed tool even if a compromised master sends it.
    stranger = FakeNode(orch, plain, policy_text="max_effect: destructive\n")
    assert stranger.execute(dict(env, node_id=plain.id))["state"] == "refused"


def test_editing_a_draft_after_approval_blocks_the_test(orch, clock):
    canary_node, _ = add_node(orch, "c", {}, canary=True)
    tv = catalog.create_draft(orch.store, actor="ai:s", manifest=tool_manifest("demo.t"), files={"main.py": "print(1)\n"})
    run = runs.propose_test(orch.store, actor="ai:s", tool_id=tv.tool_id, version=tv.version, node_ids=[canary_node.id])
    cmd(orch, "approveRun", run_id=run.id)
    catalog.update_draft(orch.store, tv.tool_id, tv.version, actor="ai:s", files={"main.py": "print(2)\n"})
    orch.tick()
    assert FakeNode(orch, canary_node).poll()["dispatches"] == []
    ex = orch.store.list_executions(run_id=run.id)[0]
    assert ex.state == "failed" and "changed" in ex.reason


# --------------------------------------------------------------------- runbooks (P3 exit criteria)

UNDO_PY = "import json, os\njson.dump({'message': 'undone'}, open(os.environ['MUCLI_RESULT'], 'w'))\n"
FAIL_ON_LABEL_PY = (
    "import json, os, sys\n"
    "p = json.load(open(os.environ['MUCLI_PARAMS']))\n"
    "if p.get('message') == 'boom':\n    sys.exit(4)\n"
    "json.dump({'message': p.get('message')}, open(os.environ['MUCLI_RESULT'], 'w'))\n"
)


def _runbook_setup(orch):
    publish_tool(orch, tool_manifest("app.check"))
    publish_tool(orch, tool_manifest("app.restore", effect="mutating"), files={"main.py": UNDO_PY})
    publish_tool(orch, tool_manifest("app.change", effect="mutating", rollback="app.restore@1"),
                 files={"main.py": FAIL_ON_LABEL_PY})
    spec = {"id": "app.deploy", "title": "Deploy app", "inputs": {
        "type": "object", "properties": {"msg": {"type": "string", "maxLength": 20, "default": "ok"}},
        "additionalProperties": False},
        "steps": [
            {"name": "check", "tool": "app.check@1", "params": {"message": "pre"}},
            {"name": "change", "tool": "app.change@1", "on_failure": "stop",
             "params": {"message": "${{ node.labels.payload }}"}},
            {"name": "verify", "tool": "app.check@1", "when": "steps.change.state == 'succeeded'",
             "params": {"message": "${{ steps.change.outputs.message }}"}},
        ]}
    rb = catalog.save_runbook_draft(orch.store, actor="operator", spec=spec)
    assert catalog.runbook_problems(orch.store, rb) == []
    catalog.set_runbook_status(orch.store, rb.runbook_id, rb.version, "in_review", actor="ai:s")
    with pytest.raises(catalog.CatalogError, match="operator"):
        catalog.set_runbook_status(orch.store, rb.runbook_id, rb.version, "published", actor="ai:s")
    return catalog.set_runbook_status(orch.store, rb.runbook_id, rb.version, "published", actor="operator")


def test_three_step_runbook_across_twenty_nodes_pauses_on_failures_then_rolls_back(orch, clock):
    rb = _runbook_setup(orch)
    fakes = []
    for i in range(20):
        payload = "boom" if i in (6, 7, 8) else f"v{i}"
        fakes.append(FakeNode(orch, add_node(orch, f"node{i:02d}", {"env": "stage", "payload": payload})[0]))
    run = orch.create_run(actor="operator", kind="runbook", target=rb.ref, selector={"labels": "env=stage"},
                          strategy={"canary": 1, "batch": 5, "failure_threshold": 20})
    assert run.state == "proposed" and run.plan["effect"] == "mutating" and len(run.plan["batches"]) == 5
    cmd(orch, "approveRun", run_id=run.id)
    drive(orch, fakes, clock=clock)
    run = orch.store.get_run(run.id)
    # Batch 2 (nodes 6-10) has 3/5 failures: 60% > 20% -> paused, waiting for the operator.
    assert run.state == "paused" and "60%" in run.paused_reason, run.paused_reason
    execs = orch.store.list_executions(run_id=run.id)
    done_nodes = {e.node_id for e in execs}
    assert len(done_nodes) == 11       # canary + two batches, nothing beyond the pause
    failed = [e for e in execs if e.state == "failed"]
    assert len(failed) == 3 and all(e.step_name == "change" for e in failed)
    verify_skipped = [e for e in execs if e.step_name == "verify" and e.state != "succeeded"]
    assert verify_skipped == []        # failed nodes stopped (on_failure: stop), no verify executions
    verified = [e for e in execs if e.step_name == "verify" and e.state == "succeeded"]
    assert verified and all(e.output["message"].startswith("v") for e in verified)  # outputs flowed between steps
    # Operator rolls back: every succeeded change is compensated, newest first.
    cmd(orch, "rollbackRun", run_id=run.id)
    drive(orch, fakes, clock=clock)
    run = orch.store.get_run(run.id)
    assert run.state == "rolled_back", run.reason
    rollbacks = [e for e in orch.store.list_executions(run_id=run.id) if e.rollback_of]
    assert len(rollbacks) == 8 and all(e.tool_id == "app.restore" and e.state == "succeeded" for e in rollbacks)


def test_continue_after_pause_completes_the_rollout(orch, clock):
    rb = _runbook_setup(orch)
    fakes = [FakeNode(orch, add_node(orch, f"n{i}", {"env": "s", "payload": "boom" if i == 0 else "x"})[0])
             for i in range(4)]
    run = orch.create_run(actor="operator", kind="runbook", target=rb.ref, selector={"labels": "env=s"},
                          strategy={"canary": 1, "batch": 3})
    cmd(orch, "approveRun", run_id=run.id)
    drive(orch, fakes, clock=clock)
    assert orch.store.get_run(run.id).state == "paused"          # the canary failed
    cmd(orch, "continueRun", run_id=run.id)
    drive(orch, fakes, clock=clock)
    run = orch.store.get_run(run.id)
    assert run.state == "failed" and run.summary["nodes"]["done"] == 3 and run.summary["nodes"]["failed"] == 1


def test_on_failure_rollback_compensates_only_that_node(orch, clock):
    publish_tool(orch, tool_manifest("app.restore", effect="mutating"), files={"main.py": UNDO_PY})
    publish_tool(orch, tool_manifest("app.change", effect="mutating", rollback="app.restore@1"),
                 files={"main.py": FAIL_ON_LABEL_PY})
    rb = catalog.save_runbook_draft(orch.store, actor="operator", spec={
        "id": "app.two", "title": "two", "steps": [
            {"name": "first", "tool": "app.change@1", "params": {"message": "fine"}},
            {"name": "second", "tool": "app.change@1", "on_failure": "rollback",
             "params": {"message": "${{ node.labels.payload }}"}}]})
    catalog.set_runbook_status(orch.store, rb.runbook_id, rb.version, "in_review", actor="operator")
    catalog.set_runbook_status(orch.store, rb.runbook_id, rb.version, "published", actor="operator")
    good = FakeNode(orch, add_node(orch, "good", {"payload": "ok"})[0])
    bad = FakeNode(orch, add_node(orch, "bad", {"payload": "boom"})[0])
    run = orch.create_run(actor="operator", kind="runbook", target=rb.ref, selector={"all": True},
                          strategy={"canary": 0, "batch": 2, "failure_threshold": 100})
    cmd(orch, "approveRun", run_id=run.id)
    drive(orch, [good, bad], clock=clock)
    execs = orch.store.list_executions(run_id=run.id)
    rollbacks = [e for e in execs if e.rollback_of]
    assert [e.node_id for e in rollbacks] == [bad.node.id] and rollbacks[0].state == "succeeded"
    assert orch.store.get_run(run.id).state == "failed"


def test_quarantine_mid_run_skips_the_node(orch, clock):
    publish_tool(orch)
    a = FakeNode(orch, add_node(orch, "a", {})[0])
    b = FakeNode(orch, add_node(orch, "b", {})[0])
    run = orch.create_run(actor="operator", kind="tool", target="demo.echo@1", selector={"all": True},
                          strategy={"canary": 1, "batch": 1, "failure_threshold": 100})
    cmd(orch, "quarantineNode", node_id=(b.node.id if run.plan["batches"][0] == [a.node.id] else a.node.id))
    drive(orch, [a, b], clock=clock)
    run = orch.store.get_run(run.id)
    cancelled = [e for e in orch.store.list_executions(run_id=run.id) if e.state == "cancelled"]
    assert len(cancelled) == 1 and "quarantined" in cancelled[0].reason


# --------------------------------------------------------------------- schedules and triggers

def test_interval_schedule_creates_runs(orch, clock):
    publish_tool(orch)
    add_node(orch, "a", {"env": "dev"})
    orch.put_schedule({"name": "every hour", "kind": "interval", "spec": {"every_s": 3600},
                       "target": {"kind": "tool", "target": "demo.echo@1", "selector": {"labels": "env=dev"}}},
                      actor="operator")
    orch.tick()
    assert orch.store.list_runs() == []
    clock.advance(3601)
    orch.tick()
    created = orch.store.list_runs()
    assert len(created) == 1 and created[0].schedule_id and created[0].state in ("approved", "running")


def test_fact_trigger_fires_on_the_edge_with_cooldown(orch, clock):
    publish_tool(orch, tool_manifest("disk.cleanup", effect="mutating"))
    node, _ = add_node(orch, "a", {"env": "dev"})
    orch.put_schedule({"name": "disk full", "kind": "trigger",
                       "spec": {"metric": "max_disk_used_pct", "op": ">", "value": 90, "cooldown_s": 600},
                       "target": {"kind": "tool", "target": "disk.cleanup@1"}}, actor="operator")
    fake = FakeNode(orch, node)
    fake.poll(facts={"disks": [{"used_pct": 50}]})
    assert orch.store.list_runs() == []
    fake.poll(facts={"disks": [{"used_pct": 95}]})
    runs_ = orch.store.list_runs()
    assert len(runs_) == 1 and runs_[0].state == "proposed" and runs_[0].nodes == [node.id]
    fake.poll(facts={"disks": [{"used_pct": 96}]})       # still high: no new edge
    fake.poll(facts={"disks": [{"used_pct": 40}]})
    fake.poll(facts={"disks": [{"used_pct": 97}]})       # new edge but inside the cooldown
    assert len(orch.store.list_runs()) == 1
    clock.advance(700)
    fake.poll(facts={"disks": [{"used_pct": 40}]})
    fake.poll(facts={"disks": [{"used_pct": 98}]})
    assert len(orch.store.list_runs()) == 2


# --------------------------------------------------------------------- nodes

def test_revoked_nodes_are_refused_and_live_work_is_written_off(orch):
    publish_tool(orch)
    node, credential = add_node(orch, "a", {})
    assert orch.hub.authenticate(node.id, credential) is not None
    assert orch.hub.authenticate(node.id, "wrong") is None
    run = orch.create_run(actor="operator", kind="tool", target="demo.echo@1", selector={"all": True})
    orch.tick()
    cmd(orch, "revokeNode", node_id=node.id)
    assert orch.hub.authenticate(node.id, credential) is None
    assert orch.store.list_executions(run_id=run.id)[0].state == "unreachable"


def test_join_tokens_are_one_time_and_expire(orch, clock):
    from mu.orchestration import enroll
    token = enroll.create_join_token(orch.store, labels={"env": "dev"})
    reply = enroll.enroll(orch.store, orch.keys, token=token["token"], name="a", facts={}, policy={}, agent_version="1")
    assert orch.store.get_node(reply["node_id"]).labels == {"env": "dev"}
    with pytest.raises(enroll.EnrollError, match="already used"):
        enroll.enroll(orch.store, orch.keys, token=token["token"], name="b", facts={}, policy={}, agent_version="1")
    old = enroll.create_join_token(orch.store, ttl_s=60)
    clock.advance(61)
    with pytest.raises(enroll.EnrollError, match="expired"):
        enroll.enroll(orch.store, orch.keys, token=old["token"], name="c", facts={}, policy={}, agent_version="1")
    with pytest.raises(enroll.EnrollError, match="unknown"):
        enroll.enroll(orch.store, orch.keys, token="mjt_nope", name="d", facts={}, policy={}, agent_version="1")
    with orch.store.conn() as conn:
        stored = [r["token_hash"] for r in conn.execute("SELECT token_hash FROM join_tokens")]
    assert token["token"] not in stored


def test_credential_rotation_through_the_poll(orch, clock):
    node, credential = add_node(orch, "a", {})
    from mu.orchestration import enroll
    clock.advance(enroll.ROTATE_AFTER_S + 1)
    reply = orch.hub.heartbeat(orch.store.get_node(node.id), {})
    new = reply["rotate_credential"]
    assert orch.hub.authenticate(node.id, credential) is not None    # old still valid until the new one is used
    assert orch.hub.authenticate(node.id, new) is not None           # promotes the new credential
    assert orch.hub.authenticate(node.id, credential) is None


def test_audit_chain_covers_the_whole_flow(orch, clock):
    from mu.orchestration.audit import verify_store
    publish_tool(orch)
    fake = FakeNode(orch, add_node(orch, "a", {})[0])
    orch.create_run(actor="operator", kind="tool", target="demo.echo@1", selector={"all": True})
    drive(orch, [fake], clock=clock)
    actions = {e["action"] for e in orch.store.audit_entries(limit=10_000)}
    assert {"tool.publish", "node.enroll", "node.accept", "run.propose", "run.approve", "execution.dispatch",
            "execution.result", "run.succeeded"} <= actions
    assert verify_store(orch.store).ok


def test_access_policy_accepts_the_settings_document(orch):
    yaml_doc = "rules:\n  - name: allow reads\n    match: {effect: read_only}\n    decision: allow\n"
    assert cmd(orch, "saveAccessPolicy", rules=yaml_doc)["version"] == 1
    assert orch.store.access_rules()[1][0]["name"] == "allow reads"
    with pytest.raises(CommandError):
        cmd(orch, "saveAccessPolicy", rules={"rules": "nope"})


def test_a_new_draft_is_not_lint_ok_until_linted(orch):
    tv = catalog.create_draft(orch.store, actor="ai:s", manifest=tool_manifest("demo.fresh"), files={"main.py": "x=1\n"})
    assert tv.lint["ok"] is False and not catalog.lint_current(tv)
