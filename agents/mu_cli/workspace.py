from __future__ import annotations

import fnmatch
import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

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


@dataclass(slots=True)
class WorkspaceSnapshot:
    root: str
    files: list[WorkspaceFile] = field(default_factory=list)
    tool_runs: list[dict] = field(default_factory=list)
    index_stats: dict[str, int] = field(default_factory=dict)


class WorkspaceStore:
    def __init__(self, storage_dir: Path) -> None:
        self.storage_dir = storage_dir
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.snapshot: WorkspaceSnapshot | None = None
        self._db_path: Path | None = None

    def attach(self, root: Path) -> WorkspaceSnapshot:
        root = root.resolve()
        files, stats = self._index_files(root)
        self.snapshot = WorkspaceSnapshot(root=str(root), files=files, index_stats=stats)

        digest = hashlib.sha1(str(root).encode("utf-8")).hexdigest()[:12]
        self._db_path = self.storage_dir / f"workspace_{digest}.json"
        if self._db_path.exists():
            saved = json.loads(self._db_path.read_text(encoding="utf-8"))
            self.snapshot.tool_runs = saved.get("tool_runs", [])
            self.snapshot.index_stats = saved.get("index_stats", self.snapshot.index_stats)
        self._persist()
        return self.snapshot

    def _index_files(self, root: Path) -> tuple[list[WorkspaceFile], dict[str, int]]:
        ignore_patterns = self._read_gitignore(root)
        indexed: list[WorkspaceFile] = []
        stats = {
            "seen": 0,
            "indexed": 0,
            "ignored_by_gitignore": 0,
            "excluded_dirs": 0,
            "non_utf8_or_unreadable": 0,
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
            indexed.append(WorkspaceFile(path=rel, size_bytes=path.stat().st_size, preview=preview))
            stats["indexed"] += 1

        indexed.sort(key=lambda item: item.path)
        return indexed, stats

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
            files = [item for item in files if q in item.path.lower()]
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
            f"non_utf8_or_unreadable={self.snapshot.index_stats.get('non_utf8_or_unreadable', 0)}\n"
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
