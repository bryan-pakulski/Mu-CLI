"""Agent self-update on the node (documentation/plans/runbook_editor.md follow-up).

The master sends a signed ``agent_update`` order naming a version and the SHA-256 of
``mucli-node.pyz``. Nothing happens unless the node's own ``policy.yaml`` says
``allow_agent_updates: true``. The agent checks the order, waits until no execution is running,
downloads the agent and checks its digest, then hands it to the root helper, which trusts
nothing: it re-verifies the order with the root-owned key, re-reads the root-owned policy,
re-checks the digest, test-runs the new agent (``version`` must print the ordered version),
keeps the old one as ``mucli-node.pyz.prev`` and swaps the file atomically. The agent then
exits and systemd starts the new one, which reports the result: an update only counts as done
when the new agent is actually running.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from typing import Any, Dict, Mapping, Optional, Tuple

from .envelope import ReplayCache, verify_update
from .policy import NodePolicy, decide_update

AGENT_PATH = "/usr/local/lib/mucli-node/mucli-node.pyz"
MAX_AGENT_BYTES = 16 * 1024 * 1024
MARKER = "update.json"
SMOKE_TIMEOUT_S = 60


def check_file(path: str, sha256: str) -> Optional[str]:
    """A problem with the staged agent file, or None."""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return f"agent file missing: {exc}"
    if size > MAX_AGENT_BYTES:
        return "agent file is too large"
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    if h.hexdigest() != sha256:
        return "agent digest mismatch; refusing to install"
    if not zipfile.is_zipfile(path):
        return "agent file is not a zipapp"
    with zipfile.ZipFile(path) as zf:
        if "mucli_node/__init__.py" not in zf.namelist():
            return "agent file is not mucli-node"
    return None


def smoke_test(path: str, version: str) -> Optional[str]:
    """Run ``python3 -I <agent> version`` on this node's Python before trusting it."""
    try:
        out = subprocess.run([sys.executable, "-I", path, "version"], capture_output=True, text=True,
                             timeout=SMOKE_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"the new agent didn't start: {exc}"
    if out.returncode != 0:
        return "the new agent didn't start: " + (out.stderr.strip().splitlines() or ["no output"])[-1][:300]
    if out.stdout.strip() != version:
        return f"the new agent reports version {out.stdout.strip()!r}, expected {version!r}"
    return None


def install(order: Mapping[str, Any], staged: str, *, node_id: str, signing_key: bytes, policy: NodePolicy,
            replay: ReplayCache, agent_path: str = AGENT_PATH, now: Optional[float] = None) -> Tuple[bool, str]:
    """The root helper's half: verify everything again, test, keep ``.prev``, swap atomically."""
    now = time.time() if now is None else now
    check = verify_update(order, node_id=node_id, signing_key=signing_key, replay=replay, now=now)
    if not check.ok:
        return False, check.reason
    decision = decide_update(policy, now=now)
    if not decision.allowed:
        return False, decision.reason
    version = str(order.get("version") or "")
    problem = check_file(staged, str(order["sha256"])) or smoke_test(staged, version)
    if problem:
        return False, problem
    target_dir = os.path.dirname(agent_path)
    tmp = os.path.join(target_dir, ".mucli-node.pyz.new")
    shutil.copyfile(staged, tmp)
    os.chmod(tmp, 0o755)
    if check_file(tmp, str(order["sha256"])):
        os.unlink(tmp)
        return False, "agent changed while installing"
    if os.path.exists(agent_path):
        shutil.copy2(agent_path, agent_path + ".prev")
    os.replace(tmp, agent_path)
    return True, f"installed mucli-node {version} (previous agent kept as {agent_path}.prev)"


def stage(state_root: str, data: bytes, order: Mapping[str, Any]) -> str:
    """Write the downloaded agent and the helper's spec under the agent's state dir; returns the spec path."""
    folder = os.path.join(state_root, "update")
    os.makedirs(folder, mode=0o700, exist_ok=True)
    pyz = os.path.join(folder, "mucli-node.pyz")
    with open(pyz, "wb") as fh:
        fh.write(data)
    spec = os.path.join(folder, "spec.json")
    with open(spec, "w", encoding="utf-8") as fh:
        json.dump({"update": dict(order), "pyz": pyz}, fh)
    return spec


def write_marker(state_root: str, order: Mapping[str, Any], from_version: str) -> None:
    with open(os.path.join(state_root, MARKER), "w", encoding="utf-8") as fh:
        json.dump({"exec_id": order.get("exec_id"), "from": from_version, "to": order.get("version"),
                   "at": time.time()}, fh)


def take_marker(state_root: str) -> Optional[Dict[str, Any]]:
    """The update this agent was started to finish, if any (read once)."""
    path = os.path.join(state_root, MARKER)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    try:
        os.unlink(path)
    except OSError:
        pass
    shutil.rmtree(os.path.join(state_root, "update"), ignore_errors=True)
    return data if isinstance(data, dict) else None
