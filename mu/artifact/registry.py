"""Durable, session-scoped artifact registry.

Artifacts are intentionally separate from ``session.json``.  A corrupt or large
artifact registry therefore cannot prevent a conversation from loading.
"""
from __future__ import annotations

import json
import mimetypes
import os
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any


class ArtifactError(ValueError):
    """Raised for invalid artifact inputs or registry operations."""


def _safe_name(name: str) -> str:
    raw = str(name or "").strip().replace("\\", "/")
    candidate = raw.rsplit("/", 1)[-1]
    if not candidate or candidate in {".", ".."} or "\x00" in candidate:
        raise ArtifactError("artifact name must be a non-empty file name")
    return candidate[:240]


class ArtifactRegistry:
    def __init__(self, session_dir: str, *, max_bytes: int | None = None):
        self.session_dir = os.path.abspath(os.path.expanduser(session_dir))
        self.artifacts_dir = os.path.join(self.session_dir, "artifacts")
        self.registry_path = os.path.join(self.artifacts_dir, "registry.json")
        self.max_bytes = int(
            max_bytes
            if max_bytes is not None
            else os.getenv("MUCLI_ARTIFACT_MAX_BYTES", 100 * 1024 * 1024)
        )
        self._lock = threading.RLock()
        os.makedirs(self.artifacts_dir, exist_ok=True)

    @property
    def session_name(self) -> str:
        return os.path.basename(self.session_dir.rstrip(os.sep))

    def _read(self) -> list[dict[str, Any]]:
        try:
            with open(self.registry_path, "r", encoding="utf-8") as handle:
                value = json.load(handle)
        except FileNotFoundError:
            return []
        except (OSError, ValueError):
            # Keep the invalid file for forensic recovery; start a usable view.
            return []
        return value if isinstance(value, list) else []

    def _write(self, entries: list[dict[str, Any]]) -> None:
        os.makedirs(self.artifacts_dir, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            prefix="registry-", suffix=".json.tmp", dir=self.artifacts_dir
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(entries, handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.registry_path)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def _descriptor(
        self, artifact_id: str, name: str, size: int, mime_type: str
    ) -> dict[str, Any]:
        return {
            "artifact_id": artifact_id,
            "name": name,
            "size": int(size),
            "mime_type": mime_type or "application/octet-stream",
            "created_at": time.time(),
            "download_url": (
                f"/api/sessions/{self.session_name}/artifacts/"
                f"{artifact_id}/download"
            ),
        }

    def add(
        self,
        name: str,
        source_path: str | None = None,
        content: str | bytes | None = None,
        mime_type: str = "application/octet-stream",
    ) -> dict[str, Any]:
        if (source_path is None) == (content is None):
            raise ArtifactError("provide exactly one of source_path or content")
        safe_name = _safe_name(name)
        artifact_id = uuid.uuid4().hex
        target_dir = os.path.join(self.artifacts_dir, artifact_id)
        target_path = os.path.join(target_dir, safe_name)

        if source_path is not None:
            source = os.path.abspath(os.path.expanduser(str(source_path)))
            if not os.path.isfile(source):
                raise ArtifactError(f"artifact source is not a file: {source}")
            size = os.path.getsize(source)
        else:
            payload = content.encode("utf-8") if isinstance(content, str) else bytes(content or b"")
            size = len(payload)

        if size > self.max_bytes:
            raise ArtifactError(
                f"artifact is {size} bytes; maximum is {self.max_bytes} bytes"
            )

        resolved_mime = (
            mime_type
            if mime_type and mime_type != "application/octet-stream"
            else mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
        )

        with self._lock:
            os.makedirs(target_dir, exist_ok=False)
            try:
                if source_path is not None:
                    shutil.copy2(source, target_path)
                else:
                    with open(target_path, "wb") as handle:
                        handle.write(payload)
                descriptor = self._descriptor(
                    artifact_id, safe_name, os.path.getsize(target_path), resolved_mime
                )
                entries = self._read()
                entries.append(descriptor)
                self._write(entries)
                return dict(descriptor)
            except Exception:
                shutil.rmtree(target_dir, ignore_errors=True)
                raise

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            entries = []
            changed = False
            for entry in self._read():
                artifact_id = str(entry.get("artifact_id") or "")
                path = self.resolve_path(artifact_id, _entry=entry)
                if path and os.path.isfile(path):
                    fresh = dict(entry)
                    fresh["size"] = os.path.getsize(path)
                    entries.append(fresh)
                else:
                    changed = True
            if changed:
                self._write(entries)
            return sorted(
                entries, key=lambda item: float(item.get("created_at", 0) or 0), reverse=True
            )

    def get(self, artifact_id: str) -> dict[str, Any] | None:
        target = str(artifact_id or "").strip()
        with self._lock:
            for entry in self._read():
                if entry.get("artifact_id") == target:
                    return dict(entry)
        return None

    def resolve_path(
        self, artifact_id: str, *, _entry: dict[str, Any] | None = None
    ) -> str | None:
        entry = _entry or self.get(artifact_id)
        if not entry:
            return None
        candidate = Path(self.artifacts_dir, str(entry["artifact_id"]), _safe_name(entry["name"]))
        resolved = candidate.resolve()
        root = Path(self.artifacts_dir).resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            return None
        return str(resolved)

    def remove(self, artifact_id: str) -> bool:
        target = str(artifact_id or "").strip()
        with self._lock:
            entries = self._read()
            kept = [entry for entry in entries if entry.get("artifact_id") != target]
            if len(kept) == len(entries):
                return False
            shutil.rmtree(os.path.join(self.artifacts_dir, target), ignore_errors=True)
            self._write(kept)
            return True
