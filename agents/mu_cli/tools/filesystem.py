from __future__ import annotations

import re
import subprocess
import textwrap
from pathlib import Path
from typing import Callable

from mu_cli.tools.base import ToolResult
from mu_cli.workspace import WorkspaceStore


class ReadFileTool:
    name = "read_file"
    description = "Read a UTF-8 text file from disk."
    mutating = False
    schema = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Path to file"}},
        "required": ["path"],
    }

    def __init__(self, workspace_root_getter: Callable[[], Path | None] | None = None) -> None:
        self.workspace_root_getter = workspace_root_getter

    def _resolve(self, path_value: str) -> tuple[Path | None, str | None]:
        root = self.workspace_root_getter() if self.workspace_root_getter else None
        raw = Path(path_value).expanduser()
        target = (root / raw).resolve() if root is not None and not raw.is_absolute() else raw.resolve()
        if root is not None:
            root_resolved = root.resolve()
            if target != root_resolved and root_resolved not in target.parents:
                return None, f"Path is outside attached workspace: {target}"
        return target, None

    def run(self, args: dict[str, str]) -> ToolResult:
        path, err = self._resolve(args["path"])
        if err:
            return ToolResult(ok=False, output=err)
        assert path is not None
        if not path.exists():
            return ToolResult(ok=False, output=f"Path not found: {path}")
        if path.is_dir():
            return ToolResult(ok=False, output=f"Path is a directory: {path}")
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult(ok=False, output=f"File is not UTF-8 text: {path}")
        return ToolResult(ok=True, output=content)


class WriteFileTool:
    name = "write_file"
    description = "Write UTF-8 content to a file path (creates parent dirs)."
    mutating = True
    schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to write"},
            "content": {"type": "string", "description": "UTF-8 text content"},
        },
        "required": ["path", "content"],
    }

    def __init__(self, workspace_root_getter: Callable[[], Path | None] | None = None) -> None:
        self.workspace_root_getter = workspace_root_getter

    def _resolve(self, path_value: str) -> tuple[Path | None, str | None]:
        root = self.workspace_root_getter() if self.workspace_root_getter else None
        raw = Path(path_value).expanduser()
        target = (root / raw).resolve() if root is not None and not raw.is_absolute() else raw.resolve()
        if root is not None:
            root_resolved = root.resolve()
            if target != root_resolved and root_resolved not in target.parents:
                return None, f"Path is outside attached workspace: {target}"
        return target, None

    def run(self, args: dict[str, str]) -> ToolResult:
        path, err = self._resolve(args["path"])
        if err:
            return ToolResult(ok=False, output=err)
        assert path is not None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(args["content"], encoding="utf-8")
        return ToolResult(ok=True, output=f"Wrote file: {path}")


def _normalize_patch_text(raw_patch: str) -> str:
    patch = textwrap.dedent(str(raw_patch or ""))

    fenced = re.match(r"^```(?:diff|patch)?\s*\n([\s\S]*?)\n```\s*$", patch.strip())
    if fenced:
        patch = fenced.group(1)

    lines = patch.splitlines()
    if lines and lines[0].strip().lower() in {"diff", "patch"}:
        patch = "\n".join(lines[1:])

    expanded: list[str] = []
    for line in patch.splitlines():
        if line and line[0] in {"+", "-", " "} and "\\n" in line:
            marker = line[0]
            parts = line[1:].split("\\n")
            expanded.extend(f"{marker}{part}" for part in parts)
            continue
        expanded.append(line)
    patch = "\n".join(expanded)

    if "\\n" in patch and patch.count("\\n") > patch.count("\n"):
        patch = patch.replace("\\n", "\n")
    if "\\t" in patch and patch.count("\\t") > patch.count("\t"):
        patch = patch.replace("\\t", "\t")

    if patch and not patch.endswith("\n"):
        patch += "\n"
    return patch


class ApplyPatchTool:
    name = "apply_patch"
    description = "Apply a unified diff patch using git apply."
    mutating = True
    schema = {
        "type": "object",
        "properties": {"patch": {"type": "string", "description": "Unified diff patch content"}},
        "required": ["patch"],
    }

    def __init__(self, workspace_root_getter: Callable[[], Path | None] | None = None) -> None:
        self.workspace_root_getter = workspace_root_getter

    def run(self, args: dict[str, str]) -> ToolResult:
        patch = _normalize_patch_text(args["patch"])
        root = self.workspace_root_getter() if self.workspace_root_getter else None
        proc = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", "-"],
            input=patch,
            text=True,
            capture_output=True,
            cwd=str(root) if root is not None else None,
        )
        if proc.returncode != 0:
            return ToolResult(ok=False, output=proc.stderr.strip() or "git apply failed")
        return ToolResult(ok=True, output="Patch applied successfully")


