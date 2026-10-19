"""Hash-chained, append-only audit log (plan §3.6).

Each entry's hash covers its sequence number, time, actor, action, subject, the digest of its
payload and the previous entry's hash, so editing, deleting or reordering any entry breaks
every hash after it. ``verify`` walks the chain (``mucli orch audit verify``).
Actors: ``operator``, ``ai:<session>``, ``node:<id>``, ``system``, ``schedule:<id>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from .node.canonical import canonical, sha256_hex

GENESIS = "0" * 64


def entry_hash(seq: int, at: float, actor: str, action: str, subject: str, payload_digest: str,
               prev_hash: str) -> str:
    return sha256_hex(canonical({"seq": int(seq), "at": repr(float(at)), "actor": actor, "action": action,
                                 "subject": subject, "digest": payload_digest, "prev": prev_hash}))


@dataclass
class VerifyResult:
    ok: bool
    entries: int
    problem: str = ""
    seq: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "entries": self.entries, "problem": self.problem, "seq": self.seq}


def verify(entries: Iterable[Dict[str, Any]]) -> VerifyResult:
    prev = GENESIS
    expected_seq = 1
    count = 0
    for entry in entries:
        count += 1
        seq = int(entry["seq"])
        if seq != expected_seq:
            return VerifyResult(False, count, f"sequence gap: expected {expected_seq}, found {seq}", seq)
        if entry["prev_hash"] != prev:
            return VerifyResult(False, count, "previous-hash link broken", seq)
        payload_digest = "sha256:" + sha256_hex(canonical(entry.get("payload") or {}))
        if payload_digest != entry["digest"]:
            return VerifyResult(False, count, "payload does not match its digest", seq)
        computed = entry_hash(seq, entry["at"], entry["actor"], entry["action"], entry["subject"],
                              entry["digest"], prev)
        if computed != entry["hash"]:
            return VerifyResult(False, count, "entry hash mismatch (entry was modified)", seq)
        prev = entry["hash"]
        expected_seq += 1
    return VerifyResult(True, count)


def verify_store(store) -> VerifyResult:
    entries: List[Dict[str, Any]] = []
    after = 0
    while True:
        batch = store.audit_entries(after=after, limit=5000)
        if not batch:
            break
        entries.extend(batch)
        after = batch[-1]["seq"]
    return verify(entries)
