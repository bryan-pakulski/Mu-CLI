"""Shared helpers for the orchestration tests (imported as a plain module, not collected)."""

from __future__ import annotations

import os
import tempfile
from typing import Any, Dict, List, Mapping, Optional

from mu.orchestration import catalog, enroll
from mu.orchestration.node.envelope import b64d
from mu.orchestration.node.executor import Executor, Keys, authorize
from mu.orchestration.node.policy import NodePolicy, parse_policy
from mu.orchestration.node.store import NodeStore
from mu.orchestration.service import Orchestrator


class Clock:
    """Starts at the real time: node-side checks (deadline, clock skew) use the real clock."""

    def __init__(self, start: Optional[float] = None):
        import time
        self.t = float(time.time() if start is None else start)

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> float:
        self.t += seconds
        return self.t


def make_orch(tmp_path, clock=None) -> Orchestrator:
    return Orchestrator(str(tmp_path / "orch"), clock=clock or Clock())


LINUX_FACTS = {"system": "linux", "systemd": True, "provides": ["systemd", "ss", "openssl"],
               "os": {"id": "debian", "pretty_name": "Debian 12"}, "kernel": "6.1.0", "cpu_count": 2}


def add_node(orch, name: str, labels: Optional[Mapping[str, str]] = None, *, canary: bool = False,
             facts: Optional[Dict[str, Any]] = None, status: str = "active", policy: Optional[Dict] = None):
    token = enroll.create_join_token(orch.store, labels=labels or {}, canary=canary)
    reply = enroll.enroll(orch.store, orch.keys, token=token["token"], name=name,
                          facts=dict(LINUX_FACTS) if facts is None else facts, policy=policy or {},
                          agent_version="test")
    if status != "pending":
        enroll.set_status(orch.store, reply["node_id"], "active", actor="operator")
        if status != "active":
            enroll.set_status(orch.store, reply["node_id"], status, actor="operator")
    return orch.store.get_node(reply["node_id"]), reply["credential"]


def tool_manifest(tool_id: str = "demo.echo", *, effect: str = "read_only", **extra) -> Dict[str, Any]:
    manifest = {
        "id": tool_id, "title": f"Tool {tool_id}", "summary": "test tool", "category": "diagnostics",
        "effect": effect, "idempotent": True,
        "inputs": {"type": "object", "properties": {"message": {"type": "string", "maxLength": 200, "default": "hi"}},
                   "additionalProperties": False},
        "outputs": {"type": "object", "properties": {"message": {"type": "string"}}},
        "implementation": {"runtime": "python3", "entry": "main.py", "check_mode": True},
        "privilege": {"run_as": "root"},
    }
    if effect != "read_only":
        manifest["approval"] = "confirm"
    manifest.update(extra)
    return manifest


ECHO_PY = (
    "import json, os\n"
    "params = json.load(open(os.environ['MUCLI_PARAMS']))\n"
    "print('message:', params.get('message'))\n"
    "json.dump({'message': params.get('message')}, open(os.environ['MUCLI_RESULT'], 'w'))\n"
)


def publish_tool(orch, manifest: Optional[Dict[str, Any]] = None, files: Optional[Dict[str, str]] = None, *,
                 typed: str = ""):
    manifest = manifest or tool_manifest()
    tv = catalog.create_draft(orch.store, actor="operator", manifest=manifest,
                              files=files if files is not None else {"main.py": ECHO_PY})
    catalog.submit_for_review(orch.store, tv.tool_id, tv.version, actor="operator", headline="test")
    return catalog.publish(orch.store, orch.keys, tv.tool_id, tv.version, actor="operator",
                           typed=typed or (tv.tool_id if manifest.get("effect") == "destructive" else ""))


class FakeNode:
    """A node driven in-process through the hub (no HTTP).

    ``poll()`` heartbeats and claims envelopes; ``finish(...)`` reports a result. With
    ``execute=True`` envelopes go through the real node ``authorize`` + ``Executor`` (sandbox
    ``none``) in a private node state dir.
    """

    def __init__(self, orch, node, *, policy_text: str = "canary: true\nmax_effect: destructive\n",
                 tmp: Optional[str] = None):
        self.orch = orch
        self.node = node
        self.policy: NodePolicy = parse_policy(policy_text)
        self.dir = tmp or tempfile.mkdtemp(prefix="fakenode-")
        self.store = NodeStore(os.path.join(self.dir, "state"))
        self.replay = self.store.replay_cache()
        self.keys = Keys(orch.keys.signing_public, orch.keys.test_public)
        self.executor = Executor(self.store, sandbox="none")
        self.running: List[str] = []

    def refresh(self):
        self.node = self.orch.store.get_node(self.node.id)
        return self.node

    def poll(self, facts: Optional[Dict[str, Any]] = None):
        self.refresh()
        body: Dict[str, Any] = {"agent_version": "test", "policy": self.policy.summary()}
        if facts is not None:
            body["facts"] = facts
        reply = self.orch.hub.heartbeat(self.node, body)
        self.refresh()
        dispatches, cancels = self.orch.hub.claim(self.node, list(self.running))
        reply.update(dispatches=dispatches, cancels=cancels)
        return reply

    def event(self, **event):
        return self.orch.hub.events(self.node, [event])

    def finish(self, envelope, state: str = "succeeded", *, output: Any = None, exit_code: int = 0,
               log: str = "", reason: str = ""):
        return self.event(type="result", exec_id=envelope["exec_id"],
                          result={"exec_id": envelope["exec_id"], "state": state, "exit_code": exit_code,
                                  "output": output, "log": log, "reason": reason, "duration_s": 0.1})

    def execute(self, envelope):
        """Authorize and run an envelope like the real agent does; report the result."""
        ok, reason, params = authorize(envelope, node_id=self.node.id, keys=self.keys, policy=self.policy,
                                       replay=self.replay, check_mode_passed=self.store.check_mode_passed())
        if not ok:
            self.event(type="result", exec_id=envelope["exec_id"],
                       result={"exec_id": envelope["exec_id"], "state": "refused", "reason": reason})
            return {"state": "refused", "reason": reason}
        for name, sha in envelope["blobs"].items():
            if not self.store.has_blob(sha):
                data = self.orch.hub.blob(self.node, sha)
                self.store.put_blob(sha, data)
        self.event(type="running", exec_id=envelope["exec_id"])
        result = self.executor.execute(envelope, params, secrets=envelope.get("secrets") or {})
        self.event(type="result", exec_id=envelope["exec_id"], result=result.to_dict())
        return result.to_dict()

    def run_all(self) -> List[Dict[str, Any]]:
        reply = self.poll()
        return [self.execute(env) for env in reply["dispatches"]]


def drive(orch, nodes: List[FakeNode], *, rounds: int = 30, advance: float = 1.0, clock: Optional[Clock] = None):
    """Alternate scheduler ticks and node executions until nothing more happens."""
    for _ in range(rounds):
        orch.tick()
        results = [r for node in nodes for r in node.run_all()]
        if clock is not None:
            clock.advance(advance)
        if not results:
            orch.tick()
            if not [r for node in nodes for r in node.run_all()]:
                break


def b64(data: bytes) -> str:
    import base64
    return base64.b64encode(data).decode()


__all__ = ["Clock", "make_orch", "add_node", "tool_manifest", "publish_tool", "FakeNode", "drive", "b64d", "b64",
           "LINUX_FACTS", "ECHO_PY"]
