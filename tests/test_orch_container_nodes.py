"""Disposable container test nodes (plan §6.8). Opt-in: needs Docker and pulls an image.

Run with ``MUCLI_ORCH_DOCKER_TESTS=1 python3 -m pytest tests/test_orch_container_nodes.py``.
"""

from __future__ import annotations

import os
import shutil
import socket
import time

import pytest

from mu.orchestration import catalog
from mu.orchestration.commands import dispatch
from mu.orchestration.server import NodeEndpoint

from orch_helpers import make_orch, tool_manifest

pytestmark = pytest.mark.skipif(
    os.environ.get("MUCLI_ORCH_DOCKER_TESTS") != "1" or not shutil.which("docker"),
    reason="set MUCLI_ORCH_DOCKER_TESTS=1 (requires Docker) to run container node tests",
)


def test_container_node_enrols_and_runs_a_draft_test(tmp_path):
    orch = make_orch(tmp_path, clock=time.time)
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    orch.store.set_setting("network", {"bind": "127.0.0.1", "port": port})
    endpoint = NodeEndpoint(orch)
    assert endpoint.start("127.0.0.1", port)["running"]
    orch.start()
    from mu.orchestration.container_nodes import start_test_node, stop_test_node
    status = start_test_node(orch, name="pytest-node", wait=True)
    try:
        assert status["state"] == "ready", status
        node = orch.store.get_node(status["node_id"])
        assert node.status == "active" and node.kind == "container" and node.canary
        script = ("import json, os, platform\n"
                  "json.dump({'message': platform.node()}, open(os.environ['MUCLI_RESULT'], 'w'))\n")
        tv = catalog.create_draft(orch.store, actor="ai:test", manifest=tool_manifest("demo.where"),
                                  files={"main.py": script})
        run = dispatch(orch, {"type": "proposeTest", "args": {"ref": tv.ref, "check_mode": False}},
                       actor="ai:test").data["run"]
        dispatch(orch, {"type": "approveRun", "args": {"run_id": run["id"]}})
        deadline = time.time() + 90
        while time.time() < deadline and orch.store.get_run(run["id"]).state not in ("succeeded", "failed"):
            time.sleep(1)
        final = orch.store.get_run(run["id"])
        ex = orch.store.list_executions(run_id=run["id"])[0]
        assert final.state == "succeeded", (final.reason, ex.reason, ex.log)
        assert ex.output["message"]
    finally:
        if status.get("node_id"):
            stop_test_node(orch, status["node_id"])
        orch.stop()
        endpoint.stop()
