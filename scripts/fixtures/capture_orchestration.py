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
    }
    OUT.mkdir(parents=True, exist_ok=True)
    for name, path in shots.items():
        r = c.get(path)
        assert r.status_code == 200, (path, r.status_code, r.text)
        (OUT / f"{name}.json").write_text(json.dumps(r.json(), indent=1, sort_keys=True) + "\n")
    validate = cmd("validateRunbook", spec=json.loads((OUT / "runbook.json").read_text())["spec"])
    (OUT / "validate_runbook.json").write_text(json.dumps(validate, indent=1, sort_keys=True) + "\n")
    orch.stop()
    print(f"wrote {len(shots) + 1} fixtures to {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
