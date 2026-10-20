"""Editing a node's ``policy.yaml`` from the master (operator only; never the AI).

The operator edits the text on the node page. ``set_policy`` validates it with the node's own
parser, signs a ``policy_update`` order and keeps it as the node's pending policy; the node's
next poll carries it (``hub.heartbeat``), the agent and then the root helper check it again and
install it (``node/remote_policy.py``), and the poll after that reports the outcome. Every step
is audited. There is no approval flow: the command is the operator's own action, like editing
the file over SSH, and the node only accepts it when its policy in force allows remote edits
(``allow_remote_policy: true``, or unset with ``allow_agent_updates: true``).
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Mapping, Optional

from .models import Node
from .node.policy import parse_policy

ORDER_TTL_S = 7 * 86400
_KEY = "node_policy:{}"
WATCHED = ("max_effect", "max_privilege", "accept_categories", "allowed_tools", "denied_tools", "allowed_run_as",
           "require_check_mode_first", "quiet_hours", "canary", "allow_agent_updates", "allow_remote_policy")


class PolicyError(ValueError):
    pass


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def pending(store, node_id: str) -> Optional[Dict[str, Any]]:
    entry = store.get_setting(_KEY.format(node_id), None)
    return entry if isinstance(entry, dict) else None


def status(store, node: Node) -> Dict[str, Any]:
    """What the node page shows: whether the master can edit this node's policy, the current text
    and the pending edit's state."""
    reported = dict(node.policy or {})
    supported = "sha256" in reported
    allowed = bool(reported.get("remote_policy")) if supported else False
    if not reported:
        reason = "the node hasn't reported its policy yet"
    elif not supported:
        reason = "the node's agent predates remote policy edits; update it (agent 1.2.0)"
    elif reported.get("errors"):
        reason = "the node's policy is invalid; fix it on the node"
    elif not allowed:
        reason = ("this node keeps its policy local; set allow_remote_policy: true in "
                  "/etc/mucli-node/policy.yaml once to edit it here")
    else:
        reason = ""
    entry = pending(store, node.id)
    view = None
    if entry:
        view = {k: entry.get(k) for k in ("state", "by", "at", "reason", "sha256", "applied_at")}
    return {"supported": supported, "allowed": allowed and supported and not reported.get("errors"),
            "reason": reason, "text": reported.get("text") or "", "sha256": reported.get("sha256") or "",
            "pending": view}


def _summary(text: str) -> Dict[str, Any]:
    return parse_policy(text).summary()


def validate(text: str, current_text: str = "") -> Dict[str, Any]:
    """The editor's live check: errors, what changes, and warnings worth a second look."""
    if len(text) > 16384:
        return {"ok": False, "errors": ["the policy is longer than 16 KB"], "changes": [], "warnings": []}
    parsed = parse_policy(text)
    if parsed.errors:
        return {"ok": False, "errors": list(parsed.errors), "changes": [], "warnings": []}
    new = parsed.summary()
    old = _summary(current_text) if current_text else {}
    changes: List[Dict[str, Any]] = []
    for key in WATCHED:
        if old.get(key) != new.get(key):
            changes.append({"key": key, "from": old.get(key), "to": new.get(key)})
    warnings = []
    from .node.policy import remote_policy_allowed
    if not remote_policy_allowed(parsed):
        warnings.append("After this, the node keeps its policy local: further edits need the node itself.")
    from .models import EFFECT_RANK
    if old and EFFECT_RANK.get(str(new.get("max_effect")), 0) > EFFECT_RANK.get(str(old.get("max_effect")), 0):
        warnings.append(f"Raises max_effect to {new.get('max_effect')}: the node will accept changing tools "
                        "(each run is still decided by the access policy).")
    if new.get("canary") and not old.get("canary"):
        warnings.append("Makes this a canary node: it will accept test-signed drafts.")
    return {"ok": True, "errors": [], "changes": changes, "warnings": warnings}


def set_policy(orch, *, node_id: str, text: str, actor: str) -> Dict[str, Any]:
    store = orch.store
    node = store.get_node(node_id)
    if node is None:
        raise PolicyError("unknown node")
    if node.status not in ("active", "quarantined"):
        raise PolicyError(f"the node is {node.status}")
    state = status(store, node)
    if not state["allowed"]:
        raise PolicyError(state["reason"] or "this node doesn't accept policy edits from the master")
    text = str(text or "").replace("\r\n", "\n")
    if not text.endswith("\n"):
        text += "\n"
    check = validate(text, state["text"])
    if not check["ok"]:
        raise PolicyError("invalid policy: " + "; ".join(check["errors"]))
    sha = digest(text)
    if sha == state["sha256"]:
        raise PolicyError("that is already the node's policy")
    now = store.now()
    from .signing import new_nonce
    order = orch.keys.sign_policy({"kind": "policy_update", "node_id": node.id, "text": text, "sha256": sha,
                                   "issued_at": now, "deadline": now + ORDER_TTL_S, "nonce": new_nonce(),
                                   "by": actor})
    store.set_setting(_KEY.format(node.id), {"state": "pending", "order": order, "by": actor, "at": now,
                                             "sha256": sha, "reason": ""})
    store.audit(actor, "node.policy.set", node.id, {"sha256": sha, "changes": check["changes"]})
    store.change("node", node.id, ["policy"])
    orch.wake()
    return {"node_id": node.id, "sha256": sha, "changes": check["changes"], "warnings": check["warnings"]}


def cancel(orch, *, node_id: str, actor: str) -> Dict[str, Any]:
    entry = pending(orch.store, node_id)
    if not entry or entry.get("state") != "pending":
        raise PolicyError("no policy edit is waiting for this node")
    orch.store.set_setting(_KEY.format(node_id), dict(entry, state="cancelled", order=None))
    orch.store.audit(actor, "node.policy.cancel", node_id, {"sha256": entry.get("sha256")})
    orch.store.change("node", node_id, ["policy"])
    return {"node_id": node_id}


def on_heartbeat(store, node: Node, body: Mapping[str, Any], reply: Dict[str, Any]) -> None:
    """Hand the pending order to the node, and record what it reported."""
    entry = pending(store, node.id)
    if not entry:
        return
    reported = body.get("policy") if isinstance(body.get("policy"), Mapping) else {}
    result = body.get("policy_result") if isinstance(body.get("policy_result"), Mapping) else None
    order = entry.get("order") or {}
    changed = False
    if entry.get("state") == "pending":
        if result and str(result.get("nonce")) == str(order.get("nonce")):
            ok = bool(result.get("ok"))
            entry = dict(entry, state="applied" if ok else "refused", reason=str(result.get("reason") or "")[:500],
                         applied_at=store.now(), order=None)
            store.audit(f"node:{node.id}", "node.policy.applied" if ok else "node.policy.refused", node.id,
                        {"sha256": entry.get("sha256"), "reason": entry.get("reason")})
            changed = True
        elif reported.get("sha256") == entry.get("sha256"):
            entry = dict(entry, state="applied", applied_at=store.now(), order=None)
            changed = True
        elif float(order.get("deadline") or 0) < store.now():
            entry = dict(entry, state="expired", order=None)
            changed = True
        else:
            reply["policy_update"] = order
    if changed:
        store.set_setting(_KEY.format(node.id), entry)
        store.change("node", node.id, ["policy"])
