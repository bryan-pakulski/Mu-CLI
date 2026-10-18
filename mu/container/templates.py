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
    # Job setup (documentation/plans/job_templates.md §4): shell commands run once per job in
    # the job's worktree after its image is built and before planning (e.g. ``npm ci``).
    job_setup: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ContainerTemplate":
        allowed = {item.name for item in cls.__dataclass_fields__.values()}
        data = {key: item for key, item in dict(value or {}).items() if key in allowed}
        # Older registry.json entries lack the job fields; malformed values never break loading.
        for key in ("egress_allow", "egress_deny", "job_setup"):
            raw = data.get(key)
            if raw is None or not isinstance(raw, (list, tuple)):
                data[key] = [] if raw is None or not isinstance(raw, str) else [raw]
            else:
                data[key] = [str(item) for item in raw if str(item or "").strip()]
        return cls(**data)


MAX_SETUP_COMMANDS = 20
MAX_SETUP_COMMAND_CHARS = 2000


def clean_job_setup(value: Any) -> list[str]:
    """Validated job setup commands (raises ``ValueError``): at most 20, each at most 2000 chars."""
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError("job_setup must be a list of commands")
    out: list[str] = []
    for raw in value:
        if not isinstance(raw, str):
            raise ValueError("job_setup commands must be strings")
        command = raw.strip()
        if not command:
            continue
        if len(command) > MAX_SETUP_COMMAND_CHARS:
            raise ValueError(f"a job setup command may be at most {MAX_SETUP_COMMAND_CHARS} characters")
        if "\0" in command:
            raise ValueError("job setup commands must not contain NUL bytes")
        out.append(command)
    if len(out) > MAX_SETUP_COMMANDS:
        raise ValueError(f"a template may have at most {MAX_SETUP_COMMANDS} job setup commands")
    return out


def clean_egress_allow(value: Any) -> list[str]:
    """Validated egress domains (the job sandbox's domain rule; raises ``ValueError``)."""
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError("egress_allow must be a list of domains")
    from mu.jobs.sandbox.repo_config import egress_domain_ok
    out: list[str] = []
    for raw in value:
        domain = str(raw or "").strip().lower().rstrip(".")
        if not domain:
            continue
        if not egress_domain_ok(domain):
            raise ValueError(f"invalid egress domain: {raw!r}")
        if domain not in out:
            out.append(domain)
    if len(out) > 200:
        raise ValueError("a template may allow at most 200 egress domains")
    return out


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

    def update(self, name: str, **changes: Any) -> ContainerTemplate | None:
        """Edit a template's job fields (``job_setup``, ``egress_allow``, ``description``).

        Values are validated; ``None`` leaves a field unchanged. Returns ``None`` when the
        template does not exist. Running jobs keep the snapshot they took."""
        with self._lock:
            template = self.get(name)
            if template is None:
                return None
            if changes.get("job_setup") is not None:
                template.job_setup = clean_job_setup(changes["job_setup"])
            if changes.get("egress_allow") is not None:
                template.egress_allow = clean_egress_allow(changes["egress_allow"])
            if changes.get("description") is not None:
                if not isinstance(changes["description"], str):
                    raise ValueError("description must be a string")
                template.description = changes["description"].strip()[:500]
            return self.upsert(template)

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
