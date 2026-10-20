"""Fleet results (mu/orchestration/results.py): a tool's output schema becomes sortable tables
across nodes — one row per node, and one row per item for arrays of objects — for a run and
for a tool's latest results across the fleet, with CSV export that neutralises formulas."""

from __future__ import annotations

import csv
import io
from types import SimpleNamespace

import pytest

from mu.orchestration import results

from orch_helpers import FakeNode, add_node, make_orch, publish_tool, tool_manifest

DISK_OUTPUTS = {
    "type": "object",
    "properties": {
        "disks": {"type": "array", "items": {"type": "object", "properties": {
            "mount": {"type": "string"}, "total_bytes": {"type": "integer"}, "used_pct": {"type": "number"}}}},
        "max_used_pct": {"type": ["number", "null"]},
        "packages": {"type": "array", "items": {"type": "string"}},
    },
}


def test_schema_becomes_a_summary_and_an_item_view():
    summary, disks = results.views(DISK_OUTPUTS)
    assert summary["per"] == "node" and summary["metric"] == "max_used_pct" and summary["order"] == "desc"
    keys = [c["key"] for c in summary["columns"]]
    # Counts, then previews, then the key metric last (beside its meter).
    assert keys == ["disks__count", "packages__count", "packages__items", "max_used_pct"]
    assert disks["key"] == "disks" and disks["per"] == "item" and disks["metric"] == "used_pct"
    assert [(c["key"], c["format"]) for c in disks["columns"]] == [
        ("mount", "text"), ("total_bytes", "bytes"), ("used_pct", "percent")]
    assert [c["label"] for c in disks["columns"]] == ["Mount", "Total", "Used %"]
    # Stored manifests sort keys alphabetically; identity columns still lead.
    stored = {"type": "object", "properties": {"disks": {"type": "array", "items": {"type": "object", "properties": {
        "free_bytes": {"type": "integer"}, "fs": {"type": "string"}, "mount": {"type": "string"},
        "used_pct": {"type": "number"}}}}}}
    assert [c["key"] for c in results.views(stored)[1]["columns"]] == ["mount", "fs", "free_bytes", "used_pct"]
    # Days sort soonest first (certificates expiring soonest).
    certs = {"type": "object", "properties": {"certs": {"type": "array", "items": {"type": "object", "properties": {
        "path": {"type": "string"}, "days_left": {"type": "integer"}}}}}}
    assert results.views(certs)[1]["metric"] == "days_left" and results.views(certs)[1]["order"] == "asc"


def test_rows_sort_by_the_key_metric_and_keep_failed_nodes_visible():
    nodes = {"a": SimpleNamespace(name="web-1", labels={"env": "prod"}), "b": SimpleNamespace(name="db-1", labels={}),
             "c": SimpleNamespace(name="cache-1", labels={})}
    ex = lambda nid, out, state="succeeded": SimpleNamespace(id=f"x_{nid}", node_id=nid, output=out, state=state,
                                                            finished_at=1.0, version=1, run_id="r")
    execs = [ex("a", {"disks": [{"mount": "/", "used_pct": 40.0}], "max_used_pct": 40.0, "packages": list("abcdefg")}),
             ex("b", {"disks": [{"mount": "/", "used_pct": 91.5}, {"mount": "/var", "used_pct": 12}], "max_used_pct": 91.5,
                      "packages": []}),
             ex("c", None, state="failed")]
    summary, disks = results.views(DISK_OUTPUTS)
    rows = results.build(summary, execs, nodes)
    assert [r["node"] for r in rows] == ["db-1", "web-1", "cache-1"]      # highest use first, failures last
    assert rows[2]["state"] == "failed" and rows[2]["values"]["max_used_pct"] is None
    assert rows[1]["values"]["packages__count"] == 7 and rows[1]["values"]["packages__items"] == "a, b, c, d, e … +2"
    items = results.build(disks, execs, nodes)
    assert [(r["node"], r["values"]["mount"]) for r in items] == [("db-1", "/"), ("web-1", "/"), ("db-1", "/var")]


