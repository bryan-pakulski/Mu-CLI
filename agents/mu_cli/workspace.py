from __future__ import annotations

import fnmatch
import hashlib
import json
import re
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

SECRET_PATH_PATTERNS = ["*.env", "*.pem", "*.key", "*id_rsa*", "*token*"]
SECRET_CONTENT_PATTERN = re.compile(r"(api[_-]?key|token|secret|password)\s*[:=]", re.IGNORECASE)


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


class WorkspaceStore:
    def __init__(self, storage_dir: Path) -> None:
        self.storage_dir = storage_dir
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.snapshot: WorkspaceSnapshot | None = None
        self._db_path: Path | None = None

    def attach(self, root: Path) -> WorkspaceSnapshot:
        root = root.resolve()
        files = self._index_files(root)
        self.snapshot = WorkspaceSnapshot(root=str(root), files=files)

        digest = hashlib.sha1(str(root).encode("utf-8")).hexdigest()[:12]
        self._db_path = self.storage_dir / f"workspace_{digest}.json"
        if self._db_path.exists():
            saved = json.loads(self._db_path.read_text(encoding="utf-8"))
            self.snapshot.tool_runs = saved.get("tool_runs", [])
        self._persist()
        return self.snapshot

    def _index_files(self, root: Path) -> list[WorkspaceFile]:
        ignore_patterns = self._read_gitignore(root)
        indexed: list[WorkspaceFile] = []

        for path in root.rglob("*"):
            if path.is_dir():
                if path.name in EXCLUDED_DIRS:
                    continue
                continue

            rel = path.relative_to(root).as_posix()
            if any(part in EXCLUDED_DIRS for part in path.parts):
                continue
            if self._matches_patterns(rel, ignore_patterns):
                continue
            if self._is_secret_path(rel):
                continue

            try:
                content = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if SECRET_CONTENT_PATTERN.search(content):
                continue

            preview = "\n".join(content.splitlines()[:8])[:1000]
            indexed.append(WorkspaceFile(path=rel, size_bytes=path.stat().st_size, preview=preview))

        indexed.sort(key=lambda item: item.path)
        return indexed

    def _read_gitignore(self, root: Path) -> list[str]:
        gitignore = root / ".gitignore"
        if not gitignore.exists():
            return []
        lines = [line.strip() for line in gitignore.read_text(encoding="utf-8").splitlines()]
        return [line for line in lines if line and not line.startswith("#")]

    @staticmethod
    def _matches_patterns(rel_path: str, patterns: list[str]) -> bool:
        return any(fnmatch.fnmatch(rel_path, pattern.rstrip("/")) for pattern in patterns)

    @staticmethod
    def _is_secret_path(rel_path: str) -> bool:
        return any(fnmatch.fnmatch(rel_path.lower(), pattern.lower()) for pattern in SECRET_PATH_PATTERNS)

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
            return f"Path not indexed in workspace: {path}"

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
            f"Recorded tool runs: {len(self.snapshot.tool_runs)}"
        )

    def _persist(self) -> None:
        if self.snapshot is None or self._db_path is None:
            return
        payload = {
            "root": self.snapshot.root,
            "files": [asdict(item) for item in self.snapshot.files],
            "tool_runs": self.snapshot.tool_runs,
        }
        self._db_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
