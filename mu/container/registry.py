"""Persistent many-session-to-one-container registry."""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

from utils.config import HISTORY_DIR

from .ref import DEFAULT_WORKER_PORT, ContainerRef


class ContainerRegistry:
    def __init__(self, root: str | None = None):
        self.root = os.path.abspath(
            os.path.expanduser(root or os.path.join(HISTORY_DIR, "containers"))
        )
        self.registry_path = os.path.join(self.root, "registry.json")
        self._lock = threading.RLock()
        os.makedirs(self.root, exist_ok=True)

    def _read(self) -> dict[str, dict]:
        try:
            with open(self.registry_path, "r", encoding="utf-8") as handle:
                value = json.load(handle)
        except (FileNotFoundError, OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write(self, value: dict[str, dict]) -> None:
        fd, path = tempfile.mkstemp(prefix="registry-", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(path, self.registry_path)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def _write_ref_file(self, ref: ContainerRef) -> None:
        directory = os.path.join(self.root, ref.name)
        os.makedirs(directory, exist_ok=True)
        target = os.path.join(directory, "container.json")
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(ref.to_dict(), handle, indent=2, sort_keys=True)

    def upsert(self, ref: ContainerRef) -> ContainerRef:
        with self._lock:
            data = self._read()
            data[ref.name] = ref.to_dict()
            self._write(data)
            self._write_ref_file(ref)
        return ref

    def get(self, name: str) -> ContainerRef | None:
        with self._lock:
            value = self._read().get(str(name))
        return ContainerRef.from_dict(value) if isinstance(value, dict) else None

    def list_containers(self) -> list[ContainerRef]:
        with self._lock:
            values = self._read().values()
            refs = [ContainerRef.from_dict(value) for value in values if isinstance(value, dict)]
        return sorted(refs, key=lambda item: item.created_at, reverse=True)

    def allocate_worker_port(
        self,
        *,
        start: int = DEFAULT_WORKER_PORT,
        exclude_name: str | None = None,
    ) -> int:
        """Return the first unused managed-worker port.

        Workers are addressed on their private Docker IPs, so sharing a port
        would technically work.  Keeping a stable unique port per managed
        environment makes diagnostics clearer and avoids clashes with custom
        images that already use the conventional worker port.
        """
        with self._lock:
            used = {
                int(ref.worker_port)
                for ref in self.list_containers()
                if ref.name != exclude_name and int(ref.worker_port or 0) > 0
            }
            candidate = max(1, int(start))
            while candidate in used:
                candidate += 1
            return candidate

    def attach_session(self, container_name: str, session_name: str) -> ContainerRef:
        ref = self.get(container_name)
        if ref is None:
            raise KeyError(f"container not registered: {container_name}")
        if session_name not in ref.attached_sessions:
            ref.attached_sessions.append(session_name)
        return self.upsert(ref)

    def detach_session(self, container_name: str, session_name: str) -> ContainerRef:
        ref = self.get(container_name)
        if ref is None:
            raise KeyError(f"container not registered: {container_name}")
        ref.attached_sessions = [name for name in ref.attached_sessions if name != session_name]
        return self.upsert(ref)

    def remove(self, name: str, *, force: bool = False) -> bool:
        with self._lock:
            data = self._read()
            value = data.get(name)
            if not isinstance(value, dict):
                return False
            ref = ContainerRef.from_dict(value)
            if ref.attached_sessions and not force:
                raise RuntimeError(
                    f"container {name!r} still has attached sessions: "
                    + ", ".join(ref.attached_sessions)
                )
            data.pop(name, None)
            self._write(data)
            path = Path(self.root, name, "container.json")
            try:
                path.unlink()
                path.parent.rmdir()
            except OSError:
                pass
            return True
