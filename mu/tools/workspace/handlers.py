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
from mu.tools._bounds import (
    check_bounds as _check_bounds,
    default_working_directory as _default_working_directory,
)
from mu.tools.capabilities import (
    normalize_session_type,
    session_type_from_context,
)
from mu.tools._scrub import scrub_and_annotate as _scrub_and_annotate
from mu.security.secret_paths import is_denied_path, redact_secrets
from utils.logger import logger




def _runtime_root(folder_context, session_type: str, requested: str | None = None) -> str:
    """Resolve a search/list root for the active runtime."""
    if requested:
        return os.path.realpath(os.path.abspath(os.path.expanduser(str(requested))))
    if normalize_session_type(session_type) == "container":
        return os.path.realpath(_default_working_directory(folder_context, session_type))
    if folder_context and folder_context.folders:
        return os.path.realpath(str(folder_context.folders[0]))
    return os.path.realpath(os.getcwd())


def _iter_runtime_files(root: str, folder_context, session_type: str):
    """Yield readable non-secret files below ``root``.

    Container traversal ignores workspace and gitignore boundaries.  Pseudo
    filesystems are skipped only to avoid blocking reads from kernel/device
    nodes; callers may still inspect individual non-secret paths directly.
    """
    unrestricted = normalize_session_type(session_type) == "container"
    virtual_roots = {"/proc", "/sys", "/dev", "/run/secrets"}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        kept_dirs = []
        for dirname in dirnames:
            candidate = os.path.join(dirpath, dirname)
            real = os.path.realpath(candidate)
            if any(real == item or real.startswith(item + os.sep) for item in virtual_roots):
                continue
            denied, _ = is_denied_path(real)
            if denied:
                continue
            if not unrestricted and folder_context and folder_context.is_ignored(real):
                continue
            kept_dirs.append(dirname)
        dirnames[:] = kept_dirs
        for filename in filenames:
            candidate = os.path.join(dirpath, filename)
            if not _check_bounds(candidate, folder_context, session_type=session_type):
                continue
            if not unrestricted and folder_context and folder_context.is_ignored(candidate):
                continue
            try:
                if not os.path.isfile(candidate) or os.path.getsize(candidate) > 8 * 1024 * 1024:
                    continue
            except OSError:
                continue
            yield candidate


class _RuntimeIndexContext:
    """Minimal FolderContext interface for indexing an arbitrary container root."""

    def __init__(self, root: str):
        self.folders = [root]

    def is_ignored(self, path: str) -> bool:
        real = os.path.realpath(path)
        if any(
            real == item or real.startswith(item + os.sep)
            for item in ("/proc", "/sys", "/dev", "/run/secrets")
        ):
            return True
        denied, _ = is_denied_path(real)
        return denied


# ---------------------------------------------------------------- workspace shape


def get_workspace_details(folder_context, *, session_type: str = "workspace") -> str:
    """Describe the active filesystem context."""
    if normalize_session_type(session_type) == "container":
        cwd = os.getcwd()
        root_items = []
        try:
            root_items = sorted(os.listdir("/"))
        except OSError:
            pass
        mounts = list(getattr(folder_context, "folders", None) or [])
        lines = [
            "Container filesystem access: unrestricted except secret paths",
            f"Current directory: {cwd}",
            "Configured project/mount roots:",
            *(f" - {item}" for item in mounts),
            "Top-level filesystem entries:",
            *(f" - /{item}" for item in root_items),
        ]
        return "\n".join(lines)
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
        "Describes the active filesystem. In container sessions this includes "
        "the complete non-secret container filesystem, not only mounted projects."
    ),
    parameters={"type": "object", "properties": {}},
    requires_approval=False,
    execution_kind="read",
    preview_policy="none",
    result_mode="structured+collated",
    summary_builder="parse_workspace_details",
)
def _get_workspace_details_tool(args: Dict[str, Any], context) -> str:
    return get_workspace_details(
        context.folder_context, session_type=session_type_from_context(context)
    )


# ---------------------------------------------------------------- file read


def read_file(filename: str, folder_context, *, session_type: str = "workspace") -> str:
    """Returns the whole file contents."""
    if not _check_bounds(filename, folder_context, session_type=session_type):
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
    return read_file(
        args.get("filename", ""),
        context.folder_context,
        session_type=session_type_from_context(context),
    )


def get_chunk(
    filename: str,
    start_line: int,
    end_line: int,
    folder_context,
    *,
    session_type: str = "workspace",
) -> str:
    """Returns a string of the file contents between the start and end line numbers."""
    if not _check_bounds(filename, folder_context, session_type=session_type):
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
        session_type=session_type_from_context(context),
    )


# ---------------------------------------------------------------- search