class GitTool:
    name = "git"
    description = "Run a constrained git operation (status, diff, log, add, commit)."
    mutating = True
    schema = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["status", "diff", "log", "add", "commit"],
                "description": "Git operation",
            },
            "args": {"type": "array", "items": {"type": "string"}, "description": "Extra args"},
        },
        "required": ["operation"],
    }

    SAFE_OPS = {
        "status": ["git", "status", "--short"],
        "diff": ["git", "diff"],
        "log": ["git", "log", "--oneline", "-n", "20"],
        "add": ["git", "add"],
        "commit": ["git", "commit"],
    }

    def __init__(self, workspace_root_getter: Callable[[], Path | None] | None = None) -> None:
        self.workspace_root_getter = workspace_root_getter

    def run(self, args: dict) -> ToolResult:
        op = str(args["operation"])
        extra = [str(item) for item in args.get("args", [])]
        if op not in self.SAFE_OPS:
            return ToolResult(ok=False, output=f"Unsupported operation: {op}")
        command = self.SAFE_OPS[op] + extra
        root = self.workspace_root_getter() if self.workspace_root_getter else None
        proc = subprocess.run(command, text=True, capture_output=True, cwd=str(root) if root is not None else None)
        output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        if proc.returncode != 0:
            return ToolResult(ok=False, output=output.strip() or "git command failed")
        return ToolResult(ok=True, output=output.strip() or "ok")


class ListWorkspaceFilesTool:
    name = "list_workspace_files"
    description = "List indexed workspace files, optionally filtered by a query."
    mutating = False
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Optional search substring"},
            "limit": {"type": "integer", "description": "Maximum files to return", "default": 25},
        },
    }

    def __init__(self, store: WorkspaceStore) -> None:
        self.store = store

    def run(self, args: dict[str, str | int]) -> ToolResult:
        query = args.get("query")
        limit = int(args.get("limit", 25))
        items = self.store.list_files(query=str(query) if query else None, limit=limit)
        if not items:
            return ToolResult(ok=True, output="No indexed files matched.")
        lines = [f"- {item.path} ({item.size_bytes} bytes)" for item in items]
        return ToolResult(ok=True, output="\n".join(lines))


class GetWorkspaceFileContextTool:
    name = "get_workspace_file_context"
    description = "Fetch context for an indexed workspace file by relative path."
    mutating = False
    schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Indexed relative file path"},
            "max_chars": {"type": "integer", "description": "Maximum characters", "default": 4000},
        },
        "required": ["path"],
    }

    def __init__(self, store: WorkspaceStore) -> None:
        self.store = store

    def run(self, args: dict[str, str | int]) -> ToolResult:
        path = str(args["path"])
        max_chars = int(args.get("max_chars", 4000))
        text = self.store.get_file_context(path=path, max_chars=max_chars)
        ok = not text.startswith("Path not indexed") and not text.startswith("Unable to read")
        return ToolResult(ok=ok, output=text)


class ListUploadedContextFilesTool:
    name = "list_uploaded_context_files"
    description = "List files in uploaded context store for the active session."
    mutating = False
    schema = {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Maximum files to return", "default": 50},
        },
    }

    def __init__(self, root_dir: Path, session_name_getter: Callable[[], str]) -> None:
        self.root_dir = root_dir
        self.session_name_getter = session_name_getter

    def run(self, args: dict) -> ToolResult:
        limit = int(args.get("limit", 50))
        session_dir = self.root_dir / self.session_name_getter()
        if not session_dir.exists():
            return ToolResult(ok=True, output="No uploaded context files.")
        files = [item for item in sorted(session_dir.iterdir(), key=lambda x: x.name.lower()) if item.is_file()]
        if not files:
            return ToolResult(ok=True, output="No uploaded context files.")
        lines = [f"- {file.name} ({file.stat().st_size} bytes)" for file in files[:limit]]
        return ToolResult(ok=True, output="\n".join(lines))


class GetUploadedContextFileTool:
    name = "get_uploaded_context_file"
    description = "Read an uploaded UTF-8 context file by filename from the active session store."
    mutating = False
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Filename in uploaded context store"},
            "max_chars": {"type": "integer", "description": "Maximum characters", "default": 8000},
        },
        "required": ["name"],
    }

    def __init__(self, root_dir: Path, session_name_getter: Callable[[], str]) -> None:
        self.root_dir = root_dir
        self.session_name_getter = session_name_getter

    def run(self, args: dict) -> ToolResult:
        name = Path(str(args["name"])).name
        max_chars = int(args.get("max_chars", 8000))
        session_dir = (self.root_dir / self.session_name_getter()).resolve()
        target = (session_dir / name).resolve()
        if session_dir not in target.parents and target != session_dir:
            return ToolResult(ok=False, output="Invalid uploaded file path")
        if not target.exists() or not target.is_file():
            return ToolResult(ok=False, output=f"Uploaded file not found: {name}")
        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult(ok=False, output=f"Uploaded file is not UTF-8 text: {name}")
        return ToolResult(ok=True, output=text[:max_chars])


class ClearUploadedContextStoreTool:
    name = "clear_uploaded_context_store"
    description = "Clear all uploaded context files for the active session store."
    mutating = True
    schema = {"type": "object", "properties": {}}

    def __init__(self, root_dir: Path, session_name_getter: Callable[[], str]) -> None:
        self.root_dir = root_dir
        self.session_name_getter = session_name_getter

    def run(self, args: dict) -> ToolResult:
        _ = args
        session_dir = self.root_dir / self.session_name_getter()
        if not session_dir.exists():
            return ToolResult(ok=True, output="Uploaded context store already empty.")
        removed = 0
        for item in session_dir.iterdir():
            if item.is_file():
                item.unlink()
                removed += 1
        return ToolResult(ok=True, output=f"Removed {removed} uploaded file(s).")