def test_tools_without_a_schema_infer_columns_from_their_outputs():
    summary = results.views(None, [{"count": 3, "ok": True, "name": "x", "items": [{"a": 1}]}])
    assert [c["key"] for c in summary[0]["columns"]] == ["name", "ok", "items__count", "count"]
    assert summary[1]["key"] == "items"


def test_csv_writes_raw_values_and_neutralises_formulas():
    table = {"columns": [{"key": "mount", "label": "Mount"}, {"key": "used_pct", "label": "Used %"}],
             "rows": [{"node": "=HYPERLINK(\"http://evil\")", "node_id": "n1", "labels": {"env": "prod"}, "state": "succeeded",
                       "values": {"mount": "@SUM(A1)", "used_pct": -4.5}}]}
    rows = list(csv.reader(io.StringIO(results.to_csv(table))))
    assert rows[0] == ["node", "node_id", "labels", "state", "Mount", "Used %"]
    assert rows[1][0].startswith("'=") and rows[1][4] == "'@SUM(A1)" and rows[1][5] == "-4.5"
    assert rows[1][2] == "env=prod"


@pytest.fixture
def orch(tmp_path):
    return make_orch(tmp_path)


def _finish_all(orch, fakes, outputs, run_id):
    for _ in range(20):          # canary first, then the remaining batches
        orch.tick()
        for fake in fakes:
            for env in fake.poll()["dispatches"]:
                fake.finish(env, output=outputs[fake.node.name])
        orch.tick()
        if orch.store.get_run(run_id).state in ("succeeded", "failed", "paused"):
            return


def test_run_and_latest_results_through_the_store_and_the_api(orch, tmp_path, monkeypatch):
    publish_tool(orch, tool_manifest("sys.disk", outputs=DISK_OUTPUTS))
    fakes = [FakeNode(orch, add_node(orch, name, {"env": "dev"})[0]) for name in ("web-1", "db-1")]
    run = orch.create_run(actor="operator", kind="tool", target="sys.disk@1", selector={"labels": "env=dev"})
    _finish_all(orch, fakes, {"web-1": {"disks": [{"mount": "/", "total_bytes": 10, "used_pct": 30}], "max_used_pct": 30, "packages": []},
                              "db-1": {"disks": [{"mount": "/", "total_bytes": 20, "used_pct": 88}], "max_used_pct": 88, "packages": ["x"]}}, run.id)
    data = results.run_results(orch.store, orch.store.get_run(run.id))
    step = data["steps"][0]
    assert step["tool"] == "sys.disk@1" and step["nodes"] == 2 and step["with_output"] == 2
    summary = step["tables"][0]
    assert [r["node"] for r in summary["rows"]] == ["db-1", "web-1"]
    assert [t["key"] for t in step["tables"]] == ["summary", "disks"]
    latest = results.latest_results(orch.store, "sys.disk")
    assert latest["nodes"] == 2 and latest["tables"][0]["rows"][0]["values"]["max_used_pct"] == 88

    from fastapi.testclient import TestClient
    from mu.gui.app import create_app
    app = create_app(args=SimpleNamespace(provider=None, model=None), build_session_fn=lambda *a, **k: None)
    app.state.orchestrator = orch
    client = TestClient(app)
    body = client.get(f"/api/orchestration/runs/{run.id}/results").json()
    assert body["steps"][0]["tables"][1]["rows"][0]["values"]["used_pct"] == 88
    csv_resp = client.get(f"/api/orchestration/runs/{run.id}/results.csv", params={"step": 0, "view": "disks"})
    assert csv_resp.status_code == 200 and csv_resp.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=" in csv_resp.headers["content-disposition"]
    assert csv_resp.text.splitlines()[1].startswith("db-1,")
    assert client.get("/api/orchestration/tools/sys.disk/results").json()["nodes"] == 2
    assert client.get("/api/orchestration/tools/sys.disk/results.csv").status_code == 200
    # The version route still serves tool versions.
    assert client.get("/api/orchestration/tools/sys.disk/1").status_code == 200
    assert client.get("/api/orchestration/tools/nope/results").status_code == 404
