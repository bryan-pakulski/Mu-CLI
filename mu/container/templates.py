"""Reusable Docker snapshots for MuCLI managed environments."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from utils.config import HISTORY_DIR


@dataclass
class ContainerTemplate:
    name: str
    image: str
    source_container: str
    description: str = ""
    created_at: float = field(default_factory=time.time)
    dockerfile_hash: str = ""
    egress_allow: list[str] = field(default_factory=list)
    egress_deny: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ContainerTemplate":
        allowed = {item.name for item in cls.__dataclass_fields__.values()}
        return cls(**{key: item for key, item in dict(value or {}).items() if key in allowed})


class TemplateRegistry:
    """Atomic JSON registry stored independently from running containers."""

    def __init__(self, root: str | None = None):
        self.root = os.path.abspath(
            os.path.expanduser(root or os.path.join(HISTORY_DIR, "container_templates"))
        )
        self.registry_path = os.path.join(self.root, "registry.json")
        self._lock = threading.RLock()
        os.makedirs(self.root, exist_ok=True)

    def _read(self) -> dict[str, dict[str, Any]]:
        try:
            with open(self.registry_path, "r", encoding="utf-8") as handle:
                value = json.load(handle)
        except (FileNotFoundError, OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write(self, value: dict[str, dict[str, Any]]) -> None:
        fd, path = tempfile.mkstemp(prefix="templates-", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(path, self.registry_path)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def upsert(self, template: ContainerTemplate) -> ContainerTemplate:
        with self._lock:
            data = self._read()
            data[template.name] = template.to_dict()
            self._write(data)
            directory = Path(self.root, template.name)
            directory.mkdir(parents=True, exist_ok=True)
            Path(directory, "template.json").write_text(
                json.dumps(template.to_dict(), indent=2, sort_keys=True),
                encoding="utf-8",
            )
        return template

    def get(self, name: str) -> ContainerTemplate | None:
        with self._lock:
            value = self._read().get(str(name))
        return ContainerTemplate.from_dict(value) if isinstance(value, dict) else None

    def list_templates(self) -> list[ContainerTemplate]:
        with self._lock:
            values = list(self._read().values())
        result = [
            ContainerTemplate.from_dict(item)
            for item in values
            if isinstance(item, dict)
        ]
        return sorted(result, key=lambda item: item.created_at, reverse=True)

    def remove(self, name: str) -> bool:
        with self._lock:
            data = self._read()
            if name not in data:
                return False
            data.pop(name, None)
            self._write(data)
        directory = Path(self.root, name)
        try:
            Path(directory, "template.json").unlink()
            directory.rmdir()
        except OSError:
            pass
        return True
