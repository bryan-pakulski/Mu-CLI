"""Workspace inspector `@tool` handlers.

Read-only tools the agent uses to discover and inspect the attached
workspace: `get_workspace_details`, `read_file`, `get_chunk`,
`search_for_string`, `search_references`, `retrieve_relevant_context`,
`list_dir`.

Each tool is a positional-signature body (publicly callable, easy to
test) plus a thin `_<name>_tool(args, context)` wrapper that carries
the `@tool` registration.
"""

from __future__ import annotations

import html
import json
import os
from typing import Any, Dict

from mu.tools import tool
from mu.tools._bounds import check_bounds as _check_bounds
from mu.tools._scrub import scrub_and_annotate as _scrub_and_annotate
from mu.security.secret_paths import redact_secrets
from utils.logger import logger


# ---------------------------------------------------------------- workspace shape


def get_workspace_details(folder_context) -> str:
    """Returns a string of the current workspace's path, files, and folders."""
    if not folder_context or not folder_context.folders:
        return "No workspace attached."

    details = ["Workspace Folders:"]
    for f in folder_context.folders:
        details.append(f" - {f}")

    details.append("\nTracked Files:")
    for f in folder_context.get_file_list():
        details.append(f" - {f}")

    return "\n".join(details)


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
def _get_workspace_details_tool(args: Dict[str, Any], context) -> str:
    return get_workspace_details(context.folder_context)


# ---------------------------------------------------------------- file read


