"""Persistent container metadata models."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any


DEFAULT_WORKER_PORT = 30312
WORKER_PROTOCOL_VERSION = 4


@dataclass(frozen=True)
class MountSpec:
    host_path: str
    container_path: str
    mode: str = "rw"

    def __post_init__(self):
        if self.mode not in {"ro", "rw"}:
            raise ValueError("mount mode must be 'ro' or 'rw'")
        if not self.host_path or not self.container_path:
            raise ValueError("mount paths are required")

    def to_dict(self) -> dict[str, str]:
        return {
            "host_path": self.host_path,
            "container_path": self.container_path,
            "mode": self.mode,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "MountSpec":
        return cls(
            host_path=str(value.get("host_path") or ""),
            container_path=str(value.get("container_path") or ""),
            mode=str(value.get("mode") or "rw"),
        )


@dataclass
class ContainerRef:
    container_id: str
    name: str
    image: str
    dockerfile_hash: str
    mounts: list[MountSpec] = field(default_factory=list)
    egress_allow: list[str] = field(default_factory=list)
    egress_deny: list[str] = field(default_factory=list)
    network_name: str = ""
    network_subnet: str = ""
    proxy_name: str = ""
    proxy_ip: str = ""
    proxy_port: int = 3128
    proxy_image: str = ""
    egress_network_name: str = ""
    session_volume: str = ""  # first attached session path (compatibility)
    container_volume: str = "/root/.mucli"
    worker_port: int = DEFAULT_WORKER_PORT
    worker_protocol: int = WORKER_PROTOCOL_VERSION
    worker_token: str = ""
    supervisor_url: str = ""
    created_at: float = field(default_factory=time.time)
    status: str = "building"
    attached_sessions: list[str] = field(default_factory=list)
    root_volume: str = ""
    workspace_volume: str = ""
    template_name: str = ""
    standalone: bool = False

    def to_dict(self, *, include_secret: bool = True) -> dict[str, Any]:
        value = asdict(self)
        if not include_secret:
            value.pop("worker_token", None)
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ContainerRef":
        data = dict(value or {})
        if "worker_protocol" not in data:
            # Registry records written before the versioned worker bridge must
            # be rebuilt so their baked-in source and port are upgraded.
            data["worker_protocol"] = 0
        data["mounts"] = [
            item if isinstance(item, MountSpec) else MountSpec.from_dict(item)
            for item in data.get("mounts", [])
        ]
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        return cls(**{key: val for key, val in data.items() if key in allowed})
