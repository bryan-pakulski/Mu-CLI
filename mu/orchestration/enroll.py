"""Node enrolment (plan §6.2): join tokens, credentials, accept / quarantine / revoke, rotation.

* A **join token** is one-time, expires (1 h by default) and may preset labels; the master
  stores only its SHA-256.
* ``enroll`` exchanges it for a per-node **credential** (32 random bytes); again only the hash
  is kept. The node stays ``pending`` until the operator accepts it.
* ``authenticate`` checks a credential with ``hmac.compare_digest``. Credentials rotate through
  the poll channel: the master offers a new one, keeps both hashes, and promotes the new hash
  the first time the node uses it.
* Revocation deletes the credential hashes, so the next poll is refused.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any, Dict, Mapping, Optional

from .models import Node
from .node.envelope import b64e
from .selectors import validate_labels
from .store import new_id

TOKEN_TTL_S = 3600.0
TOKEN_ID_LEN = 16
ROTATE_AFTER_S = 30 * 86400.0


class EnrollError(ValueError):
    pass


def _hash(secret: str) -> str:
    return hashlib.sha256(str(secret).encode("utf-8")).hexdigest()


def token_id(token: str) -> str:
    """A token's public handle: a prefix of its stored hash (the setup wizard's resume key)."""
    return _hash(token)[:TOKEN_ID_LEN]


def token_status(store, tid: str) -> Optional[Dict[str, Any]]:
    """The join token whose hash starts with ``tid`` (never the token itself)."""
    import json
    tid = str(tid or "").lower()
    if len(tid) != TOKEN_ID_LEN or any(c not in "0123456789abcdef" for c in tid):
        return None
    with store.conn() as conn:
        row = conn.execute("SELECT * FROM join_tokens WHERE substr(token_hash, 1, ?) = ?",
                           (TOKEN_ID_LEN, tid)).fetchone()
    if row is None:
        return None
    return {"token_id": tid, "created_at": row["created_at"], "expires_at": row["expires_at"],
            "used_at": row["used_at"], "node_id": row["node_id"] or "", "labels": json.loads(row["labels"] or "{}"),
            "canary": bool(row["canary"]), "note": row["note"],
            "expired": row["used_at"] is None and row["expires_at"] < store.now()}


def create_join_token(store, *, labels: Optional[Mapping[str, str]] = None, canary: bool = False,
                      ttl_s: float = TOKEN_TTL_S, note: str = "", actor: str = "operator") -> Dict[str, Any]:
    labels = {str(k): str(v) for k, v in (labels or {}).items()}
    problems = validate_labels(labels)
    if problems:
        raise EnrollError("; ".join(problems))
    token = "mjt_" + secrets.token_urlsafe(24)
    now = store.now()
    ttl_s = max(60.0, min(float(ttl_s), 7 * 86400.0))
    import json
    with store.tx() as conn:
        conn.execute("INSERT INTO join_tokens(token_hash, created_at, expires_at, labels, canary, note)"
                     " VALUES(?,?,?,?,?,?)", (_hash(token), now, now + ttl_s, json.dumps(labels), int(canary), note))
        store.audit(actor, "join_token.create", "", {"labels": labels, "canary": canary, "ttl_s": ttl_s,
                                                    "note": note}, conn=conn)
        store.change("node", "", ["state"], conn=conn)
    return {"token": token, "token_id": token_id(token), "expires_at": now + ttl_s, "labels": labels,
            "canary": canary}


def enroll(store, keys, *, token: str, name: str, facts: Mapping[str, Any], policy: Mapping[str, Any],
           agent_version: str, address: str = "") -> Dict[str, Any]:
    import json
    now = store.now()
    credential = secrets.token_urlsafe(32)
    with store.tx() as conn:
        row = conn.execute("SELECT * FROM join_tokens WHERE token_hash=?", (_hash(token),)).fetchone()
        if row is None:
            raise EnrollError("unknown join token")
        if row["used_at"] is not None:
            raise EnrollError("join token already used")
        if row["expires_at"] < now:
            raise EnrollError("join token expired")
        node_id = new_id("n", 5)
        labels = json.loads(row["labels"] or "{}")
        clean_name = "".join(ch for ch in str(name or "") if ch.isalnum() or ch in "-_.")[:63] or node_id
        node = Node(id=node_id, name=clean_name, labels=labels, status="pending", canary=bool(row["canary"]),
                    created_at=now, agent_version=str(agent_version or "")[:32], facts=dict(facts or {}),
                    facts_at=now if facts else None, policy=dict(policy or {}), address=address, last_seen=now)
        store.insert_node(node, _hash(credential), conn)
        conn.execute("UPDATE join_tokens SET used_at=?, node_id=? WHERE token_hash=?", (now, node_id, _hash(token)))
        store.audit(f"node:{node_id}", "node.enroll", node_id, {"name": clean_name, "labels": labels,
                                                                 "address": address, "agent_version": agent_version},
                    conn=conn)
        store.change("node", node_id, ["state", "facts"], conn=conn)
    return {"node_id": node_id, "credential": credential, "signing_key": b64e(keys.signing_public),
            "test_key": b64e(keys.test_public), "status": "pending"}


def authenticate(store, node_id: str, credential: str) -> Optional[Node]:
    """The node, if the credential is valid and the node is not revoked."""
    found = store.node_credentials(str(node_id or ""))
    if not found or not credential:
        return None
    status, current, pending = found
    if status == "revoked":
        return None
    presented = _hash(credential)
    if current and hmac.compare_digest(presented, current):
        return store.get_node(node_id)
    if pending and hmac.compare_digest(presented, pending):
        with store.tx() as conn:
            conn.execute("UPDATE nodes SET credential_hash=?, pending_credential_hash=NULL, credential_rotated_at=?"
                         " WHERE id=?", (pending, store.now(), node_id))
            store.audit(f"node:{node_id}", "node.credential_rotated", node_id, {}, conn=conn)
        return store.get_node(node_id)
    return None


def maybe_rotate(store, node: Node, *, rotate_after_s: float = ROTATE_AFTER_S, force: bool = False) -> Optional[str]:
    """A new credential to hand the node on this poll, when rotation is due."""
    found = store.node_credentials(node.id)
    if not found:
        return None
    _status, _current, pending = found
    if pending and not force:
        return None
    last = node.credential_rotated_at or node.accepted_at or node.created_at
    if not force and store.now() - float(last or 0) < rotate_after_s:
        return None
    credential = secrets.token_urlsafe(32)
    with store.tx() as conn:
        conn.execute("UPDATE nodes SET pending_credential_hash=? WHERE id=?", (_hash(credential), node.id))
        store.audit("system", "node.credential_offered", node.id, {}, conn=conn)
    return credential


def set_status(store, node_id: str, status: str, *, actor: str, reason: str = "") -> Node:
    allowed = {"active": ("pending", "quarantined"), "quarantined": ("active", "pending"),
               "revoked": ("pending", "active", "quarantined")}
    node = store.get_node(node_id)
    if node is None:
        raise EnrollError(f"unknown node {node_id}")
    if node.status not in allowed.get(status, ()):
        raise EnrollError(f"cannot move node from {node.status} to {status}")
    fields: Dict[str, Any] = {"status": status}
    if status == "active" and node.accepted_at is None:
        fields["accepted_at"] = store.now()
        fields["credential_rotated_at"] = store.now()
    with store.tx() as conn:
        if status == "revoked":
            conn.execute("UPDATE nodes SET credential_hash=NULL, pending_credential_hash=NULL WHERE id=?", (node_id,))
            # Live work on a revoked node can never report back.
            conn.execute("UPDATE executions SET state='unreachable', reason='node revoked', finished_at=?"
                         " WHERE node_id=? AND state IN ('queued','dispatched','running')", (store.now(), node_id))
        store.update_node(node_id, conn=conn, **fields)
        action = {"active": "node.accept" if node.status == "pending" else "node.unquarantine",
                  "quarantined": "node.quarantine", "revoked": "node.revoke"}[status]
        store.audit(actor, action, node_id, {"from": node.status, "reason": reason}, conn=conn)
        store.change("node", node_id, ["state"], conn=conn)
    return store.get_node(node_id)


def list_tokens(store) -> list:
    with store.conn() as conn:
        rows = conn.execute("SELECT substr(token_hash, 1, ?) AS token_id, created_at, expires_at, used_at, node_id,"
                            " labels, canary, note FROM join_tokens ORDER BY created_at DESC LIMIT 50",
                            (TOKEN_ID_LEN,)).fetchall()
    import json
    return [dict(r, labels=json.loads(r["labels"] or "{}"), canary=bool(r["canary"])) for r in rows]