def search_for_string(
    search_string: str,
    folder_context,
    *,
    path: str | None = None,
    session_type: str = "workspace",
) -> str:
    """Return files and line numbers containing an exact string."""
    search_string = html.unescape(search_string)
    unrestricted = normalize_session_type(session_type) == "container"
    if not unrestricted and (not folder_context or not folder_context.folders):
        return "No workspace attached to search."

    if unrestricted:
        root = _runtime_root(folder_context, session_type, path)
        if not _check_bounds(root, folder_context, session_type=session_type):
            return f"Error: Access denied for search root '{root}'"
        candidates = _iter_runtime_files(root, folder_context, session_type)
    else:
        folder_context.sync_with_filesystem()
        candidates = folder_context.get_file_list()

    results = []
    for filepath in candidates:
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as handle:
                for line_num, line in enumerate(handle, 1):
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
            },
            "path": {
                "type": "string",
                "description": (
                    "Optional search root. Container sessions may search any "
                    "non-secret directory in the container."
                ),
            },
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
    return search_for_string(
        args.get("string", ""),
        context.folder_context,
        path=args.get("path"),
        session_type=session_type_from_context(context),
    )


def search_references(
    query: str,
    folder_context,
    context_lines: int = 3,
    *,
    path: str | None = None,
    session_type: str = "workspace",
) -> str:
    """Search runtime files for a string and return surrounding context."""
    unrestricted = normalize_session_type(session_type) == "container"
    if not unrestricted and (not folder_context or not folder_context.folders):
        return json.dumps({"error": "No workspace attached to search."})
    if not str(query or "").strip():
        return json.dumps({"error": "query is required"})

    context_lines = 3 if context_lines is None else max(0, int(context_lines))
    if unrestricted:
        root = _runtime_root(folder_context, session_type, path)
        if not _check_bounds(root, folder_context, session_type=session_type):
            return json.dumps({"error": f"Access denied for search root: {root}"})
        candidate_files = _iter_runtime_files(root, folder_context, session_type)
    else:
        files = set(folder_context.get_file_list() or [])
        for root in getattr(folder_context, "folders", []) or []:
            if not root or not os.path.isdir(root):
                continue
            files.update(_iter_runtime_files(root, folder_context, session_type))
        candidate_files = sorted(files)

    results = []
    for filepath in candidate_files:
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as handle:
                lines = handle.readlines()
        except Exception:
            continue
        for index, line in enumerate(lines):
            if query not in line:
                continue
            begin = max(0, index - context_lines)
            finish = min(len(lines), index + context_lines + 1)
            snippet = "".join(lines[begin:finish])
            scrubbed_snippet, _ = redact_secrets(snippet.rstrip())
            results.append({
                "filepath": filepath,
                "line_number": index + 1,
                "context_snippet": scrubbed_snippet,
            })

    return json.dumps(
        {"query": query, "count": len(results), "results": results}, indent=2
    )


@tool(
    name="search_references",
    description=(
        "Searches runtime files for references to a query string. Container "
        "sessions may choose any non-secret search root. Returns matches with "
        "filepath, line_number, "
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
            "path": {
                "type": "string",
                "description": (
                    "Optional search root. Container sessions may search any "
                    "non-secret directory in the container."
                ),
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
        path=args.get("path"),
        session_type=session_type_from_context(context),
    )


def retrieve_relevant_context(
    query: str,
    folder_context,
    top_k: int = 5,
    filters: dict | None = None,
    *,
    path: str | None = None,
    session_type: str = "workspace",
) -> str:
    """Return ranked snippets from a workspace or arbitrary container root."""
    from mu.retrieval.index import RETRIEVAL_INDEX as _RETRIEVAL_INDEX

    if not str(query or "").strip():
        return json.dumps({"error": "query is required"})

    unrestricted = normalize_session_type(session_type) == "container"
    index_context = folder_context
    effective_filters = dict(filters or {})
    if unrestricted:
        root = _runtime_root(folder_context, session_type, path)
        if not _check_bounds(root, folder_context, session_type=session_type):
            return json.dumps({"error": f"Access denied for index root: {root}"})
        index_context = _RuntimeIndexContext(root)
        effective_filters["_workspace_root"] = root
    elif not folder_context or not folder_context.folders:
        return json.dumps({"query": query, "count": 0, "results": []})

    _RETRIEVAL_INDEX.refresh_incremental(index_context)
    payload = _RETRIEVAL_INDEX.retrieve(
        query,
        top_k=max(1, int(top_k or 5)),
        filters=effective_filters,
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
            "path": {
                "type": "string",
                "description": (
                    "Optional index root. Container sessions may index any "
                    "non-secret directory inside the container."
                ),
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
        path=args.get("path"),
        session_type=session_type_from_context(context),
    )


# ---------------------------------------------------------------- listing


def list_dir(path: str, folder_context, *, session_type: str = "workspace") -> str:
    """Returns a list of files and directories in the specified path."""
    target_path = path or "."
    if not _check_bounds(target_path, folder_context, session_type=session_type):
        logger.warning(f"list_dir: Access denied or path ignored: {target_path}")
        return f"Error: Access denied or path ignored. '{target_path}'"

    try:
        items = os.listdir(target_path)
        filtered_items = []
        for item in items:
            item_path = os.path.join(target_path, item)
            denied, _ = is_denied_path(item_path)
            if denied:
                continue
            if (
                normalize_session_type(session_type) != "container"
                and folder_context
                and folder_context.is_ignored(item_path)
            ):
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
    return list_dir(
        args.get("path", ""),
        context.folder_context,
        session_type=session_type_from_context(context),
    )
