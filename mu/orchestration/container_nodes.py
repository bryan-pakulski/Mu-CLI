"""Disposable test nodes (plan §6.8): a canary node inside a throwaway container.

The master starts a container from a chosen image (any image with Python 3.10+), mounts the
node zipapp read-only, writes a permissive *disposable* node policy (``canary: true``,
destructive allowed — nothing on it matters), enrols with a one-time join token and accepts
the node automatically because the operator started it. The agent runs with
``--insecure-no-sandbox``: containers have no systemd, and the container *is* the sandbox.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from typing import Any, Dict

from . import enroll
from .zipapp import build_zipapp

DEFAULT_IMAGE = "python:3.12-slim"
LABEL = "mucli.orchestration.testnode"
DISPOSABLE_POLICY = """accept_categories: ["*"]
max_effect: destructive
max_privilege: root
allowed_tools: ["*"]
canary: true
"""
_STATUS: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.Lock()


def _docker() -> str:
    exe = shutil.which("docker")
    if not exe:
        raise RuntimeError("docker is not installed on the master")
    return exe


def statuses() -> Dict[str, Dict[str, Any]]:
    with _LOCK:
        return {k: dict(v) for k, v in _STATUS.items()}


def start_test_node(orch, *, image: str = "", name: str = "", wait: bool = False) -> Dict[str, Any]:
    docker = _docker()
    endpoint = orch.network()
    url = orch.master_url()
    if not url:
        raise RuntimeError("configure and start the node endpoint first (Settings > Network)")
    image = image or DEFAULT_IMAGE
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_./:@-]{0,200}$", image):
        raise RuntimeError("invalid image name")
    name = re.sub(r"[^a-z0-9-]", "-", (name or f"testnode-{int(time.time()) % 100000}").lower())[:40]
    container = f"mucli-{name}"
    token = enroll.create_join_token(orch.store, labels={"kind": "test", "canary": "true"}, canary=True,
                                     ttl_s=900, note=f"test node {container}")
    fingerprint = orch.keys.tls_identity([endpoint.get("bind") or "", endpoint.get("advertise") or ""]).display_fingerprint
    data, _sha = build_zipapp()
    pyz_dir = os.path.join(orch.root, "testnodes")
    os.makedirs(pyz_dir, exist_ok=True)
    pyz = os.path.join(pyz_dir, "mucli-node.pyz")
    with open(pyz, "wb") as fh:
        fh.write(data)
    os.chmod(pyz, 0o644)
    network = ["--network", "host"] if (endpoint.get("bind") or "").startswith(("127.", "::1")) else []
    script = (
        "set -e; mkdir -p /etc/mucli-node /var/lib/mucli-node; "
        "printf '%s' \"$MUCLI_POLICY\" > /etc/mucli-node/policy.yaml; "
        "python3 /opt/mucli-node.pyz --state-dir /var/lib/mucli-node --etc-dir /etc/mucli-node enroll "
        "--master \"$MUCLI_MASTER\" --token \"$MUCLI_JOIN_TOKEN\" --fingerprint \"$MUCLI_FINGERPRINT\" "
        f"--name {name}; unset MUCLI_JOIN_TOKEN; "
        "exec python3 /opt/mucli-node.pyz --state-dir /var/lib/mucli-node --etc-dir /etc/mucli-node run "
        "--insecure-no-sandbox"
    )
    argv = [docker, "run", "-d", "--name", container, "--label", f"{LABEL}=1", "--memory", "512m",
            "--pids-limit", "256", *network, "-v", f"{pyz}:/opt/mucli-node.pyz:ro",
            "-e", f"MUCLI_MASTER={url}", "-e", f"MUCLI_JOIN_TOKEN={token['token']}",
            "-e", f"MUCLI_FINGERPRINT={fingerprint}", "-e", f"MUCLI_POLICY={DISPOSABLE_POLICY}",
            image, "sh", "-c", script]
    with _LOCK:
        _STATUS[container] = {"container": container, "state": "starting", "image": image, "node_id": "",
                              "error": "", "started_at": time.time()}

    def _run():
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=600)
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip()[-500:] or "docker run failed")
            deadline = time.time() + 120
            node_id = ""
            while time.time() < deadline and not node_id:
                with orch.store.conn() as conn:
                    row = conn.execute("SELECT node_id FROM join_tokens WHERE note=? AND node_id IS NOT NULL",
                                       (f"test node {container}",)).fetchone()
                node_id = row["node_id"] if row else ""
                if not node_id:
                    time.sleep(1)
            if not node_id:
                raise RuntimeError("the test node did not enrol within 2 minutes (can it reach the node endpoint?)")
            with orch.store.tx() as conn:
                orch.store.update_node(node_id, conn=conn, kind="container")
                containers = dict(orch.store.get_setting("test_nodes", {}) or {})
                containers[node_id] = container
                orch.store.set_setting("test_nodes", containers, conn=conn)
            enroll.set_status(orch.store, node_id, "active", actor="operator", reason="disposable test node")
            with _LOCK:
                _STATUS[container].update(state="ready", node_id=node_id)
            orch.store.change("node", node_id, ["state"])
        except Exception as exc:  # noqa: BLE001 - surfaced through statuses()
            with _LOCK:
                _STATUS[container].update(state="failed", error=str(exc)[:500])
            orch.store.change("node", "", ["state"])
            orch.store.audit("operator", "testnode.failed", container, {"error": str(exc)[:500]})

    orch.store.audit("operator", "testnode.start", container, {"image": image})
    orch.store.change("node", "", ["state"])
    thread = threading.Thread(target=_run, name=f"testnode-{container}", daemon=True)
    thread.start()
    if wait:
        thread.join()
    return statuses()[container]


def stop_test_node(orch, node_id: str) -> Dict[str, Any]:
    docker = _docker()
    containers = dict(orch.store.get_setting("test_nodes", {}) or {})
    container = containers.pop(node_id, "")
    if not container:
        raise RuntimeError("that node is not a disposable test node")
    subprocess.run([docker, "rm", "-f", container], capture_output=True, timeout=60)
    orch.store.set_setting("test_nodes", containers)
    node = orch.store.get_node(node_id)
    if node is not None and node.status != "revoked":
        enroll.set_status(orch.store, node_id, "revoked", actor="operator", reason="test node removed")
    orch.store.audit("operator", "testnode.stop", container, {"node_id": node_id})
    with _LOCK:
        _STATUS.pop(container, None)
    return {"node_id": node_id, "container": container, "removed": True}
