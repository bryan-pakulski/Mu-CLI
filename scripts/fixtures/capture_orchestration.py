#!/usr/bin/env python3
"""Capture orchestration view models for the Android tests (documentation/plans/mobile_parity.md §8).

Builds a small fleet in a throwaway MUCLI_HOME with the real orchestration code (the same helpers
the Python tests use), then saves what the GUI's REST routes answer into
mobile/android/__tests__/fixtures/server/orchestration/*.json. Re-run it after a view-model change
so the phone is tested against today's shapes:

    python3 scripts/fixtures/capture_orchestration.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "mobile" / "android" / "__tests__" / "fixtures" / "server" / "orchestration"


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="mucli-fixtures-"))
    os.environ["MUCLI_HOME"] = str(tmp / "home")
    sys.path[:0] = [str(ROOT), str(ROOT / "tests" / "helpers")]

    from fastapi.testclient import TestClient
    from mu.gui.app import create_app
    from mu.gui.routers import orchestration as router
    from mu.orchestration import catalog
    from orch_helpers import FakeNode, add_node, make_orch, publish_tool, tool_manifest

    orch = make_orch(tmp)
    app = create_app(args=SimpleNamespace(provider=None, model=None), build_session_fn=lambda *a, **k: None)
    app.state.orchestrator = orch
    router._CURSOR["seq"] = -1
    c = TestClient(app)
    headers = {"X-MuCLI-CSRF": c.get("/api/orchestration/csrf").json()["token"]}

    def cmd(type_, **args):
        r = c.post("/api/orchestration/cmd", json={"type": type_, "args": args}, headers=headers)
        assert r.status_code == 200, (type_, r.status_code, r.text)
        return r.json()["data"]

    cmd("installStarterPack")
    canary, _ = add_node(orch, "web-1", {"env": "prod", "role": "web"}, canary=True)
    web2, _ = add_node(orch, "web-2", {"env": "prod", "role": "web"})
    add_node(orch, "db-new", {"env": "prod", "role": "db"}, status="pending")
    publish_tool(orch, tool_manifest("demo.echo"))
    publish_tool(orch, tool_manifest("demo.restart", effect="mutating"))

    # A run that finished on both nodes (results), and one awaiting approval.
    fakes = [FakeNode(orch, canary), FakeNode(orch, web2)]
    done = cmd("proposeRun", target="demo.echo@1", selector={"labels": "role=web"}, params={"message": "hello"},
               title="Say hello")["run"]
    if done["state"] == "proposed":
        cmd("approveRun", run_id=done["id"])
    for _ in range(6):
        orch.tick()
        for f in fakes:
            for env in f.poll()["dispatches"]:
                f.finish(env, output={"message": f"hello from {f.node.name}"}, log="message: hello\n")
    proposed = cmd("proposeRun", target="demo.restart@1", selector={"labels": "role=web"}, title="Restart web")["run"]

    # A tool draft and a runbook draft for the studio.
    draft = cmd("createDraft", tool_id="demo.rotate", intent="Rotate logs older than N days.")
    rb = cmd("saveRunbookDraft", spec={"id": "web.safe-check", "title": "Safe check",
                                       "steps": [{"name": "echo", "tool": "demo.echo@1", "params": {"message": "x"}},
                                                 {"name": "bad", "tool": ""}]})
    tool_id, version = draft["ref"].split("@")

    # Node groups and the dashboard: facts for health (web-2's disk is nearly full), a selector
    # group and a members-only group; db-new (pending) stays ungrouped.
    from mu.orchestration.node import AGENT_VERSION
    now = orch.store.now()
    for node, disk in ((canary, 40), (web2, 96)):
        orch.store.update_node(node.id, agent_version=AGENT_VERSION, facts_at=now, last_seen=now, facts={
            "memory": {"total_bytes": 8 * 2**30, "used_pct": 52}, "disks": [{"mount": "/", "used_pct": disk}],
            "uptime": {"uptime_s": 86400 * 3, "load": [0.4]}, "cpu_count": 2, "os": {"pretty_name": "Debian 12"}})
    # Node history: six hours of samples every two minutes for the canary and web-2, with the
    # canary off for half an hour (a gap the charts must show, not bridge).
    import math
    for i in range(180):
        t = now - 6 * 3600 + i * 120
        if not (60 <= i < 75):
            orch.store.add_metrics(canary.id, t, {"cpu_pct": round(20 + 15 * math.sin(i / 9), 1), "mem_pct": 52.0, "disk_pct": 40.0,
                                                  "load1": 0.4, "net_rx_bps": 20000.0 + i * 100, "net_tx_bps": 8000.0,
                                                  "disk_read_bps": 1024.0, "disk_write_bps": 4096.0})
        orch.store.add_metrics(web2.id, t, {"cpu_pct": 70.0, "mem_pct": 60.0, "disk_pct": 96.0, "load1": 1.6,
                                            "net_rx_bps": 50000.0, "net_tx_bps": 30000.0})
    cmd("putGroup", name="web", selector="role=web", description="Front ends")
    cmd("putGroup", name="canaries", members=[canary.id])
    rb_id, rb_v = rb["ref"].split("@")

    shots = {
        "overview": "/api/orchestration/overview",
        "approvals": "/api/orchestration/approvals",
        "nodes": "/api/orchestration/nodes",
        "node": f"/api/orchestration/nodes/{canary.id}",
        "maintenance": f"/api/orchestration/nodes/{canary.id}/maintenance",
        "runs": "/api/orchestration/runs",
        "run": f"/api/orchestration/runs/{done['id']}",
        "run_proposed": f"/api/orchestration/runs/{proposed['id']}",
        "run_results": f"/api/orchestration/runs/{done['id']}/results",
        "tools": "/api/orchestration/tools?all=true",
        "tool": "/api/orchestration/tools/demo.echo/1",
        "tool_draft": f"/api/orchestration/tools/{tool_id}/{version}",
        "runbook": f"/api/orchestration/runbooks/{rb_id}/{rb_v}",
        "settings": "/api/orchestration/settings",
        "audit": "/api/orchestration/audit?limit=10",
        "dashboard": "/api/orchestration/dashboard",
        "dashboard_group": "/api/orchestration/dashboard?group=web",
        "dashboard_ungrouped": "/api/orchestration/dashboard?group=-",
        "groups": "/api/orchestration/groups",
        "node_metrics": f"/api/orchestration/nodes/{canary.id}/metrics?range=6h",
        "dashboard_metrics": "/api/orchestration/dashboard/metrics?range=6h",
        "dashboard_metrics_per_node": "/api/orchestration/dashboard/metrics?range=6h&per_node=1",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    for name, path in shots.items():
        r = c.get(path)
        assert r.status_code == 200, (path, r.status_code, r.text)
        (OUT / f"{name}.json").write_text(json.dumps(r.json(), indent=1, sort_keys=True) + "\n")
    # A node whose agent predates history (1.3.0): captured last so the other fixtures see web-2 current.
    orch.store.update_node(web2.id, agent_version="1.2.0")
    r = c.get(f"/api/orchestration/nodes/{web2.id}/metrics?range=6h")
    assert r.status_code == 200, r.text
    (OUT / "node_metrics_outdated.json").write_text(json.dumps(r.json(), indent=1, sort_keys=True) + "\n")
    orch.store.update_node(web2.id, agent_version=AGENT_VERSION)
    validate = cmd("validateRunbook", spec=json.loads((OUT / "runbook.json").read_text())["spec"])
    (OUT / "validate_runbook.json").write_text(json.dumps(validate, indent=1, sort_keys=True) + "\n")
    orch.stop()
    print(f"wrote {len(shots) + 1} fixtures to {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
