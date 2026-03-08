from __future__ import annotations

import subprocess
from pathlib import Path

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

    def run(self, args: dict[str, str]) -> ToolResult:
        path = Path(args["path"]).expanduser()
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

    def run(self, args: dict[str, str]) -> ToolResult:
        path = Path(args["path"]).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(args["content"], encoding="utf-8")
        return ToolResult(ok=True, output=f"Wrote file: {path}")


class ApplyPatchTool:
    name = "apply_patch"
    description = "Apply a unified diff patch using git apply."
    mutating = True
    schema = {
        "type": "object",
        "properties": {"patch": {"type": "string", "description": "Unified diff patch content"}},
        "required": ["patch"],
    }

    def run(self, args: dict[str, str]) -> ToolResult:
        patch = args["patch"]
        proc = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", "-"],
            input=patch,
            text=True,
            capture_output=True,
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

    def run(self, args: dict) -> ToolResult:
        op = str(args["operation"])
        extra = [str(item) for item in args.get("args", [])]
        if op not in self.SAFE_OPS:
            return ToolResult(ok=False, output=f"Unsupported operation: {op}")
        command = self.SAFE_OPS[op] + extra
        proc = subprocess.run(command, text=True, capture_output=True)
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
