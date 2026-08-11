"""Typed records shared by the durable cross-session memory plane.

The SQLite ledger is the source of truth.  These dataclasses deliberately
contain only JSON-safe fields so the TUI, FastAPI GUI, mobile client and trace
emitter all expose the same contract without a second translation layer.
"""

from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List

MEMORY_KINDS = {
    "constraint",
    "decision",
    "preference",
    "convention",
    "finding",
    "procedure",
    "lesson",
    "goal",
    "handoff",
    "observation",
}
SCOPE_TYPES = {"personal", "workspace", "repository", "branch", "feature"}
LIFECYCLES = {
    "active",
    "needs_review",
    "disputed",
    "superseded",
    "archived",
    "forgotten",
}
EGRESS_POLICIES = {"never", "local_only", "selected", "any"}


def uuid7() -> str:
    """Return a sortable UUIDv7 without requiring Python 3.14's uuid.uuid7.

    RFC 9562 UUIDv7 stores Unix epoch milliseconds in the high 48 bits.  The
    remaining fields are cryptographically random with the version/variant
    bits set explicitly.
    """

    timestamp_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    random_a = secrets.randbits(12)
    random_b = secrets.randbits(62)
    value = (
        (timestamp_ms << 80) | (0x7 << 76) | (random_a << 64) | (0b10 << 62) | random_b
    )
    return str(uuid.UUID(int=value))


@dataclass(frozen=True)
class ScopeContext:
    """Portable identity for the workspace active during a turn."""

    personal_key: str = "personal:default"
    workspace_key: str = ""
    workspace_label: str = ""
    repository_key: str = ""
    repository_label: str = ""
    branch_key: str = ""
    branch_label: str = ""
    feature_key: str = ""
    feature_label: str = ""

    def eligible(self) -> List[Dict[str, str]]:
        result: List[Dict[str, str]] = []
        for scope_type in ("feature", "branch", "repository", "workspace", "personal"):
            key = str(getattr(self, f"{scope_type}_key", "") or "")
            if not key:
                continue
            label = str(getattr(self, f"{scope_type}_label", "") or key)
            result.append({"type": scope_type, "key": key, "label": label})
        return result

    def resolve(self, requested: str = "auto") -> Dict[str, str]:
        requested = str(requested or "auto").strip().lower()
        if requested == "repo":
            requested = "repository"
        if requested == "global":
            requested = "personal"
        order = (
            (requested,)
            if requested not in {"", "auto"}
            else ("repository", "workspace", "personal")
        )
        for scope_type in order:
            if scope_type not in SCOPE_TYPES:
                continue
            key = str(getattr(self, f"{scope_type}_key", "") or "")
            if key:
                return {
                    "type": scope_type,
                    "key": key,
                    "label": str(getattr(self, f"{scope_type}_label", "") or key),
                }
        return {
            "type": "personal",
            "key": self.personal_key,
            "label": "Personal",
        }

    def to_dict(self) -> Dict[str, Any]:
        return {"eligible": self.eligible()}


@dataclass
class MemoryItem:
    id: str
    version: int
    statement: str
    kind: str
    scope_type: str
    scope_key: str
    scope_label: str
    lifecycle: str = "active"
    pinned: bool = False
    trust_origin: str = "model"
    verification: str = "unverified"
    confidence: float = 0.7
    sensitivity: str = "normal"
    egress_policy: str = "any"
    tags: List[str] = field(default_factory=list)
    source_refs: List[Dict[str, Any]] = field(default_factory=list)
    relations: List[Dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_recalled_at: float | None = None
    recall_count: int = 0
    content_hash: str = ""
    etag: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, include_statement: bool = True) -> Dict[str, Any]:
        data = {
            "id": self.id,
            "version": self.version,
            "kind": self.kind,
            "scope": {
                "type": self.scope_type,
                "key": self.scope_key,
                "label": self.scope_label,
            },
            "lifecycle": self.lifecycle,
            "pinned": self.pinned,
            "trust": {
                "origin": self.trust_origin,
                "verification": self.verification,
                "confidence": self.confidence,
            },
            "sensitivity": self.sensitivity,
            "egress_policy": self.egress_policy,
            "tags": list(self.tags),
            "source_refs": list(self.source_refs),
            "relations": list(self.relations),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_recalled_at": self.last_recalled_at,
            "recall_count": self.recall_count,
            "content_hash": self.content_hash,
            "etag": self.etag,
            "metadata": dict(self.metadata),
        }
        if include_statement:
            data["statement"] = self.statement
        return data


@dataclass
class RecallCandidate:
    item: MemoryItem
    score: float
    token_cost: int
    reasons: Dict[str, float | str | bool]
    included: bool = False
    exclusion_reason: str = ""

    def to_dict(self, *, include_statement: bool = True) -> Dict[str, Any]:
        return {
            "memory": self.item.to_dict(include_statement=include_statement),
            "score": round(float(self.score), 4),
            "token_cost": int(self.token_cost),
            "reasons": dict(self.reasons),
            "included": bool(self.included),
            "exclusion_reason": self.exclusion_reason,
        }


@dataclass
class RecallReceipt:
    id: str
    session_name: str
    query: str
    scopes: List[Dict[str, str]]
    budget_tokens: int
    included: List[RecallCandidate] = field(default_factory=list)
    excluded: List[RecallCandidate] = field(default_factory=list)
    token_count: int = 0
    created_at: float = field(default_factory=time.time)

    def to_dict(self, *, include_statements: bool = True) -> Dict[str, Any]:
        return {
            "id": self.id,
            "session_name": self.session_name,
            "query": self.query,
            "scopes": list(self.scopes),
            "budget_tokens": self.budget_tokens,
            "token_count": self.token_count,
            "included": [
                item.to_dict(include_statement=include_statements)
                for item in self.included
            ],
            "excluded": [
                item.to_dict(include_statement=include_statements)
                for item in self.excluded
            ],
            "created_at": self.created_at,
        }


__all__ = [
    "EGRESS_POLICIES",
    "LIFECYCLES",
    "MEMORY_KINDS",
    "SCOPE_TYPES",
    "MemoryItem",
    "RecallCandidate",
    "RecallReceipt",
    "ScopeContext",
    "uuid7",
]
