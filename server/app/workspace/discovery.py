from __future__ import annotations

import fnmatch
import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pydantic import GetCoreSchemaHandler
from pydantic_core import CoreSchema, core_schema
from sqlalchemy.types import TypeDecorator, JSON


EXCLUDED_DIRS = {
    ".git",
    "node_modules",
    "dist",
    "build",
    "venv",
    ".venv",
    "__pycache__",
    ".idea",
    ".mypy_cache",
}


@dataclass(slots=True)
class WorkspaceFile:
    path: str
    size_bytes: int
    preview: str
    fingerprint: str = ""
    purpose: str = ""


@dataclass(slots=True)
class WorkspaceSnapshot:
    root: str
    files: list[WorkspaceFile] = field(default_factory=list)
    tool_runs: list[dict] = field(default_factory=list)
    index_stats: dict[str, int] = field(default_factory=dict)

class WorkspaceStoreType(TypeDecorator):
    impl = JSON
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, WorkspaceStore):
            return value.to_dict()
        if isinstance(value, dict):
            return value
        raise TypeError(f"Expected WorkspaceStore or dict, got {type(value)!r}")

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, WorkspaceStore):
            return value
        if isinstance(value, dict):
            return WorkspaceStore.from_dict(value)
        raise TypeError(f"Expected dict from DB, got {type(value)!r}")

