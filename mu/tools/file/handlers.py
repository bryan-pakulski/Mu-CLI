"""File mutator `@tool` handlers.

`write_file` / `apply_diff` / `search_and_replace_file`. All three keep
`requires_approval=True` so the loop's approval pipeline gates them
before they run. Underlying implementations remain in `core/tools.py`.
"""

from typing import Any, Dict

from mu.tools import tool


@tool(
    name="write_file",
    description="Creates or overwrites a file with the provided content.",
    parameters={
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "Path to the file."},
            "content": {"type": "string", "description": "Content to write."},
        },
        "required": ["filename", "content"],
    },
    requires_approval=True,
    execution_kind="mutate",
    preview_policy="required",
)
def write_file(args: Dict[str, Any], context) -> str:
    from core.tools import write_file as _impl

    return _impl(
        args.get("filename", ""),
        args.get("content", ""),
        context.folder_context,
    )


@tool(
    name="apply_diff",
    description=(
        "Applies a unified diff to a file. This is a FALLBACK method. Use "
        "search_and_replace_file as the PRIMARY method for targeted code "
        "changes. Use apply_diff only for complex multi-file changes."
    ),
    parameters={
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "Path to the file to modify.",
            },
            "diff": {
                "type": "string",
                "description": (
                    "The unified diff content to apply. MUST follow standard "
                    "unified diff format: --- filename, +++ filename, "
                    "@@ -L,C +L,C @@ headers, and +/-/space line markers."
                ),
            },
        },
        "required": ["filename", "diff"],
    },
    requires_approval=True,
    execution_kind="mutate",
    preview_policy="required",
)
def apply_diff(args: Dict[str, Any], context) -> str:
    from core.tools import apply_diff as _impl

    return _impl(
        args.get("filename", ""),
        args.get("diff", ""),
        context.folder_context,
    )


@tool(
    name="search_and_replace_file",
    description=(
        "Search and replace text in a file using exact string matching. "
        "This is the PRIMARY method for targeted code modifications. Use "
        "apply_diff (unified diff) only as a fallback for complex multi-file "
        "changes."
    ),
    parameters={
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "Path to the file."},
            "search": {
                "type": "string",
                "description": (
                    "The exact text to search for in the file. Must match "
                    "exactly including whitespace."
                ),
            },
            "replace": {
                "type": "string",
                "description": "The text to replace the search match with.",
            },
            "expected_count": {
                "type": "integer",
                "description": (
                    "Optional expected number of matches. If provided and "
                    "count differs, operation fails (safety check for "
                    "disambiguation)."
                ),
            },
            "normalize_whitespace": {
                "type": "boolean",
                "description": (
                    "If True, normalize whitespace in search pattern "
                    "(collapse multiple spaces, trim leading/trailing)."
                ),
                "default": False,
            },
            "dry_run": {
                "type": "boolean",
                "description": (
                    "If True, return preview of changes without modifying "
                    "the file."
                ),
                "default": False,
            },
        },
        "required": ["filename", "search", "replace"],
    },
    requires_approval=True,
    execution_kind="mutate",
    preview_policy="required",
)
def search_and_replace_file(args: Dict[str, Any], context) -> str:
    from core.tools import search_and_replace_file as _impl

    return _impl(
        args.get("filename", ""),
        args.get("search", ""),
        args.get("replace", ""),
        args.get("expected_count"),
        args.get("normalize_whitespace", False),
        args.get("dry_run", False),
        context.folder_context,
    )
