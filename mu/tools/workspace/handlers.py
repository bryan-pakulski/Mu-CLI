"""Workspace inspector `@tool` handlers.

Thin wrappers over the implementations in `core/tools.py`. The
implementation functions stay where they are for now (other call sites
in the legacy code still use them); this module is the registration
surface the new agent loop sees.
"""

from typing import Any, Dict

from mu.tools import tool


# Late imports so this module loads cleanly even if `core.tools` is
# re-entered during its own import. Imports happen inside each handler
# body — pattern matches `mu/tools/agent/spawn.py`.


# ---------------------------------------------------------------- workspace shape


@tool(
    name="get_workspace_details",
    description=(
        "Returns a string of the current workspace's path, files, and "
        "folders. Use this to discover the structure of the attached project."
    ),
    parameters={"type": "object", "properties": {}},
    requires_approval=False,
    execution_kind="read",
    preview_policy="none",
    result_mode="structured+collated",
    summary_builder="parse_workspace_details",
)
def get_workspace_details(args: Dict[str, Any], context) -> str:
    from core.tools import get_workspace_details as _impl

    return _impl(context.folder_context)


# ---------------------------------------------------------------- file read


@tool(
    name="read_file",
    description=(
        "Returns the whole file contents. Use this to read the code or "
        "text of a specific file."
    ),
    parameters={
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "The absolute or relative path to the file.",
            }
        },
        "required": ["filename"],
    },
    requires_approval=False,
    execution_kind="read",
    preview_policy="none",
    result_mode="structured+collated",
    summary_builder="read_file_preview",
)
def read_file(args: Dict[str, Any], context) -> str:
    from core.tools import read_file as _impl

    return _impl(args.get("filename", ""), context.folder_context)


@tool(
    name="get_chunk",
    description=(
        "Returns a string of the file contents between the start and end "
        "line numbers. Use this to read a specific portion of a large file."
    ),
    parameters={
        "type": "object",
        "properties": {
            "file": {
                "type": "string",
                "description": "The absolute or relative path to the file.",
            },
            "start_line": {
                "type": "integer",
                "description": "The starting line number (1-indexed).",
            },
            "end_line": {
                "type": "integer",
                "description": "The ending line number.",
            },
        },
        "required": ["file", "start_line", "end_line"],
    },
    requires_approval=False,
    execution_kind="read",
    preview_policy="none",
    result_mode="structured+collated",
    summary_builder="chunk_preview",
)
def get_chunk(args: Dict[str, Any], context) -> str:
    from core.tools import get_chunk as _impl

    return _impl(
        args.get("file", ""),
        args.get("start_line", 1),
        args.get("end_line", 100),
        context.folder_context,
    )


# ---------------------------------------------------------------- search


@tool(
    name="search_for_string",
    description=(
        "Returns a list of all files that contain the string as well as "
        "the line number. Use this to find variable usages, function "
        "definitions, or specific text."
    ),
    parameters={
        "type": "object",
        "properties": {
            "string": {
                "type": "string",
                "description": "The exact text string to search for in the codebase.",
            }
        },
        "required": ["string"],
    },
    requires_approval=False,
    execution_kind="read",
    preview_policy="none",
    result_mode="structured+collated",
    summary_builder="parse_search_results",
)
def search_for_string(args: Dict[str, Any], context) -> str:
    from core.tools import search_for_string as _impl

    return _impl(args.get("string", ""), context.folder_context)


@tool(
    name="search_references",
    description=(
        "Searches the whole project workspace for references to a query "
        "string. Returns a list of matches with filepath, line_number, "
        "and a short context snippet (surrounding lines) for each match. "
        "This complements search_for_string by providing surrounding "
        "context lines."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The exact text string to search for across all workspace files.",
            },
            "context_lines": {
                "type": "integer",
                "description": "Number of context lines before and after each match (default 3).",
                "default": 3,
            },
        },
        "required": ["query"],
    },
    requires_approval=False,
    execution_kind="read",
    preview_policy="none",
    result_mode="structured+collated",
    summary_builder="parse_search_results",
)
def search_references(args: Dict[str, Any], context) -> str:
    from core.tools import search_references as _impl

    return _impl(
        args.get("query", ""),
        context.folder_context,
        context_lines=args.get("context_lines", 3),
    )


@tool(
    name="retrieve_relevant_context",
    description=(
        "Retrieve semantically relevant code snippets using indexed "
        "symbols, lexical overlap, recency, and git-diff weighting."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural-language or code query describing what context is needed.",
            },
            "top_k": {
                "type": "integer",
                "description": "Number of snippets to return.",
                "default": 5,
            },
            "filters": {
                "type": "object",
                "description": "Optional retrieval filters (e.g., path_prefix, extensions).",
            },
        },
        "required": ["query"],
    },
    requires_approval=False,
    execution_kind="read",
    preview_policy="none",
    result_mode="structured+collated",
)
def retrieve_relevant_context(args: Dict[str, Any], context) -> str:
    from core.tools import retrieve_relevant_context as _impl

    return _impl(
        args.get("query", ""),
        context.folder_context,
        top_k=args.get("top_k", 5),
        filters=args.get("filters", {}),
    )


# ---------------------------------------------------------------- listing


@tool(
    name="list_dir",
    description="Returns a list of files and directories in the specified path.",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "The directory path to list (defaults to current dir).",
            }
        },
    },
    requires_approval=False,
    execution_kind="read",
    preview_policy="none",
    result_mode="structured+collated",
    summary_builder="parse_list_dir",
)
def list_dir(args: Dict[str, Any], context) -> str:
    from core.tools import list_dir as _impl

    return _impl(args.get("path", ""), context.folder_context)
