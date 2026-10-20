"""Signed payloads: what the master signs and what a node verifies before running anything.

The master builds these byte strings and signs them with ``cryptography``; the node rebuilds
them from what it received and checks the signature with the vendored ``ed25519.verify``.
Each payload starts with a domain tag so a signature for one kind can never be replayed as
another.
"""

from __future__ import annotations

import base64
import hashlib
import time
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from . import ed25519
from .canonical import canonical, digest

TOOL_VERSION_TAG = b"mucli-orch/tool-version/v1\n"
DISPATCH_TAG = b"mucli-orch/dispatch/v1\n"
CANCEL_TAG = b"mucli-orch/cancel/v1\n"
BUNDLE_TAG = b"mucli-orch/bundle/v1\n"
UPDATE_TAG = b"mucli-orch/agent-update/v1\n"
POLICY_TAG = b"mucli-orch/node-policy/v1\n"

# Fields of a dispatch envelope that are not covered by its signature: the signature itself
# and secret values (delivered beside the envelope over the pinned TLS channel; their names
# are signed).
UNSIGNED_DISPATCH_FIELDS = frozenset({"sig", "secrets"})
MAX_CLOCK_SKEW_S = 300.0


def b64e(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def b64d(text: str) -> bytes:
    return base64.b64decode(str(text or "").encode("ascii"), validate=True)


def key_id(public: bytes) -> str:
    return hashlib.sha256(public).hexdigest()[:16]


def manifest_digest(manifest: Mapping[str, Any]) -> str:
    return digest(dict(manifest))


def tool_version_payload(tool_id: str, version: int, manifest_digest_: str,
                         blobs: Mapping[str, str]) -> bytes:
    return TOOL_VERSION_TAG + canonical({
        "tool_id": str(tool_id), "version": int(version),
        "manifest_digest": str(manifest_digest_), "blobs": dict(sorted(blobs.items())),
    })


def dispatch_payload(envelope: Mapping[str, Any]) -> bytes:
    body = {k: v for k, v in envelope.items() if k not in UNSIGNED_DISPATCH_FIELDS}
    return DISPATCH_TAG + canonical(body)


def cancel_payload(cancel: Mapping[str, Any]) -> bytes:
    body = {k: v for k, v in cancel.items() if k != "sig"}
    return CANCEL_TAG + canonical(body)


def update_payload(order: Mapping[str, Any]) -> bytes:
    body = {k: v for k, v in order.items() if k != "sig"}
    return UPDATE_TAG + canonical(body)


def policy_payload(order: Mapping[str, Any]) -> bytes:
    body = {k: v for k, v in order.items() if k != "sig"}
    return POLICY_TAG + canonical(body)


def verify_policy_update(order: Mapping[str, Any], *, node_id: str, signing_key: bytes,
                         replay: Optional["ReplayCache"], now: Optional[float] = None) -> "Check":
    """Authenticate a policy order from the master: ``{kind: policy_update, node_id, text, sha256,
    issued_at, deadline, nonce, by, sig}``. ``replay`` None checks without consuming the nonce
    (the agent checks first; the root helper consumes it)."""
    now = time.time() if now is None else float(now)
    if not isinstance(order, Mapping) or order.get("kind") != "policy_update":
        return Check(False, "not a policy update")
    if str(order.get("node_id") or "") != node_id:
        return Check(False, "policy is addressed to another node")
    if not verify_b64(signing_key, policy_payload(order), str(order.get("sig") or "")):
        return Check(False, "bad policy signature")
    text = order.get("text")
    if not isinstance(text, str) or len(text) > 16384:
        return Check(False, "policy text missing or too long")
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != str(order.get("sha256") or ""):
        return Check(False, "policy text doesn't match its digest")
    try:
        deadline, issued_at = float(order.get("deadline")), float(order.get("issued_at"))
    except (TypeError, ValueError):
        return Check(False, "missing deadline")
    if deadline < now:
        return Check(False, "policy order expired")
    if issued_at > now + MAX_CLOCK_SKEW_S:
        return Check(False, "policy issued in the future (clock skew)")
    nonce = str(order.get("nonce") or "")
    if len(nonce) < 16:
        return Check(False, "missing nonce")
    if replay is not None:
        if replay.seen(nonce):
            return Check(False, "replayed policy order (nonce already used)")
        replay.add(nonce, deadline)
    return Check(True)


def bundle_payload(bundle_digest: str) -> bytes:
    return BUNDLE_TAG + canonical({"bundle_digest": str(bundle_digest)})


def verify_b64(public: bytes, payload: bytes, signature_b64: str) -> bool:
    try:
        signature = b64d(signature_b64)
    except (ValueError, TypeError):
        return False
    return ed25519.verify(public, payload, signature)


@dataclass(frozen=True)
class Check:
    ok: bool
    reason: str = ""


class ReplayCache:
    """Seen nonces, kept until their envelope's deadline passes (in memory; see store.py)."""

    def __init__(self, entries: Optional[Dict[str, float]] = None):
        self.entries: Dict[str, float] = dict(entries or {})

    def seen(self, nonce: str) -> bool:
        return nonce in self.entries

    def add(self, nonce: str, expires_at: float) -> None:
        self.entries[nonce] = float(expires_at)

    def prune(self, now: float) -> None:
        for nonce in [n for n, exp in self.entries.items() if exp < now]:
            del self.entries[nonce]


def verify_dispatch(envelope: Mapping[str, Any], *, node_id: str, signing_key: bytes,
                    test_key: Optional[bytes], accept_test: bool, replay: ReplayCache,
                    now: Optional[float] = None) -> Check:
    """Authenticate a dispatch envelope. Parameters and policy are checked separately."""
    now = time.time() if now is None else float(now)
    if not isinstance(envelope, Mapping) or envelope.get("kind") != "dispatch":
        return Check(False, "not a dispatch envelope")
    if str(envelope.get("node_id") or "") != node_id:
        return Check(False, "envelope is addressed to another node")
    if not verify_b64(signing_key, dispatch_payload(envelope), str(envelope.get("sig") or "")):
        return Check(False, "bad envelope signature")
    try:
        deadline = float(envelope.get("deadline"))
        issued_at = float(envelope.get("issued_at"))
    except (TypeError, ValueError):
        return Check(False, "missing deadline")
    if deadline < now:
        return Check(False, "dispatch deadline passed")
    if issued_at > now + MAX_CLOCK_SKEW_S:
        return Check(False, "dispatch issued in the future (clock skew)")
    nonce = str(envelope.get("nonce") or "")
    if len(nonce) < 16:
        return Check(False, "missing nonce")
    if replay.seen(nonce):
        return Check(False, "replayed dispatch (nonce already used)")
    replay.add(nonce, deadline)
    manifest = envelope.get("manifest")
    if not isinstance(manifest, Mapping):
        return Check(False, "missing manifest")
    if manifest.get("id") != envelope.get("tool_id") or manifest.get("version") != envelope.get("version"):
        return Check(False, "manifest does not match the dispatched tool")
    computed = manifest_digest(manifest)
    if computed != envelope.get("manifest_digest"):
        return Check(False, "manifest digest mismatch")
    blobs = envelope.get("blobs")
    if not isinstance(blobs, Mapping):
        return Check(False, "missing blob digests")
    payload = tool_version_payload(str(envelope.get("tool_id")), int(envelope.get("version") or 0),
                                   computed, {str(k): str(v) for k, v in blobs.items()})
    if envelope.get("test"):
        if not accept_test:
            return Check(False, "test-signed tool refused: this node is not a canary")
        if not test_key or not verify_b64(test_key, payload, str(envelope.get("version_sig") or "")):
            return Check(False, "bad test signature on tool version")
    elif not verify_b64(signing_key, payload, str(envelope.get("version_sig") or "")):
        return Check(False, "bad signature on tool version")
    return Check(True)


def verify_update(order: Mapping[str, Any], *, node_id: str, signing_key: bytes, replay: Optional[ReplayCache],
                  now: Optional[float] = None) -> Check:
    """Authenticate an agent-update order: ``{kind: agent_update, exec_id, run_id, node_id,
    version, sha256, size, issued_at, deadline, nonce, sig}``. ``replay`` None checks without
    consuming the nonce (the agent checks first; the root helper consumes it)."""
    now = time.time() if now is None else float(now)
    if not isinstance(order, Mapping) or order.get("kind") != "agent_update":
        return Check(False, "not an agent update")
    if str(order.get("node_id") or "") != node_id:
        return Check(False, "update is addressed to another node")
    if not verify_b64(signing_key, update_payload(order), str(order.get("sig") or "")):
        return Check(False, "bad update signature")
    try:
        deadline = float(order.get("deadline"))
        issued_at = float(order.get("issued_at"))
    except (TypeError, ValueError):
        return Check(False, "missing deadline")
    if deadline < now:
        return Check(False, "update deadline passed")
    if issued_at > now + MAX_CLOCK_SKEW_S:
        return Check(False, "update issued in the future (clock skew)")
    sha = str(order.get("sha256") or "")
    if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        return Check(False, "update has no agent digest")
    nonce = str(order.get("nonce") or "")
    if len(nonce) < 16:
        return Check(False, "missing nonce")
    if replay is not None:
        if replay.seen(nonce):
            return Check(False, "replayed update (nonce already used)")
        replay.add(nonce, deadline)
    return Check(True)


def verify_cancel(cancel: Mapping[str, Any], *, node_id: str, signing_key: bytes,
                  now: Optional[float] = None) -> Check:
    now = time.time() if now is None else float(now)
    if not isinstance(cancel, Mapping) or cancel.get("kind") != "cancel":
        return Check(False, "not a cancel")
    if str(cancel.get("node_id") or "") != node_id:
        return Check(False, "cancel is addressed to another node")
    if not verify_b64(signing_key, cancel_payload(cancel), str(cancel.get("sig") or "")):
        return Check(False, "bad cancel signature")
    try:
        if float(cancel.get("deadline")) < now:
            return Check(False, "cancel expired")
    except (TypeError, ValueError):
        return Check(False, "missing deadline")
    return Check(True)
