"""Durable, session-scoped storage for asynchronous sub-agent work.

The in-memory ``SubagentRegistry`` remains the live control plane. This store
is the recovery plane: it records start/progress/end events, the terminal
summary, and complete child history under the parent session directory.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

_TASK_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _json_safe(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, default=str))
    except Exception:
        return str(value)


class SubagentArtifactStore:
    """Atomic state files plus an append-only event journal per delegation."""

    schema_version = 1

    def __init__(self, session_dir: str, artifact_registry: Any = None) -> None:
        self.session_dir = os.path.abspath(os.path.expanduser(str(session_dir)))
        self.root = os.path.join(self.session_dir, "subagents")
        self.artifact_registry = artifact_registry
        self._lock = threading.RLock()
        os.makedirs(self.root, exist_ok=True)

    @staticmethod
    def _task_id(task_id: str) -> str:
        value = str(task_id or "").strip()
        if not _TASK_ID_RE.fullmatch(value):
            raise ValueError(f"invalid sub-agent task id: {value!r}")
        return value

    def _dir(self, task_id: str) -> str:
        return os.path.join(self.root, self._task_id(task_id))

    def _path(self, task_id: str, name: str) -> str:
        return os.path.join(self._dir(task_id), name)

    @staticmethod
    def _atomic_json(path: str, value: Any) -> None:
        directory = os.path.dirname(path)
        os.makedirs(directory, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".subagent-", suffix=".json.tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(_json_safe(value), handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _append_event(self, task_id: str, event: Dict[str, Any]) -> None:
        path = self._path(task_id, "events.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {"at": time.time(), **_json_safe(event)}
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")
            handle.flush()

    def _read_json(self, path: str) -> Optional[Dict[str, Any]]:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                value = json.load(handle)
        except (FileNotFoundError, OSError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def relative_path(self, task_id: str, name: str = "state.json") -> str:
        return str(Path("subagents", self._task_id(task_id), name))

    def start(self, task_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        now = time.time()
        state = {
            "schema_version": self.schema_version,
            "task_id": self._task_id(task_id),
            "status": "running",
            "started_at": now,
            "updated_at": now,
            "state_path": self.relative_path(task_id),
            "events_path": self.relative_path(task_id, "events.jsonl"),
            "result_path": self.relative_path(task_id, "result.json"),
            "history_path": self.relative_path(task_id, "history.json"),
            **_json_safe(payload),
        }
        with self._lock:
            self._atomic_json(self._path(task_id, "state.json"), state)
            self._append_event(task_id, {"kind": "subagent_start", **state})
        return dict(state)

    def record_event(self, task_id: str, event: Dict[str, Any], *, state_patch: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        with self._lock:
            state = self.load(task_id) or {
                "schema_version": self.schema_version,
                "task_id": self._task_id(task_id),
                "started_at": time.time(),
                "state_path": self.relative_path(task_id),
                "events_path": self.relative_path(task_id, "events.jsonl"),
                "result_path": self.relative_path(task_id, "result.json"),
                "history_path": self.relative_path(task_id, "history.json"),
            }
            if state_patch:
                state.update(_json_safe(state_patch))
            state["updated_at"] = time.time()
            self._atomic_json(self._path(task_id, "state.json"), state)
            self._append_event(task_id, event)
            return dict(state)

    def finish(self, task_id: str, payload: Dict[str, Any], history: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        with self._lock:
            state = self.load(task_id) or {
                "schema_version": self.schema_version,
                "task_id": self._task_id(task_id),
                "started_at": time.time(),
            }
            finished_at = time.time()
            state.update(_json_safe(payload))
            state.update({
                "updated_at": finished_at,
                "finished_at": finished_at,
                "state_path": self.relative_path(task_id),
                "events_path": self.relative_path(task_id, "events.jsonl"),
                "result_path": self.relative_path(task_id, "result.json"),
                "history_path": self.relative_path(task_id, "history.json"),
            })
            history_value = _json_safe(list(history or []))
            if not isinstance(history_value, list):
                history_value = []
            self._atomic_json(self._path(task_id, "history.json"), history_value)
            bundle = {"schema_version": self.schema_version, "state": state, "history": history_value}
            self._atomic_json(self._path(task_id, "result.json"), bundle)

            artifact = state.get("artifact")
            if not isinstance(artifact, dict) and self.artifact_registry is not None:
                try:
                    artifact = self.artifact_registry.add(
                        name=f"subagent-{self._task_id(task_id)}.json",
                        content=json.dumps(bundle, indent=2, sort_keys=True, default=str),
                        mime_type="application/json",
                        kind="file",
                        display="download",
                    )
                    state["artifact"] = artifact
                    bundle["state"] = state
                    self._atomic_json(self._path(task_id, "result.json"), bundle)
                except Exception:
                    artifact = None

            self._atomic_json(self._path(task_id, "state.json"), state)
            self._append_event(task_id, {
                "kind": "subagent_end",
                "status": state.get("status"),
                "summary": state.get("summary", ""),
                "error": state.get("error"),
                "artifact": artifact,
            })
            return dict(state)

    def load(self, task_id: str) -> Optional[Dict[str, Any]]:
        task_id = self._task_id(task_id)
        with self._lock:
            state = self._read_json(self._path(task_id, "state.json"))
            if state is not None:
                state.setdefault("durable", True)
                return state
            result = self._read_json(self._path(task_id, "result.json"))
            nested = result.get("state") if result else None
            if isinstance(nested, dict):
                nested = dict(nested)
                nested.setdefault("durable", True)
                return nested
        return None

    def list(self) -> list[Dict[str, Any]]:
        values: list[Dict[str, Any]] = []
        with self._lock:
            try:
                names = sorted(os.listdir(self.root))
            except OSError:
                return values
            for name in names:
                try:
                    value = self.load(name)
                except ValueError:
                    continue
                if value is not None:
                    values.append(value)
        return values


__all__ = ["SubagentArtifactStore"]
