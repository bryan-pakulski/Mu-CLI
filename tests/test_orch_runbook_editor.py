"""Runbook editor, test rules, dynamic pickers and node setup (documentation/plans/runbook_editor.md):
structured problems and semantic checks, invalid drafts, base_digest conflicts, new versions,
draft runbook tests, ``run: test`` access rules, picker endpoints, bulk tool upgrade, the node
setup wizard's backend, the CLI and the AI tools."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from types import SimpleNamespace

import pytest

from mu.orchestration import access, catalog, enroll, overlay, runbook, runs, vm
from mu.orchestration.commands import CommandError, CommandRejected, dispatch
from mu.orchestration.store import OrchestrationStore

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


def outputs_manifest(tool_id, **extra):
    m = tool_manifest(tool_id, **extra)
    m["inputs"] = {"type": "object", "properties": {
        "message": {"type": "string", "maxLength": 20, "default": "hi"},
        "keep_days": {"type": "integer", "minimum": 1, "maximum": 90, "default": 7}},
        "additionalProperties": False}
    m["outputs"] = {"type": "object", "properties": {"message": {"type": "string"}, "free_pct": {"type": "number"}}}
    return m


def spec(**extra):
    base = {"id": "web.check", "title": "Check web", "inputs": {
        "type": "object", "properties": {"msg": {"type": "string", "default": "ok"}}, "additionalProperties": False},
        "steps": [{"name": "check", "tool": "app.check@1", "params": {"message": "${{ inputs.msg }}"}},
                  {"name": "again", "tool": "app.check@1",
                   "when": "steps.check.outputs.free_pct < 20",
                   "params": {"message": "${{ steps.check.outputs.message }}", "keep_days": 30}}]}
    base.update(extra)
    return base


@pytest.fixture
def tools(orch):
    publish_tool(orch, outputs_manifest("app.check"))
    return orch


def paths(problems, severity="error"):
    return {p["path"]: p["message"] for p in problems if p.get("severity", "error") == severity}


# --------------------------------------------------------------------- pure analysis

def test_references_are_extracted_with_their_paths():
    assert runbook.references("steps.check.outputs.disks[0].mount == 'x' and inputs.force") == [
        ["steps", "check", "outputs", "disks", 0, "mount"], ["inputs", "force"]]
    assert runbook.references("node.labels['env'] in inputs.envs") == [["node", "labels", "env"], ["inputs", "envs"]]


def test_semantic_checks_report_each_problem_at_its_path(tools):
    resolve = lambda tid, v: tools.store.get_version(tid, v)
    bad = spec(steps=[
        {"name": "first", "tool": "app.check@1", "params": {"message": "${{ steps.second.outputs.message }}"}},
        {"name": "second", "tool": "app.check@1", "when": "steps.nope.state == 'succeeded'",
         "params": {"message": "${{ inputs.undeclared }}", "keep_days": "a lot", "colour": "red"}},
        {"name": "third", "tool": "app.check@1", "params": {"message": "${{ steps.first.outputs.gone }}",
                                                            "keep_days": 120}},
    ])
    _spec, problems, info = runbook.analyze(bad, resolve_tool=resolve)
    errors = paths(problems)
    assert "runs after this step" in errors["steps[0].params.message"]
    assert "not an earlier step" in errors["steps[1].when"]
    assert "not a declared input" in errors["steps[1].params.message"]
    assert errors["steps[1].params.keep_days"] == "keep_days: expected integer"
    assert "no parameter 'colour'" in errors["steps[1].params.colour"]
    assert errors["steps[2].params.keep_days"] == "keep_days: must be <= 90"
    assert "does not declare an output 'gone'" in paths(problems, "warning")["steps[2].params.message"]
    assert [s["ref"] for s in info] == ["app.check@1"] * 3 and info[0]["outputs"]["properties"]
    # The string form keeps "path: message" for existing callers; warnings never block.
    _n, errors_text = runbook.normalize(bad, resolve_tool=resolve)
    assert "steps[2].params.keep_days: keep_days: must be <= 90" in errors_text
    assert not any("gone" in e for e in errors_text)


def test_required_params_and_templates(orch):
    m = outputs_manifest("app.need")
    m["inputs"]["required"] = ["message"]
    del m["inputs"]["properties"]["message"]["default"]
    publish_tool(orch, m)
    resolve = lambda tid, v: orch.store.get_version(tid, v)
    s = spec(steps=[{"name": "a", "tool": "app.need@1", "params": {"keep_days": "${{ inputs.msg }}"}}])
    _spec, problems, _info = runbook.analyze(s, resolve_tool=resolve)
    assert "required" in paths(problems)["steps[0].params.message"]
    assert "steps[0].params.keep_days" not in paths(problems)       # templates are typed at run time


def test_valid_spec_is_clean_and_normalize_is_idempotent_by_digest(tools):
    resolve = lambda tid, v: tools.store.get_version(tid, v)
    first, problems, _ = runbook.analyze(spec(), resolve_tool=resolve)
    assert runbook.errors_of(problems) == []
    again, _p, _ = runbook.analyze(first, resolve_tool=resolve)
    assert catalog.runbook_digest(first) == catalog.runbook_digest(again)


def test_yaml_positions_and_preview(tools):
    text = ("id: web.check\ntitle: Check web\nsteps:\n  - name: check\n    tool: app.check@1\n"
            "    params:\n      keep_days: 300\n")
    result = catalog.validate_runbook(tools.store, text)
    problem = next(p for p in result["problems"] if p["path"] == "steps[0].params.keep_days")
    assert problem["line"] == 7 and result["ok"] is False
    broken = catalog.validate_runbook(tools.store, "id: web.check\nsteps: [\n  - nope")
    assert broken["parses"] is False and broken["yaml_error"]["line"] and "YAML doesn't parse" in broken["problems"][0]["message"]
    node, _ = add_node(tools, "web1", {"env": "prod"})
    preview = catalog.validate_runbook(tools.store, spec(), preview={"node_id": node.id, "inputs": {"msg": "hello"}})
    steps = preview["preview"]["steps"]
    assert steps[0]["params"] == {"message": "hello"}
    assert steps[1]["when"] == {"value": False} and steps[1]["params"]["message"] is None   # outputs unknown yet


# --------------------------------------------------------------------- commands

def test_validate_is_read_only(tools):
    before_audit = len(tools.store.audit_entries(limit=10_000))
    before_seq = tools.store.last_change_seq()
    out = cmd(tools, "validateRunbook", spec=spec())
    assert out["ok"] and out["effect"] == "read_only" and out["decision"]["level"] == "allow"
    assert [s["title"] for s in out["steps_info"]] == ["Tool app.check"] * 2
    cmd(tools, "validateRunbook", actor="ai:s", spec=spec())
    assert len(tools.store.audit_entries(limit=10_000)) == before_audit
    assert tools.store.last_change_seq() == before_seq


def test_base_digest_conflict_returns_the_current_draft(tools):
    first = cmd(tools, "saveRunbookDraft", spec=spec())
    digest = first["digest"]
    cmd(tools, "saveRunbookDraft", actor="ai:s", spec=spec(title="Changed by the AI"))   # the AI: last write wins
    with pytest.raises(CommandRejected) as exc:
        cmd(tools, "saveRunbookDraft", spec=spec(title="Mine"), base_digest=digest)
    assert "changed since you opened it" in str(exc.value)
    assert exc.value.data["runbook"]["spec"]["title"] == "Changed by the AI"
    current = exc.value.data["digest"]
    saved = cmd(tools, "saveRunbookDraft", spec=spec(title="Mine"), base_digest=current)
    assert saved["runbook"]["spec"]["title"] == "Mine" and saved["ref"] == "web.check@1"


def test_invalid_drafts_save_but_cannot_be_submitted(tools):
    saved = cmd(tools, "saveRunbookDraft", spec=spec())
    good_spec = saved["runbook"]["spec"]
    broken = "id: web.check\ntitle: a: b\n"
    out = cmd(tools, "saveRunbookDraft", spec=broken, runbook_id="web.check", base_digest=saved["digest"])
    rb = tools.store.get_runbook_version("web.check", 1)
    assert rb.source == broken and rb.spec == good_spec and out["digest"] != saved["digest"]
    detail = vm.runbook_vm(tools.store, rb, detail=True)
    assert detail["parses"] is False and detail["problem_details"][0]["line"] == 2
    with pytest.raises(CommandRejected, match="YAML doesn't parse"):
        cmd(tools, "submitRunbook", ref="web.check@1")
    with pytest.raises(Exception, match="parse"):
        catalog.set_runbook_status(tools.store, "web.check", 1, "in_review", actor="operator")
    # A semantic error saves too (with problems reported) and still can't be submitted.
    bad = spec(steps=[{"name": "a", "tool": "app.check@1", "params": {"keep_days": 500}}])
    out = cmd(tools, "saveRunbookDraft", spec=bad, base_digest=out["digest"])
    assert tools.store.get_runbook_version("web.check", 1).source == ""
    assert out["problems"] and out["problems"][0]["path"] == "steps[0].params.keep_days"
    submit = next(a for a in vm.runbook_vm(tools.store, tools.store.get_runbook_version("web.check", 1))["actions"]
                  if a["id"] == "submitRunbook")
    assert not submit["enabled"] and submit["reason"].startswith("fix 1 problem first")
    with pytest.raises(CommandRejected, match="fix 1 problem"):
        cmd(tools, "submitRunbook", ref="web.check@1")
    with pytest.raises(catalog.CatalogError, match="invalid"):
        catalog.set_runbook_status(tools.store, "web.check", 1, "in_review", actor="operator")
    cmd(tools, "saveRunbookDraft", spec=spec())
    assert cmd(tools, "submitRunbook", ref="web.check@1")["runbook"]["status"] == "in_review"


def test_new_version_and_discard_availability(tools):
    cmd(tools, "saveRunbookDraft", spec=spec())
    cmd(tools, "submitRunbook", ref="web.check@1")
    cmd(tools, "publishRunbook", ref="web.check@1")
    with pytest.raises(CommandRejected, match="published versions cannot be discarded"):
        cmd(tools, "discardRunbookDraft", ref="web.check@1")
    out = cmd(tools, "newRunbookVersion", actor="ai:s", ref="web.check@1")
    assert out["ref"] == "web.check@2"
    assert tools.store.get_runbook_version("web.check", 2).spec["steps"] == \
        tools.store.get_runbook_version("web.check", 1).spec["steps"]
    acts = {a["id"]: a for a in vm.runbook_vm(tools.store, tools.store.get_runbook_version("web.check", 1))["actions"]}
    assert not acts["newRunbookVersion"]["enabled"] and "web.check@2 is already open" in acts["newRunbookVersion"]["reason"]
    with pytest.raises(CommandRejected, match="requires the operator"):
        cmd(tools, "discardRunbookDraft", actor="ai:s", ref="web.check@2")
    cmd(tools, "discardRunbookDraft", ref="web.check@2")
    assert tools.store.get_runbook_version("web.check", 2) is None
    notes = [n["action"] for n in vm.runbook_vm(tools.store, tools.store.get_runbook_version("web.check", 1),
                                                detail=True)["notes"]]
    assert "runbook.draft.discard" in notes and "runbook.published" in notes


def test_runbook_detail_has_diff_versions_and_notes(tools):
    cmd(tools, "saveRunbookDraft", spec=spec())
    cmd(tools, "submitRunbook", ref="web.check@1")
    cmd(tools, "publishRunbook", ref="web.check@1")
    cmd(tools, "newRunbookVersion", ref="web.check@1")
    cmd(tools, "saveRunbookDraft", spec=spec(title="Check web v2"))
    detail = vm.runbook_vm(tools.store, tools.store.get_runbook_version("web.check", 2), detail=True)
    assert detail["previous"] == "web.check@1" and "+title: Check web v2" in detail["diff"]
    assert [v["ref"] for v in detail["versions"]] == ["web.check@1", "web.check@2"]


# --------------------------------------------------------------------- draft runbook tests

def _test_setup(orch):
    publish_tool(orch, outputs_manifest("app.check"))
    canary = FakeNode(orch, add_node(orch, "canary1", {"env": "dev"}, canary=True)[0])
    plain = add_node(orch, "plain1", {"env": "dev"})[0]
    cmd(orch, "saveRunbookDraft", spec=spec())
    return canary, plain


def test_draft_runbook_runs_in_check_mode_on_a_canary_after_approval(orch, clock):
    canary, plain = _test_setup(orch)
    run = cmd(orch, "proposeRunbookTest", ref="web.check@1", nodes=[canary.node.id, plain.id])["run"]
    assert run["state"] == "proposed" and run["kind"] == "runbook_test" and run["check_mode"]
    assert run["nodes"] == [canary.node.id]
    assert run["excluded"] == [{"node_id": plain.id, "name": "plain1", "reason": "test runs only target canary nodes"}]
    assert run["decision"]["level"] == "confirm" and "Tests are approved" in run["decision"]["rule"]
    cmd(orch, "approveRun", run_id=run["id"])
    drive(orch, [canary], clock=clock)
    final = orch.store.get_run(run["id"])
    assert final.state == "succeeded", final.reason
    tests = orch.store.get_runbook_version("web.check", 1).review["tests"]
    assert len(tests) == 1 and tests[0]["passed"] and tests[0]["results"][0]["node_id"] == canary.node.id
    assert all(e.check_mode for e in orch.store.list_executions(run_id=run["id"]))


def test_editing_the_draft_after_approval_blocks_dispatch(orch, clock):
    canary, _plain = _test_setup(orch)
    run = cmd(orch, "proposeRunbookTest", ref="web.check@1")["run"]
    cmd(orch, "approveRun", run_id=run["id"])
    cmd(orch, "saveRunbookDraft", spec=spec(title="edited after approval"))
    drive(orch, [canary], clock=clock)
    execs = orch.store.list_executions(run_id=run["id"])
    assert execs and all(e.state == "failed" and "changed after this test was approved" in e.reason for e in execs)
    assert orch.store.get_run(run["id"]).state == "failed"
    assert not orch.store.get_runbook_version("web.check", 1).review["tests"][-1]["passed"]


def test_change_between_proposal_and_approval_refuses_approval(orch):
    _canary, _plain = _test_setup(orch)
    run = cmd(orch, "proposeRunbookTest", ref="web.check@1")["run"]
    cmd(orch, "saveRunbookDraft", spec=spec(title="edited"))
    with pytest.raises(CommandError, match="changed since this test was proposed"):
        cmd(orch, "approveRun", run_id=run["id"])


def test_runbook_tests_need_a_valid_draft_and_published_tools(orch):
    _test_setup(orch)
    cmd(orch, "saveRunbookDraft", spec=spec(steps=[{"name": "a", "tool": "app.check@1", "params": {"keep_days": 0}}]))
    with pytest.raises(CommandRejected, match="fix 1 problem"):
        cmd(orch, "proposeRunbookTest", ref="web.check@1")
    with pytest.raises(CommandError, match="draft tests"):
        cmd(orch, "proposeRun", kind="runbook_test", target="web.check@1", selector={"all": True})


# --------------------------------------------------------------------- access rules for tests

AI_TEST_RULE = {"name": "AI may test read-only drafts on canaries", "match": {"actor": "ai", "run": "test",
                                                                             "effect": "read_only"},
                "decision": "allow"}


def test_policies_without_run_keys_behave_as_before():
    old = [r for r in access.DEFAULT_RULES if "run" not in r["match"]]
    ro = {"id": "x.y", "effect": "read_only", "category": "diagnostics"}
    nodes = [{"id": "n1", "labels": {"env": "dev"}}]
    for actor in ("ai:s", "operator"):
        assert access.decide(old, actor, ro, nodes).level == access.decide(access.DEFAULT_RULES, actor, ro, nodes).level
        assert access.decide(old, actor, ro, nodes, run="test").level == "confirm"           # fallback
        assert access.decide(access.DEFAULT_RULES, actor, ro, nodes, run="test").level == "confirm"
    everything = [{"name": "allow all", "match": {}, "decision": "allow"}]
    assert access.decide(everything, "ai:s", ro, nodes).level == "allow"
    assert access.decide(everything, "ai:s", ro, nodes, run="test").level == "confirm"       # no run key: runs only


def test_rule_validation_of_the_run_key():
    assert access.validate_rules([dict(AI_TEST_RULE)]) == []
    assert access.validate_rules([{"name": "x", "match": {"run": "run"}, "decision": "deny"}]) == []
    assert "run must be run or test" in access.validate_rules([{"name": "x", "match": {"run": "tests"},
                                                                "decision": "allow"}])[0]


def test_an_allow_test_rule_auto_starts_only_matching_tests(orch, clock):
    canary, _plain = _test_setup(orch)
    cmd(orch, "saveAccessPolicy", rules=[AI_TEST_RULE] + access.DEFAULT_RULES)
    run = cmd(orch, "proposeRunbookTest", actor="ai:s", ref="web.check@1")["run"]
    assert run["state"] in ("approved", "running") and run["approved_by"] == f"policy:{AI_TEST_RULE['name']}"
    drive(orch, [canary], clock=clock)
    assert orch.store.get_run(run["id"]).state == "succeeded"
    # A mutating draft test from the AI still waits; so does the operator's (the rule is AI-only).
    publish_tool(orch, outputs_manifest("app.fix", effect="mutating"))
    draft = catalog.create_draft(orch.store, actor="ai:s", manifest=tool_manifest("app.new"), files={"main.py": "x=1\n"})
    mut = catalog.create_draft(orch.store, actor="ai:s", manifest=tool_manifest("app.mut", effect="mutating"),
                               files={"main.py": "x=1\n"})
    assert cmd(orch, "proposeTest", actor="ai:s", ref=mut.ref)["run"]["state"] == "proposed"
    assert cmd(orch, "proposeTest", actor="ai:s", ref=draft.ref)["run"]["state"] in ("approved", "running")
    assert cmd(orch, "proposeTest", ref=draft.ref)["run"]["state"] == "proposed"


def test_no_rule_relaxes_the_test_invariants(orch, clock):
    canary, plain = _test_setup(orch)
    cmd(orch, "saveAccessPolicy", rules=[{"name": "allow every test", "match": {"run": "test"}, "decision": "allow"}])
    run = cmd(orch, "proposeRunbookTest", ref="web.check@1", nodes=[plain.id, canary.node.id])["run"]
    assert run["nodes"] == [canary.node.id]                        # still canary-only
    with pytest.raises(CommandError, match="no eligible nodes"):
        cmd(orch, "proposeRunbookTest", ref="web.check@1", nodes=[plain.id])
    nuke = catalog.create_draft(orch.store, actor="operator", manifest=tool_manifest("app.nuke", effect="destructive"),
                                files={"main.py": "x=1\n"})
    test = cmd(orch, "proposeTest", ref=nuke.ref)["run"]
    assert test["state"] == "proposed" and test["decision"]["level"] == "typed"   # typed floor stays


# --------------------------------------------------------------------- pickers

def test_catalog_options_by_purpose(orch):
    publish_tool(orch, outputs_manifest("app.check"))
    v2 = catalog.new_version_from(orch.store, "app.check", 1, actor="operator")
    catalog.submit_for_review(orch.store, "app.check", v2.version, actor="operator")
    catalog.publish(orch.store, orch.keys, "app.check", v2.version, actor="operator")
    catalog.set_lifecycle(orch.store, "app.check", 1, "deprecated", actor="operator")
    catalog.create_draft(orch.store, actor="operator", manifest=tool_manifest("app.draft"), files={})
    cmd(orch, "saveRunbookDraft", spec=spec(steps=[{"name": "a", "tool": "app.check@2"}]))
    run = vm.catalog_options(orch.store, "run")
    assert [t["id"] for t in run["tools"]] == ["app.check"] and run["tools"][0]["latest"] == 2
    assert [(v["ref"], v["dispatchable"]) for v in run["tools"][0]["versions"]] == [("app.check@2", True),
                                                                                    ("app.check@1", False)]
    assert run["runbooks"] == []
    test = vm.catalog_options(orch.store, "test")
    assert [t["id"] for t in test["tools"]] == ["app.draft"] and [r["default"] for r in test["runbooks"]] == ["web.check@1"]
    assert vm.catalog_options(orch.store, "step")["runbooks"] == []
    with pytest.raises(ValueError):
        vm.catalog_options(orch.store, "nope")


def test_labels_exclude_revoked_and_preview_matches_the_plan(orch):
    publish_tool(orch)
    a, _ = add_node(orch, "a", {"env": "prod", "role": "web"})
    b, _ = add_node(orch, "b", {"env": "prod", "role": "db"})
    c, _ = add_node(orch, "c", {"env": "dev"}, status="quarantined")
    d, _ = add_node(orch, "d", {"env": "prod", "secret": "x"})
    enroll.set_status(orch.store, d.id, "revoked", actor="operator")
    cmd(orch, "putGroup", name="prod", selector="env=prod")
    index = vm.label_index(orch.store)
    assert index["labels"]["env"] == {"dev": 1, "prod": 2} and "secret" not in index["labels"]
    assert {g["name"]: g["count"] for g in index["groups"]} == {"prod": 2}
    for selector in ({"labels": "env in (prod,dev)"}, {"groups": ["prod"]}, {"all": True}, {"nodes": [a.id, c.id]}):
        preview = cmd(orch, "previewSelector", actor="ai:s", selector=selector)
        plan = cmd(orch, "planRun", target="demo.echo@1", selector=selector)["plan"]
        assert [n["id"] for n in preview["nodes"]] == plan["nodes"], selector
        assert preview["excluded"] == plan["excluded"], selector
    assert cmd(orch, "previewSelector", selector="env=prod,role=web")["count"] == 1
    bad = cmd(orch, "previewSelector", selector="env in (")
    assert not bad["ok"] and bad["error"]


# --------------------------------------------------------------------- bulk upgrade

def test_upgrade_report_detects_every_consequence_and_stores_nothing(orch):
    publish_tool(orch, outputs_manifest("app.check"))
    publish_tool(orch, outputs_manifest("app.other"))
    m2 = outputs_manifest("app.check", effect="mutating", rollback="app.other@1")
    m2["inputs"]["properties"]["keep_days"]["maximum"] = 20
    del m2["outputs"]["properties"]["free_pct"]
    v2 = catalog.create_draft(orch.store, actor="operator", manifest=m2, files={"main.py": "x=1\n"})
    catalog.submit_for_review(orch.store, "app.check", v2.version, actor="operator")
    catalog.publish(orch.store, orch.keys, "app.check", v2.version, actor="operator")
    s = spec(steps=[{"name": "check", "tool": "app.check@1", "params": {"keep_days": 30}},
                    {"name": "other", "tool": "app.other@1", "when": "steps.check.outputs.free_pct < 5"}])
    before_seq = orch.store.last_change_seq()
    report = cmd(orch, "upgradeRunbookTools", spec=s)
    assert [r["name"] for r in report["steps"]] == ["check"]        # app.other has nothing newer
    step = report["steps"][0]
    assert step["from"] == "app.check@1" and step["to"] == "app.check@2" and step["selected"]
    assert step["effect"] == {**step["effect"], "from": "read_only", "to": "mutating", "changed": True}
    assert step["rejected_params"][0]["path"] == "steps[0].params.keep_days"
    assert step["removed_outputs"] == ["free_pct"] and step["removed_referenced"] == [{"step": "other", "output": "free_pct"}]
    assert step["rollback"] == {"from": "", "to": "app.other@1"} and "+effect: mutating" in step["diff"]
    assert report["effect"]["changed"] and report["decision"]["from"]["level"] == "allow"
    assert report["decision"]["to"]["level"] == "confirm"
    assert report["spec"]["steps"][0]["tool"] == "app.check@2"
    none = cmd(orch, "upgradeRunbookTools", spec=s, steps=[])
    assert none["spec"]["steps"][0]["tool"] == "app.check@1" and not none["steps"][0]["selected"]
    assert orch.store.last_change_seq() == before_seq and orch.store.list_runbook_versions() == []


# --------------------------------------------------------------------- node setup

def test_interfaces_are_classified_like_the_endpoint():
    rows = overlay.interfaces([{"interface": "eth0", "address": "8.8.8.8"},
                               {"interface": "wg0", "address": "10.77.0.1"},
                               {"interface": "lo", "address": "127.0.0.1"},
                               {"interface": "ts", "address": "100.64.0.9"}])
    by = {r["address"]: r for r in rows}
    for address, row in by.items():
        check = overlay.classify(address)
        assert (row["ok"], row["kind"], row["reason"]) == (check.ok, check.kind, check.reason)
    assert rows[-1]["address"] == "8.8.8.8" and not rows[-1]["ok"]       # public last, refused


def test_verify_node_is_an_ordinary_audited_run(orch, clock):
    node, _ = add_node(orch, "web1", {"env": "dev"})
    with pytest.raises(CommandRejected, match="starter pack"):
        cmd(orch, "verifyNode", node_id=node.id)
    orch.install_starter_pack()
    with pytest.raises(CommandRejected, match="requires the operator"):
        cmd(orch, "verifyNode", actor="ai:s", node_id=node.id)
    out = cmd(orch, "verifyNode", node_id=node.id)
    run = orch.store.get_run(out["run"]["id"])
    assert run.kind == "tool" and run.target_id == "sys.facts" and run.nodes == [node.id]
    assert run.title.startswith("Setup check") and run.state in ("approved", "running")
    assert any(e["action"] == "node.verify" and e["subject"] == node.id for e in orch.store.audit_entries(limit=10_000))
    pending, _ = add_node(orch, "new", status="pending")
    with pytest.raises(CommandRejected, match="accept the node first"):
        cmd(orch, "verifyNode", node_id=pending.id)


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


def _headers(c):
    return {"X-MuCLI-CSRF": c.get("/api/orchestration/csrf").json()["token"]}


def test_setup_downloads_need_csrf_and_a_live_token(client):
    c, orch = client
    h = _headers(c)
    orch.store.set_setting("network", {"bind": "10.0.0.5", "port": 30312, "advertise": ""})
    token = c.post("/api/orchestration/cmd", json={"type": "createJoinToken", "args": {"labels": {"env": "dev"},
                                                                                       "name": "web1"}},
                   headers=h).json()["data"]
    tid = token["token_id"]
    for path in (f"/api/orchestration/setup/{tid}", f"/api/orchestration/setup/{tid}/install.sh",
                 f"/api/orchestration/setup/{tid}/agent.pyz"):
        assert c.get(path).status_code == 403, path
    state = c.get(f"/api/orchestration/setup/{tid}", headers=h).json()
    assert state["available"] and state["used_at"] is None and state["labels"] == {"env": "dev"}
    script = c.get(f"/api/orchestration/setup/{tid}/install.sh", headers=h)
    assert script.status_code == 200
    text = script.text
    assert token["token"] in text and "MASTER=https://10.0.0.5:30312" in text and state["fingerprint"] in text
    assert f"PYZ_SHA256={state['pyz_sha256']}" in text and "NAME=web1" in text
    assert hashlib.sha256(script.content).hexdigest() == state["install_sha256"] == script.headers["x-content-sha256"]
    pyz = c.get(f"/api/orchestration/setup/{tid}/agent.pyz", headers=h)
    assert hashlib.sha256(pyz.content).hexdigest() == state["pyz_sha256"]
    downloads = [e for e in orch.store.audit_entries(limit=10_000) if e["action"] == "setup.download"]
    assert {e["payload"]["file"] for e in downloads} == {"install.sh", "mucli-node.pyz"}
    # Once the token is used it is gone; the wizard sees the node instead.
    reply = enroll.enroll(orch.store, orch.keys, token=token["token"], name="web1", facts={}, policy={},
                          agent_version="t")
    assert c.get(f"/api/orchestration/setup/{tid}/install.sh", headers=h).status_code == 410
    state = c.get(f"/api/orchestration/setup/{tid}", headers=h).json()
    assert not state["available"] and state["node"]["id"] == reply["node_id"] and "command" not in state
    assert state["name"] == "web1" and state["verify_run_id"] == ""      # the node's name once the token is gone
    assert c.get("/api/orchestration/setup/0123456789abcdef", headers=h).status_code == 404


def test_router_endpoints_for_pickers_and_conflicts(client):
    c, orch = client
    h = _headers(c)
    publish_tool(orch, outputs_manifest("app.check"))
    add_node(orch, "a", {"env": "dev"})
    assert c.get("/api/orchestration/catalog/options?purpose=step").json()["tools"][0]["id"] == "app.check"
    assert c.get("/api/orchestration/catalog/options?purpose=bogus").status_code == 400
    assert c.get("/api/orchestration/labels").json()["labels"] == {"env": {"dev": 1}}
    assert c.get("/api/orchestration/network/interfaces").json()["interfaces"]
    saved = c.post("/api/orchestration/cmd", json={"type": "saveRunbookDraft", "args": {"spec": spec()}},
                   headers=h).json()["data"]
    c.post("/api/orchestration/cmd", json={"type": "saveRunbookDraft", "args": {"spec": spec(title="other")}},
           headers=h)
    r = c.post("/api/orchestration/cmd", json={"type": "saveRunbookDraft",
                                               "args": {"spec": spec(title="mine"), "base_digest": saved["digest"]}},
               headers=h)
    assert r.status_code == 409 and r.json()["data"]["runbook"]["spec"]["title"] == "other"
    detail = c.get("/api/orchestration/runbooks/web.check/1").json()
    assert detail["digest"] == r.json()["data"]["digest"] and "notes" in detail and detail["parses"]


# --------------------------------------------------------------------- schema v2 migration

def test_v1_store_migrates_to_v2(tmp_path):
    root = tmp_path / "orch"
    store = OrchestrationStore(str(root))
    with store.conn() as conn:
        conn.execute("DROP TABLE runbook_versions")
        conn.execute("CREATE TABLE runbook_versions(runbook_id TEXT NOT NULL, version INTEGER NOT NULL, status TEXT NOT"
                     " NULL, spec TEXT NOT NULL, digest TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at"
                     " REAL NOT NULL, created_by TEXT NOT NULL DEFAULT '', published_at REAL, origin TEXT NOT NULL"
                     " DEFAULT 'manual', PRIMARY KEY(runbook_id, version))")
        conn.execute("INSERT INTO runbook_versions(runbook_id, version, status, spec, created_at, updated_at)"
                     " VALUES('old.rb', 1, 'published', '{\"id\": \"old.rb\"}', 1, 1)")
        conn.execute("UPDATE meta SET value='1' WHERE key='schema_version'")
    migrated = OrchestrationStore(str(root))
    assert migrated.schema_version() == 2
    rb = migrated.get_runbook_version("old.rb", 1)
    assert rb.review == {} and rb.source == "" and rb.spec == {"id": "old.rb"}
    cols = {r[1] for r in sqlite3.connect(str(root / "orch.sqlite3")).execute("PRAGMA table_info(runbook_versions)")}
    assert {"review", "source"} <= cols


# --------------------------------------------------------------------- CLI and AI

def test_cli_validates_a_runbook_directory(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    from mu.orchestration.cli import main
    assert main(["tools", "starter"]) == 0
    ops = tmp_path / "runbooks"
    ops.mkdir()
    (ops / "good.yaml").write_text("id: ops.disk\ntitle: Disk\nsteps:\n  - name: disk\n    tool: sys.disk-usage@1\n")
    assert main(["runbooks", "validate", str(ops)]) == 0
    (ops / "bad.yaml").write_text("id: ops.bad\ntitle: Bad\nsteps:\n  - name: disk\n    tool: sys.disk-usage@1\n"
                                  "    when: steps.later.state == 'succeeded'\n")
    capsys.readouterr()
    assert main(["runbooks", "validate", str(ops)]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out and "bad.yaml:6" in out and "not an earlier step" in out
    assert main(["runbooks", "save", str(ops / "good.yaml")]) == 0
    assert main(["runbooks", "submit", "ops.disk@1"]) == 0 and main(["runbooks", "publish", "ops.disk@1"]) == 0
    capsys.readouterr()
    assert main(["runbooks", "list"]) == 0 and "ops.disk@1" in capsys.readouterr().out


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


def test_ai_validates_saves_and_proposes_runbook_tests(ai_orch):
    orch = ai_orch
    publish_tool(orch, outputs_manifest("app.check"))
    add_node(orch, "canary1", {"env": "dev"}, canary=True)
    checked = _call("orch_runbook_validate", {"spec": spec(steps=[{"name": "a", "tool": "app.check@1",
                                                                    "params": {"keep_days": 900}}])})
    assert checked["ok"] and not checked["data"]["ok"] and "steps[0].params.keep_days" in checked["data"]["problems"][0]
    saved = _call("orch_runbook_draft", {"spec": spec()})
    assert saved["ok"] and saved["data"]["problems"] == [] and saved["data"]["digest"]
    dispatch(orch, {"type": "saveRunbookDraft", "args": {"spec": spec(title="operator edit")}})
    stale = _call("orch_runbook_draft", {"spec": spec(), "base_digest": saved["data"]["digest"]})
    assert not stale["ok"] and "changed since you opened it" in stale["message"]
    test = _call("orch_runbook_test", {"ref": "web.check@1"})
    assert test["ok"] and test["data"]["state"] == "proposed"
