"""Orchestration records (plan §3). Plain dataclasses; ``store.py`` maps them to sqlite rows."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, List, Optional

NODE_STATUSES = ("pending", "active", "quarantined", "revoked")
EFFECTS = ("read_only", "mutating", "destructive")
EFFECT_RANK = {name: i for i, name in enumerate(EFFECTS)}
CATEGORIES = ("inventory", "diagnostics", "maintenance", "deploy", "security", "network", "storage",
              "database", "observability", "other")
STAGES = ("intent", "spec", "discover", "implement", "test", "review", "publish")


def _loads(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class _Record:
    JSON_FIELDS: Dict[str, Any] = {}
    BOOL_FIELDS: tuple = ()

    @classmethod
    def from_row(cls, row: Any):
        data = dict(row)
        names = {f.name for f in fields(cls)}
        kwargs = {}
        for key, value in data.items():
            if key not in names:
                continue
            if key in cls.JSON_FIELDS:
                value = _loads(value, cls.JSON_FIELDS[key]() if callable(cls.JSON_FIELDS[key]) else cls.JSON_FIELDS[key])
            elif key in cls.BOOL_FIELDS:
                value = bool(value)
            kwargs[key] = value
        return cls(**kwargs)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Node(_Record):
    JSON_FIELDS = {"labels": dict, "facts": dict, "policy": dict}
    BOOL_FIELDS = ("canary",)

    id: str
    name: str = ""
    labels: Dict[str, str] = field(default_factory=dict)
    status: str = "pending"
    kind: str = "host"
    canary: bool = False
    created_at: float = 0.0
    accepted_at: Optional[float] = None
    last_seen: Optional[float] = None
    agent_version: str = ""
    facts: Dict[str, Any] = field(default_factory=dict)
    facts_at: Optional[float] = None
    policy: Dict[str, Any] = field(default_factory=dict)
    address: str = ""
    credential_rotated_at: Optional[float] = None

    def public(self) -> Dict[str, Any]:
        return self.to_dict()


@dataclass
class ToolVersion(_Record):
    JSON_FIELDS = {"manifest": dict, "blobs": dict, "lint": dict, "review": dict}

    tool_id: str
    version: int
    status: str = "draft"
    manifest: Dict[str, Any] = field(default_factory=dict)
    manifest_digest: str = ""
    blobs: Dict[str, str] = field(default_factory=dict)
    signature: str = ""
    signed_with: str = ""
    key_id: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    created_by: str = ""
    published_at: Optional[float] = None
    stage: str = "intent"
    session_name: str = ""
    intent: str = ""
    lint: Dict[str, Any] = field(default_factory=dict)
    review: Dict[str, Any] = field(default_factory=dict)
    origin: str = "studio"

    @property
    def ref(self) -> str:
        return f"{self.tool_id}@{self.version}"

    @property
    def effect(self) -> str:
        return str(self.manifest.get("effect") or "destructive")


@dataclass
class RunbookVersion(_Record):
    JSON_FIELDS = {"spec": dict, "review": dict}

    runbook_id: str
    version: int
    status: str = "draft"
    spec: Dict[str, Any] = field(default_factory=dict)
    digest: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    created_by: str = ""
    published_at: Optional[float] = None
    origin: str = "manual"
    review: Dict[str, Any] = field(default_factory=dict)
    source: str = ""      # the operator's raw YAML when it doesn't parse; ``spec`` keeps the last valid parse

    @property
    def ref(self) -> str:
        return f"{self.runbook_id}@{self.version}"


@dataclass
class Run(_Record):
    JSON_FIELDS = {"params": dict, "selector": dict, "nodes": list, "excluded": list, "strategy": dict,
                   "decision": dict, "plan": dict, "summary": dict}
    BOOL_FIELDS = ("check_mode",)

    id: str
    kind: str = "tool"
    target_id: str = ""
    target_version: int = 0
    params: Dict[str, Any] = field(default_factory=dict)
    selector: Dict[str, Any] = field(default_factory=dict)
    nodes: List[str] = field(default_factory=list)
    excluded: List[Dict[str, Any]] = field(default_factory=list)
    strategy: Dict[str, Any] = field(default_factory=dict)
    check_mode: bool = False
    state: str = "proposed"
    proposed_by: str = "operator"
    approved_by: str = ""
    created_at: float = 0.0
    approved_at: Optional[float] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    decision: Dict[str, Any] = field(default_factory=dict)
    plan: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    paused_reason: str = ""
    parent_run: str = ""
    schedule_id: str = ""
    batch: int = 0
    batch_finished_at: Optional[float] = None
    summary: Dict[str, Any] = field(default_factory=dict)
    title: str = ""

    @property
    def target_ref(self) -> str:
        return f"{self.target_id}@{self.target_version}"


@dataclass
class Execution(_Record):
    JSON_FIELDS = {"params": dict, "output": lambda: None, "output_errors": list, "envelope": dict}
    BOOL_FIELDS = ("cancel_requested", "idempotent", "check_mode", "log_truncated")

    id: str
    run_id: str
    node_id: str
    tool_id: str = ""
    version: int = 0
    step: int = 0
    step_name: str = ""
    batch: int = 0
    params: Dict[str, Any] = field(default_factory=dict)
    state: str = "queued"
    attempts: int = 0
    check_mode: bool = False
    idempotent: bool = False
    created_at: float = 0.0
    dispatched_at: Optional[float] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    exit_code: Optional[int] = None
    output: Any = None
    output_errors: List[str] = field(default_factory=list)
    log: str = ""
    log_truncated: bool = False
    reason: str = ""
    duration_s: Optional[float] = None
    output_digest: str = ""
    nonce: str = ""
    envelope: Dict[str, Any] = field(default_factory=dict)
    cancel_requested: bool = False
    rollback_of: str = ""
    redactions: int = 0