class WorkspaceStore:
    def __init__(self, storage_dir: Path) -> None:
        self.storage_dir = storage_dir
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.snapshot: WorkspaceSnapshot | None = None
        self._db_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "storage_dir": str(self.storage_dir),
            "snapshot": None if self.snapshot is None else {
                "root": self.snapshot.root,
                "files": [asdict(f) for f in self.snapshot.files],
                "index_stats": self.snapshot.index_stats,
                "tool_runs": self.snapshot.tool_runs,
            },
            "db_path": None if self._db_path is None else str(self._db_path),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkspaceStore":
        store = cls(Path(data["storage_dir"]))

        snapshot_data = data.get("snapshot")
        if snapshot_data is not None:
            store.snapshot = WorkspaceSnapshot(
                root=snapshot_data["root"],
                files=[WorkspaceFile(**item) for item in snapshot_data.get("files", [])],
                index_stats=snapshot_data.get("index_stats", {}),
            )
            store.snapshot.tool_runs = snapshot_data.get("tool_runs", [])

        db_path = data.get("db_path")
        store._db_path = Path(db_path) if db_path else None
        return store

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type, handler):
        from pydantic_core import core_schema

        def validate(value):
            if isinstance(value, cls):
                return value
            if isinstance(value, Path):
                return cls(value)
            if isinstance(value, str):
                return cls(Path(value))
            if isinstance(value, dict):
                return cls.from_dict(value)
            raise ValueError(
                "WorkspaceStore must be provided as a WorkspaceStore instance, "
                "a path string, a pathlib.Path, or a serialized dict."
            )
        inner = core_schema.json_or_python_schema(
            json_schema=core_schema.no_info_plain_validator_function(validate),
            python_schema=core_schema.union_schema(
                [
                    core_schema.is_instance_schema(cls),
                    core_schema.no_info_plain_validator_function(validate),
                ]
            ),
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda value: value.to_dict(),
                return_schema=core_schema.dict_schema(),
            ),
        )

        return core_schema.nullable_schema(inner)

    def attach(self, root: Path) -> WorkspaceSnapshot:
        root = root.resolve()

        digest = hashlib.sha1(str(root).encode("utf-8")).hexdigest()[:12]
        self._db_path = self.storage_dir / f"workspace_{digest}.json"
        persisted: dict = {}
        if self._db_path.exists():
            persisted = json.loads(self._db_path.read_text(encoding="utf-8"))

        files, stats = self._index_files(root, persisted_files=persisted.get("files", []))
        self.snapshot = WorkspaceSnapshot(root=str(root), files=files, index_stats=stats)
        self.snapshot.tool_runs = persisted.get("tool_runs", [])
        self._persist()
        return self.snapshot

    def _index_files(self, root: Path, *, persisted_files: list[dict] | None = None) -> tuple[list[WorkspaceFile], dict[str, int]]:
        ignore_patterns = self._read_gitignore(root)
        persisted_by_path = {str(item.get("path") or ""): item for item in (persisted_files or [])}
        indexed: list[WorkspaceFile] = []
        stats = {
            "seen": 0,
            "indexed": 0,
            "ignored_by_gitignore": 0,
            "excluded_dirs": 0,
            "non_utf8_or_unreadable": 0,
            "reused_descriptions": 0,
            "recomputed_descriptions": 0,
        }

        for path in root.rglob("*"):
            if path.is_dir():
                if path.name in EXCLUDED_DIRS:
                    stats["excluded_dirs"] += 1
                    continue
                continue

            stats["seen"] += 1
            rel = path.relative_to(root).as_posix()
            if any(part in EXCLUDED_DIRS for part in path.parts):
                stats["excluded_dirs"] += 1
                continue
            if self._matches_patterns(rel, ignore_patterns):
                stats["ignored_by_gitignore"] += 1
                continue

            try:
                content = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                stats["non_utf8_or_unreadable"] += 1
                continue

            preview = "\n".join(content.splitlines()[:8])[:1000]
            fingerprint = self._fingerprint_content(content)

            persisted = persisted_by_path.get(rel, {})
            persisted_fingerprint = str(persisted.get("fingerprint") or "")
            persisted_purpose = str(persisted.get("purpose") or "").strip()
            if persisted_fingerprint and persisted_fingerprint == fingerprint and persisted_purpose:
                purpose = persisted_purpose
                stats["reused_descriptions"] += 1
            else:
                purpose = self._infer_file_purpose(path=path, rel_path=rel, preview=preview)
                stats["recomputed_descriptions"] += 1

            indexed.append(
                WorkspaceFile(
                    path=rel,
                    size_bytes=path.stat().st_size,
                    preview=preview,
                    fingerprint=fingerprint,
                    purpose=purpose,
                )
            )
            stats["indexed"] += 1

        indexed.sort(key=lambda item: item.path)
        return indexed, stats

    @staticmethod
    def _fingerprint_content(content: str) -> str:
        return hashlib.sha1(content.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _infer_file_purpose(*, path: Path, rel_path: str, preview: str) -> str:
        name = path.name.lower()
        suffix = path.suffix.lower()
        first = ""
        for line in preview.splitlines():
            if line.strip():
                first = line.strip()
                break

        if name in {"readme.md", "readme"}:
            return "Project documentation and onboarding details."
        if name.startswith("test_") or "/tests/" in rel_path or rel_path.startswith("tests/"):
            return "Automated tests that validate behavior and guard regressions."
        if suffix in {".md", ".rst"}:
            return "Human-readable documentation for workflows or architecture."
        if suffix in {".json", ".yaml", ".yml", ".toml", ".ini"}:
            return "Configuration and structured project metadata."
        if suffix in {".html", ".css", ".js", ".ts", ".tsx", ".jsx"}:
            return "Frontend/UI asset used for rendering or browser interactions."
        if suffix in {".py", ".go", ".rs", ".java", ".kt", ".rb", ".php", ".c", ".cpp", ".h"}:
            if first.startswith("class "):
                return f"Source module defining classes; starts with: {first[:100]}"
            if first.startswith("def ") or first.startswith("func "):
                return f"Source module exposing functions; starts with: {first[:100]}"
            if "if __name__ == \"__main__\"" in preview:
                return "Executable entrypoint/module for command-line usage."
            return "Source module implementing runtime behavior."
        if suffix in {".sh", ".bash", ".zsh"}:
            return "Shell automation script for local/dev workflows."
        return "Repository file indexed for retrieval and targeted context expansion."

    def _read_gitignore(self, root: Path) -> list[str]:
        gitignore = root / ".gitignore"
        if not gitignore.exists():
            return []
        lines = [line.strip() for line in gitignore.read_text(encoding="utf-8").splitlines()]
        return [line for line in lines if line and not line.startswith("#")]

    @staticmethod
    def _matches_patterns(rel_path: str, patterns: list[str]) -> bool:
        return any(fnmatch.fnmatch(rel_path, pattern.rstrip("/")) for pattern in patterns)

    def list_files(self, query: str | None = None, limit: int = 25) -> list[WorkspaceFile]:
        if self.snapshot is None:
            return []
        files = self.snapshot.files
        if query:
            q = query.lower()
            files = [item for item in files if q in item.path.lower() or q in item.purpose.lower()]
        return files[:limit]

    def get_file_context(self, path: str, max_chars: int = 4000) -> str:
        if self.snapshot is None:
            return "No workspace attached."

        match = next((item for item in self.snapshot.files if item.path == path), None)
        if match is None:
            lowered = path.lower()
            suggestions = [item.path for item in self.snapshot.files if lowered in item.path.lower()][:8]
            hint = f"\nClosest matches:\n" + "\n".join(f"- {item}" for item in suggestions) if suggestions else ""
            return f"Path not indexed in workspace: {path}{hint}"

        root = Path(self.snapshot.root)
        full = root / match.path
        try:
            content = full.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            return f"Unable to read file: {exc}"

        return content[:max_chars]

    def record_tool_run(self, tool_name: str, args: dict, output: str, ok: bool) -> None:
        if self.snapshot is None:
            return
        self.snapshot.tool_runs.append(
            {
                "tool": tool_name,
                "args": args,
                "ok": ok,
                "output_preview": output[:500],
            }
        )
        self._persist()

    def summary(self) -> str:
        if self.snapshot is None:
            return "No workspace attached."
        return (
            f"Workspace: {self.snapshot.root}\n"
            f"Indexed files: {len(self.snapshot.files)}\n"
            f"Index stats: seen={self.snapshot.index_stats.get('seen', 0)} "
            f"gitignored={self.snapshot.index_stats.get('ignored_by_gitignore', 0)} "
            f"non_utf8_or_unreadable={self.snapshot.index_stats.get('non_utf8_or_unreadable', 0)} "
            f"reused_descriptions={self.snapshot.index_stats.get('reused_descriptions', 0)}\n"
            f"Recorded tool runs: {len(self.snapshot.tool_runs)}"
        )

    def _persist(self) -> None:
        if self.snapshot is None or self._db_path is None:
            return
        payload = {
            "root": self.snapshot.root,
            "files": [asdict(item) for item in self.snapshot.files],
            "tool_runs": self.snapshot.tool_runs,
            "index_stats": self.snapshot.index_stats,
        }
        self._db_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
