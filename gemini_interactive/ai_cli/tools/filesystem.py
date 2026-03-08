from __future__ import annotations

from pathlib import Path

from ai_cli.tools.base import ToolResult


class ReadFileTool:
    name = "read_file"
    description = "Read a UTF-8 text file from disk."
    schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to file"},
        },
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