def read_file(filename: str, folder_context) -> str:
    """Returns the whole file contents."""
    if not _check_bounds(filename, folder_context):
        logger.warning(f"read_file: Access denied or file ignored: {filename}")
        return (
            f"Error: Access denied or file ignored. '{filename}' is outside "
            "boundaries or in ignore list."
        )
    try:
        with open(filename, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        return (
            f"Error: File '{filename}' not found. Try using search_for_string "
            "to locate it."
        )
    except UnicodeDecodeError:
        return (
            f"Error: '{filename}' appears to be a binary file or has an "
            "unsupported encoding."
        )
    except Exception as e:
        logger.error(f"read_file: Error reading {filename}: {e}")
        return f"Error reading file: {e}"
    return _scrub_and_annotate(content)


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
def _read_file_tool(args: Dict[str, Any], context) -> str:
    return read_file(args.get("filename", ""), context.folder_context)


def get_chunk(filename: str, start_line: int, end_line: int, folder_context) -> str:
    """Returns a string of the file contents between the start and end line numbers."""
    if not _check_bounds(filename, folder_context):
        logger.warning(f"get_chunk: Access denied or file ignored: {filename}")
        return (
            f"Error: Access denied or file ignored. '{filename}' is outside "
            "boundaries or in ignore list."
        )
    try:
        with open(filename, "r", encoding="utf-8") as f:
            lines = f.readlines()

        start_idx = max(0, start_line - 1)
        end_idx = min(len(lines), end_line)

        chunk = lines[start_idx:end_idx]
        return _scrub_and_annotate("".join(chunk))
    except FileNotFoundError:
        return (
            f"Error: File '{filename}' not found. Try using search_for_string "
            "to locate it."
        )
    except UnicodeDecodeError:
        return (
            f"Error: '{filename}' appears to be a binary file or has an "
            "unsupported encoding."
        )
    except Exception as e:
        logger.error(f"get_chunk: Error reading {filename}: {e}")
        return f"Error reading file chunk: {e}"


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
def _get_chunk_tool(args: Dict[str, Any], context) -> str:
    return get_chunk(
        args.get("file", ""),
        args.get("start_line", 1),
        args.get("end_line", 100),
        context.folder_context,
    )


# ---------------------------------------------------------------- search


def search_for_string(search_string: str, folder_context) -> str:
    """Returns a list of all files that contain the string plus the line number."""
    # Provider SDKs (especially Gemini) escape angle brackets in tool call
    # args; '&lt;/style&gt;' must match '</style>' in files.
    search_string = html.unescape(search_string)

    if not folder_context:
        return "No workspace attached to search."

    folder_context.sync_with_filesystem()

    results = []
    for filepath in folder_context.get_file_list():
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                for line_num, line in enumerate(f, 1):
                    if search_string in line:
                        results.append(f"{filepath}:{line_num} -> {line.strip()}")
        except Exception:
            continue

    if not results:
        return f"No matches found for '{search_string}'"
    return _scrub_and_annotate("\n".join(results))


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
def _search_for_string_tool(args: Dict[str, Any], context) -> str:
    return search_for_string(args.get("string", ""), context.folder_context)


def search_references(query: str, folder_context, context_lines: int = 3) -> str:
    """Searches all workspace files for the query string and returns matches with context."""
    if not folder_context:
        return json.dumps({"error": "No workspace attached to search."})
    if not str(query or "").strip():
        return json.dumps({"error": "query is required"})

    context_lines = 3 if context_lines is None else max(0, int(context_lines))
    results = []
    candidate_files = set(folder_context.get_file_list() or [])
    for root in getattr(folder_context, "folders", []) or []:
        if not root or not os.path.isdir(root):
            continue
        for dirpath, _, filenames in os.walk(root):
            for filename in filenames:
                fullpath = os.path.join(dirpath, filename)
                if folder_context and folder_context.is_ignored(fullpath):
                    continue
                candidate_files.add(fullpath)

    for filepath in sorted(candidate_files):
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
        except Exception:
            continue
        for i, line in enumerate(lines):
            if query in line:
                start = max(0, i - context_lines)
                end = min(len(lines), i + context_lines + 1)
                snippet = "".join(lines[start:end])
                scrubbed_snippet, _ = redact_secrets(snippet.rstrip())
                results.append(
                    {
                        "filepath": filepath,
                        "line_number": i + 1,
                        "context_snippet": scrubbed_snippet,
                    }
                )

    if not results:
        return json.dumps({"query": query, "count": 0, "results": []})
    return json.dumps(
        {"query": query, "count": len(results), "results": results}, indent=2
    )


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
def _search_references_tool(args: Dict[str, Any], context) -> str:
    return search_references(
        args.get("query", ""),
        context.folder_context,
        context_lines=args.get("context_lines", 3),
    )


def retrieve_relevant_context(
    query: str,
    folder_context,
    top_k: int = 5,
    filters: dict | None = None,
) -> str:
    """Returns ranked context snippets from the semantic code index."""
    from mu.retrieval.index import RETRIEVAL_INDEX as _RETRIEVAL_INDEX

    if not str(query or "").strip():
        return json.dumps({"error": "query is required"})
    if not folder_context or not folder_context.folders:
        return json.dumps({"query": query, "count": 0, "results": []})
    _RETRIEVAL_INDEX.refresh_incremental(folder_context)
    payload = _RETRIEVAL_INDEX.retrieve(
        query,
        top_k=max(1, int(top_k or 5)),
        filters=filters or {},
    )
    payload["target_latency_ms"] = 2000
    payload["latency_ok"] = bool(payload.get("latency_ms", 0) < 2000)
    payload["ok"] = True
    payload["message"] = (
        f"Retrieved {payload.get('count', 0)} snippet(s) for query '{query}'."
    )
    return json.dumps(payload, indent=2)


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
def _retrieve_relevant_context_tool(args: Dict[str, Any], context) -> str:
    return retrieve_relevant_context(
        args.get("query", ""),
        context.folder_context,
        top_k=args.get("top_k", 5),
        filters=args.get("filters", {}),
    )


# ---------------------------------------------------------------- listing


def list_dir(path: str, folder_context) -> str:
    """Returns a list of files and directories in the specified path."""
    target_path = path or "."
    if not _check_bounds(target_path, folder_context):
        logger.warning(f"list_dir: Access denied or path ignored: {target_path}")
        return f"Error: Access denied or path ignored. '{target_path}'"

    try:
        items = os.listdir(target_path)
        filtered_items = []
        for item in items:
            item_path = os.path.join(target_path, item)
            if folder_context and folder_context.is_ignored(item_path):
                continue
            filtered_items.append(item)
        return "\n".join(sorted(filtered_items))
    except Exception as e:
        logger.error(f"list_dir: Error listing {target_path}: {e}")
        return f"Error listing directory: {e}"


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
def _list_dir_tool(args: Dict[str, Any], context) -> str:
    return list_dir(args.get("path", ""), context.folder_context)
