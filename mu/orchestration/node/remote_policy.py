"""Applying a policy the operator edited on the master (standard library only).

The master signs a ``policy_update`` order carrying the new ``policy.yaml`` text. Applying it
re-checks everything where it matters — in the root helper for systemd nodes:

* the order's signature, addressee, digest, deadline and nonce (``verify_policy_update``);
* the policy in force allows remote edits (``decide_policy_update``: ``allow_remote_policy``,
  or unset with ``allow_agent_updates: true``);
* the new text parses without errors (an invalid policy is never installed: it would deny all).

The previous file is kept as ``policy.yaml.prev`` and the new one replaces it atomically. The
AI can never cause this: orders are only signed for ``setNodePolicy``, an operator command.
"""

from __future__ import annotations

import hashlib
import os
import time
from typing import Any, Mapping, Optional, Tuple

from .envelope import ReplayCache, verify_policy_update
from .policy import decide_policy_update, load_policy_file, parse_policy


def text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def current_text(etc_dir: str) -> str:
    try:
        with open(os.path.join(etc_dir, "policy.yaml"), "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def check(order: Mapping[str, Any], *, etc_dir: str, node_id: str, signing_key: bytes,
          replay: Optional[ReplayCache], now: Optional[float] = None) -> Tuple[bool, str]:
    verdict = verify_policy_update(order, node_id=node_id, signing_key=signing_key, replay=replay, now=now)
    if not verdict.ok:
        return False, verdict.reason
    decision = decide_policy_update(load_policy_file(os.path.join(etc_dir, "policy.yaml")))
    if not decision.allowed:
        return False, decision.reason
    candidate = parse_policy(str(order.get("text") or ""))
    if candidate.errors:
        return False, "the new policy is invalid: " + "; ".join(candidate.errors)
    return True, ""


def apply(order: Mapping[str, Any], *, etc_dir: str, node_id: str, signing_key: bytes, replay: ReplayCache,
          now: Optional[float] = None) -> Tuple[bool, str]:
    """Verify (consuming the nonce) and install the policy; returns (ok, message)."""
    now = time.time() if now is None else now
    ok, reason = check(order, etc_dir=etc_dir, node_id=node_id, signing_key=signing_key, replay=replay, now=now)
    if not ok:
        return False, reason
    text = str(order["text"])
    path = os.path.join(etc_dir, "policy.yaml")
    tmp = os.path.join(etc_dir, ".policy.yaml.new")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, 0o644)
    if os.path.exists(path):
        with open(path, "rb") as src, open(path + ".prev", "wb") as dst:
            dst.write(src.read())
    os.replace(tmp, path)
    return True, f"installed policy {text_digest(text)[:12]} (previous kept as {path}.prev)"
