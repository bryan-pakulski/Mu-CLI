import json
import os
import time
import datetime
import difflib
import re
import bisect
import html
from dataclasses import dataclass, asdict, field
from urllib.parse import quote
from typing import Any, Callable
from providers.base import ToolDefinition
from utils.logger import logger
from utils.citation_manager import register_source, SourceType
from core.secret_paths import redact_secrets
from core.feature_mode import (
    create_feature_shell,
    create_feature_phases,
    create_feature_task,
    create_task_review_record,
    review_all_completed_tasks as create_reviews_for_completed_tasks,
    create_diff_proposal,
    decide_diff_proposal,
    archive_task as archive_feature_task,
    feature_execution_snapshot,
    create_feature_plan,
    load_feature_plan,
    save_feature_plan,
    transition_task_status,
    update_task_status,
    update_task_content,
    refresh_and_persist_feature_plan,
    summarize_feature_plan,
    update_feature_plan_metadata,
    _workspace_root,
)
from core.retrieval import SemanticCodeIndex




def _register_source_and_get_citation(
    title: str,
    url: str,
    source_type: SourceType,
    authors: list[str] | None = None,
    date: str | None = None,
) -> str:
    """
    Register a source with the citation manager and return a citation ID.
    
    Args:
        title: The title of the source
        url: The URL of the source
        source_type: The type of source (SourceType enum)
        authors: Optional list of authors
        date: Optional publication date
    
    Returns:
        A citation ID in the format [^n]
    """
    try:
        citation_id = register_source(title=title, url=url, source_type=source_type, authors=authors, date=date)
        return citation_id
    except Exception as e:
        logger.warning(f"Failed to register source: {e}")
        return ""


@dataclass(frozen=True)
class ToolDescriptor:
    definition: ToolDefinition
    execution_kind: str
    preview_policy: str
    server_policy: str
    result_mode: str
    handler_key: str
    error_mode: str = "text_error"
    summary_builder: str | None = None


@dataclass(frozen=True)
class ToolExecutionContext:
    folder_context: Any
    ui: Any = None
    variables: dict | None = None
    invocation_source: str = "session"
    session: Any = None


def _build_descriptor(
    definition: ToolDefinition,
    *,
    execution_kind: str,
    preview_policy: str,
    server_policy: str,
    result_mode: str,
    handler_key: str,
    error_mode: str = "text_error",
    summary_builder: str | None = None,
) -> ToolDescriptor:
    return ToolDescriptor(
        definition=definition,
        execution_kind=execution_kind,
        preview_policy=preview_policy,
        server_policy=server_policy,
        result_mode=result_mode,
        handler_key=handler_key,
        error_mode=error_mode,
        summary_builder=summary_builder,
    )


def build_tool_context(
    folder_context,
    ui=None,
    variables: dict | None = None,
    *,
    invocation_source: str = "session",
    session: Any = None,
) -> ToolExecutionContext:
    return ToolExecutionContext(
        folder_context=folder_context,
        ui=ui,
        variables=variables,
        invocation_source=invocation_source,
        session=session,
    )


# --- Tool Definitions (Schemas) ---

TOOLS = [
    # NOTE: get_workspace_details / read_file / search_for_string /
    # search_references / retrieve_relevant_context / get_chunk / list_dir
    # live in `mu/tools/workspace/handlers.py` as `@tool`-decorated
    # functions. The underlying implementations remain in this module
    # (see `read_file`, `search_for_string`, etc. below) for other call
    # sites; the descriptors and dispatcher entries moved out.
    # write_file / apply_diff / search_and_replace_file live in
    # `mu/tools/file/handlers.py` as `@tool`-decorated functions.
    # batch_job + flush live in `mu/tools/batch/handlers.py`.
    # bash and the bash_{background,status,logs,kill,list} family live in
    # `mu/tools/shell/handlers.py` as `@tool`-decorated functions.
    # ----------------------------------------------- security audit engine
    # The 9 security tools (create_security_report, add_security_finding,
    # attach_security_proof, verify_security_proof, attach_remediation_patch,
    # verify_remediation, approve_security_finding, refute_security_finding,
    # get_security_state) register via `mu/tools/security/handlers.py`.
    # Bodies still live in this file as `_handle_*` until
    # `core/security_mode.py` itself relocates (Phase 6 namespace rename).
    # (block above ends — every security ToolDefinition is now in
    # mu/tools/security/handlers.py.)
    # Research tools (url_grounding, web_search, arxiv_search,
    # doi_resolve, reddit_search, stackoverflow_search,
    # hackernews_search, read_document) live in
    # `mu/tools/research/handlers.py` as `@tool`-decorated functions.
    # `flush` lives in mu/tools/batch/handlers.py.
    # NOTE: save_memory / search_memory / list_memory / save_scratchpad /
    # search_scratchpad / list_scratchpad / clear_scratchpad live in
    # `mu/tools/memory/handlers.py` as `@tool`-decorated functions.
    # Feature-mode tools (create_feature, create_phases, create_task,
    # get_execution_state, block_task, resume_task, review_*,
    # propose/decide_task_diff, archive_task,
    # create/update/approve_feature_task, get_current_task, get_tasks,
    # update_task_status, raise_blocker) register via the `@tool`
    # decorator in `mu/tools/feature/handlers.py`. Bodies still live in
    # this file as `_handle_*` until `core/feature_mode.py` itself
    # relocates (Phase 6 namespace rename).
    # (block above ends here — every feature-mode ToolDefinition is now
    # registered through `mu/tools/feature/handlers.py`.)
    # invoke_skill lives in `mu/tools/skill/handlers.py` as a
    # `@tool`-decorated function.
]
_COLLATED_TOOL_NAMES = {
    "get_workspace_details",
    "read_file",
    "search_for_string",
    "search_references",
    "retrieve_relevant_context",
    "get_chunk",
    "list_dir",
    "url_grounding",
    "web_search",
    "arxiv_search",
    "doi_resolve",
    "reddit_search",
    "stackoverflow_search",
    "hackernews_search",
    "read_document",
    "get_tasks",
    "get_current_task",
}


def _default_result_mode(tool_name: str) -> str:
    return "structured+collated" if tool_name in _COLLATED_TOOL_NAMES else "structured"


def _default_server_policy(tool_name: str) -> str:
    if tool_name in {
        "flush",
    }:
        return "session_only"
    return "allowed"


TOOL_DESCRIPTOR_OVERRIDES = {
    # Workspace inspector metadata now lives with the `@tool` registrations
    # in `mu/tools/workspace/handlers.py`.
    # write_file / apply_diff / search_and_replace_file metadata lives
    # with the `@tool` registrations in mu/tools/file/handlers.py.
    # batch_job + flush metadata moved to mu/tools/batch/handlers.py.
    # bash + bash_{background,status,logs,kill,list} metadata lives with
    # the `@tool` registrations in mu/tools/shell/handlers.py.
    # Security-mode tool metadata moved to mu/tools/security/handlers.py.
    # url_grounding / web_search / arxiv_search / read_document
    # metadata moved to mu/tools/research/handlers.py.
    # Memory + scratchpad tool metadata now lives with their handlers in
    # `mu/tools/memory/handlers.py` (see `@tool` decorator arguments).
    # Feature-mode tool metadata moved to mu/tools/feature/handlers.py.
    # invoke_skill metadata moved to mu/tools/skill/handlers.py.
}

TOOL_DESCRIPTORS = {
    tool.name: _build_descriptor(
        tool,
        execution_kind=TOOL_DESCRIPTOR_OVERRIDES.get(tool.name, {}).get(
            "execution_kind",
            "mutate" if tool.requires_approval else "read",
        ),
        preview_policy=TOOL_DESCRIPTOR_OVERRIDES.get(tool.name, {}).get(
            "preview_policy",
            "optional" if tool.requires_approval else "none",
        ),
        server_policy=TOOL_DESCRIPTOR_OVERRIDES.get(tool.name, {}).get(
            "server_policy",
            _default_server_policy(tool.name),
        ),
        result_mode=TOOL_DESCRIPTOR_OVERRIDES.get(tool.name, {}).get(
            "result_mode",
            _default_result_mode(tool.name),
        ),
        handler_key=TOOL_DESCRIPTOR_OVERRIDES.get(tool.name, {}).get(
            "handler_key",
            tool.name,
        ),
        error_mode=TOOL_DESCRIPTOR_OVERRIDES.get(tool.name, {}).get(
            "error_mode",
            "text_error",
        ),
        summary_builder=TOOL_DESCRIPTOR_OVERRIDES.get(tool.name, {}).get(
            "summary_builder"
        ),
    )
    for tool in TOOLS
}


def get_tool_definition(tool_name: str) -> ToolDefinition | None:
    descriptor = get_tool_descriptor(tool_name)
    return descriptor.definition if descriptor else None


def get_tool_descriptor(tool_name: str) -> ToolDescriptor | None:
    descriptor = TOOL_DESCRIPTORS.get(tool_name)
    if descriptor is not None:
        return descriptor
    # Tools migrated to `mu/tools/<group>/handlers.py` register through the
    # `@tool` decorator and no longer appear in the legacy TOOL_DESCRIPTORS
    # dict. Fall through to the unified registry so callers (approval
    # plan, /tool list, harness layer tests, etc.) keep working.
    try:
        from mu import tools as _mut
    except ImportError:
        return None
    return _mut.get(tool_name)


def list_tool_descriptors() -> list[ToolDescriptor]:
    return [
        TOOL_DESCRIPTORS[tool.name] for tool in TOOLS if tool.name in TOOL_DESCRIPTORS
    ]


COLLATED_TOOLS = [
    *sorted(_COLLATED_TOOL_NAMES),
]


# `_check_bounds` body moved to `mu/tools/_bounds.py:check_bounds`.
# The alias is bound at the bottom of this file (after the bridge
# `import mu.tools` triggers the full registry to populate) so that the
# legacy name keeps working for in-file callers and external tests.
def _check_bounds(filename, folder_context):
    from mu.tools._bounds import check_bounds as _impl
    global _check_bounds
    _check_bounds = _impl
    return _impl(filename, folder_context)


# `_scrub_and_annotate` body moved to `mu/tools/_scrub.py:scrub_and_annotate`.
# Lazy alias keeps the legacy underscored name working for in-file callers
# (read_file, bash_command, get_chunk, search_for_string, search_references).
def _scrub_and_annotate(text):
    from mu.tools._scrub import scrub_and_annotate as _impl
    global _scrub_and_annotate
    _scrub_and_annotate = _impl
    return _impl(text)


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


def read_file(filename: str, folder_context) -> str:
    """Returns the whole file contents."""
    if not _check_bounds(filename, folder_context):
        logger.warning(f"read_file: Access denied or file ignored: {filename}")
        return f"Error: Access denied or file ignored. '{filename}' is outside boundaries or in ignore list."
    try:
        with open(filename, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        return f"Error: File '{filename}' not found. Try using search_for_string to locate it."
    except UnicodeDecodeError:
        return f"Error: '{filename}' appears to be a binary file or has an unsupported encoding."
    except Exception as e:
        logger.error(f"read_file: Error reading {filename}: {e}")
        return f"Error reading file: {e}"
    return _scrub_and_annotate(content)


def search_for_string(search_string: str, folder_context) -> str:
    """Returns a list of all files that contain the string as well as the line number."""
    # Unescape HTML entities — provider SDKs (especially Gemini) escape angle brackets
    # in tool call args, so '&lt;/style&gt;' becomes '</style>' for correct matching.
    search_string = html.unescape(search_string)

    if not folder_context:
        return "No workspace attached to search."

    # Sync with filesystem to pick up externally added/removed files
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


_RETRIEVAL_INDEX = SemanticCodeIndex()


def retrieve_relevant_context(
    query: str,
    folder_context,
    top_k: int = 5,
    filters: dict | None = None,
) -> str:
    """Returns ranked context snippets from the semantic code index."""
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


def search_references(query: str, folder_context, context_lines: int = 3) -> str:
    """Searches all workspace files for the query string and returns matches with context."""
    if not folder_context:
        return json.dumps({"error": "No workspace attached to search."})
    if not str(query or "").strip():
        return json.dumps({"error": "query is required"})

    context_lines = 3 if context_lines is None else max(0, int(context_lines))
    results = []
    candidate_files = set(folder_context.get_file_list() or [])
    # Include on-disk files created after initial folder attachment so searches
    # do not miss freshly generated files during tests/agent runs.
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
                results.append({
                    "filepath": filepath,
                    "line_number": i + 1,
                    "context_snippet": scrubbed_snippet,
                })

    if not results:
        return json.dumps({"query": query, "count": 0, "results": []})
    return json.dumps({"query": query, "count": len(results), "results": results}, indent=2)


def get_chunk(filename: str, start_line: int, end_line: int, folder_context) -> str:
    """Returns a string of the file contents between the start and end line numbers."""
    if not _check_bounds(filename, folder_context):
        logger.warning(f"get_chunk: Access denied or file ignored: {filename}")
        return f"Error: Access denied or file ignored. '{filename}' is outside boundaries or in ignore list."
    try:
        with open(filename, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # 1-indexed to 0-indexed
        start_idx = max(0, start_line - 1)
        end_idx = min(len(lines), end_line)

        chunk = lines[start_idx:end_idx]
        return _scrub_and_annotate("".join(chunk))
    except FileNotFoundError:
        return f"Error: File '{filename}' not found. Try using search_for_string to locate it."
    except UnicodeDecodeError:
        return f"Error: '{filename}' appears to be a binary file or has an unsupported encoding."
    except Exception as e:
        logger.error(f"get_chunk: Error reading {filename}: {e}")
        return f"Error reading file chunk: {e}"


def list_dir(path: str, folder_context) -> str:
    """Returns a list of files and directories in the specified path."""
    target_path = path or "."
    # We check bounds for the directory itself
    if not _check_bounds(target_path, folder_context):
        logger.warning(f"list_dir: Access denied or path ignored: {target_path}")
        return f"Error: Access denied or path ignored. '{target_path}'"

    try:
        items = os.listdir(target_path)
        # Filter individual items based on ignore patterns
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


def write_file(filename: str, content: str, folder_context) -> str:
    """Creates or overwrites a file with the provided content."""
    if not _check_bounds(filename, folder_context):
        logger.warning(f"write_file: Access denied or path ignored: {filename}")
        return f"Error: Access denied or path ignored. '{filename}'"

    try:
        dirname = os.path.dirname(filename)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
        with open(filename, "w", encoding="utf-8") as f:
            f.write(content)
        # Track file in workspace so search_for_string can find it
        if folder_context and hasattr(folder_context, 'track_file'):
            folder_context.track_file(filename)
        return f"Successfully wrote to {filename}"
    except Exception as e:
        logger.error(f"write_file: Error writing to {filename}: {e}")
        return f"Error writing file: {e}"


def _sanitize_diff(diff: str, filename: str) -> str:
    """Cleans up common LLM diff issues."""
    lines = diff.splitlines()
    if not lines:
        return diff

    # 1. Strip LLM chatter/markers (e.g. *** Begin Patch, *** Update File)
    # Also strip code block backticks if the model included them inside the string
    cleaned_lines = []
    meaningful_found = False
    for line in lines:
        trimmed = line.strip()
        if trimmed.startswith("***") or trimmed.endswith("***"):
            continue
        # Strip markdown code block markers
        if trimmed.startswith("```"):
            continue
        cleaned_lines.append(line)
    lines = cleaned_lines

    # 2. Strip preamble text before the first header or hunk
    first_meaningful_line = -1
    for i, line in enumerate(lines):
        if line.startswith("---") or line.startswith("+++") or line.startswith("@@"):
            first_meaningful_line = i
            break
    if first_meaningful_line != -1:
        lines = lines[first_meaningful_line:]

    sanitized = []
    hunk_lines = []

    def flush_hunk(hlines):
        if not hlines:
            return
        if hlines[0].startswith("@@"):
            header = hlines[0].strip()
            # Try to match standard @@ -start,len +start,len @@
            match = re.match(r"^@@ -(\d+),?\d* \+(\d+),?\d* @@(.*)$", header)

            start_old, start_new = "1", "1"
            tail = ""
            if match:
                start_old, start_new, tail = match.groups()

            count_old = 0
            count_new = 0
            content_lines = []
            for hl in hlines[1:]:
                if not hl:
                    continue

                # If model is lazy and doesn't provide prefixes, try to guess
                if hl.startswith("-"):
                    count_old += 1
                    content_lines.append(hl)
                elif hl.startswith("+"):
                    count_new += 1
                    content_lines.append(hl)
                elif hl.startswith(" ") or hl.startswith("\t"):
                    count_old += 1
                    count_new += 1
                    content_lines.append(hl)
                else:
                    # If it's not a +/- line and we are in a hunk, assume context
                    trimmed_hl = hl.strip()
                    if trimmed_hl:
                        count_old += 1
                        count_new += 1
                        content_lines.append(" " + hl)
                    else:
                        # Empty line, usually context
                        count_old += 1
                        count_new += 1
                        content_lines.append(" ")

            if not content_lines and header == "@@":
                return

            new_header = (
                f"@@ -{start_old},{count_old} +{start_new},{count_new} @@{tail}"
            )
            sanitized.append(new_header)
            sanitized.extend(content_lines)
            return
        sanitized.extend(hlines)

    # Ensure file headers are at the TOP (after stripping chatter)
    # If the model included headers, we use them, but if it didn't we add them.
    # But wait, if they are already in 'lines', we shouldn't duplicate them.
    # The current logic will add them if they are not in the first 3 lines.

    if not any(l.startswith("--- ") for l in lines[:3]):
        sanitized.append(f"--- {filename}")
        sanitized.append(f"+++ {filename}")

    for line in lines:
        if line.startswith("---") or line.startswith("+++"):
            flush_hunk(hunk_lines)
            hunk_lines = []
            sanitized.append(line)
        elif line.startswith("@@"):
            flush_hunk(hunk_lines)
            hunk_lines = [line]
        elif line.startswith("+") or line.startswith("-") or line.startswith(" "):
            if hunk_lines:
                hunk_lines.append(line)
            else:
                # Line without prefix but we haven't found a hunk yet.
                # Skip it or treat it as context? Treat as context for now.
                trimmed = line.strip()
                if trimmed:
                    sanitized.append(" " + line)
        else:
            # Likely context missing its space OR trailing garbage
            if hunk_lines:
                # If we are in a hunk, assume it's context
                hunk_lines.append(" " + line)
            else:
                # Outside hunk, probably preamble/postamble chatter
                pass

    flush_hunk(hunk_lines)

    return "\n".join(sanitized) + "\n"


def apply_diff(filename: str, diff: str, folder_context) -> str:
    """Applies a unified diff to a file."""
    if not _check_bounds(filename, folder_context):
        logger.warning(f"apply_diff: Access denied or path ignored: {filename}")
        return f"Error: Access denied or path ignored. '{filename}'"

    try:
        if not os.path.exists(filename):
            return f"Error: File '{filename}' does not exist. Cannot apply diff."

        # Pre-sanitize the diff
        diff = _sanitize_diff(diff, filename)

        import tempfile
        import subprocess

        with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp_diff:
            tmp_diff.write(diff)
            tmp_diff_path = tmp_diff.name

        try:
            # Try using system 'patch' command first as it is robust.
            # -u: unified diff
            # -l: ignore whitespace in context
            # -F3: set fuzz factor to 3 lines
            result = subprocess.run(
                ["patch", "-u", "-l", "-F3", filename, "-i", tmp_diff_path],
                capture_output=True,
                text=True,
            )
            os.unlink(tmp_diff_path)

            if result.returncode == 0:
                # Track file in workspace so search_for_string can find it
                if folder_context and hasattr(folder_context, 'track_file'):
                    folder_context.track_file(filename)
                return f"Successfully applied diff to {filename}"
            else:
                logger.error(
                    f"apply_diff: Patch error for {filename}: {result.stderr or result.stdout}"
                )
                return (
                    f"Error applying diff via 'patch': {result.stderr or result.stdout}"
                )
        except FileNotFoundError:
            os.unlink(tmp_diff_path)
            logger.error("apply_diff: 'patch' utility not found.")
            return "Error: 'patch' utility not found on system. Please install it to apply diffs."

    except Exception as e:
        logger.error(f"apply_diff: Exception for {filename}: {e}")
        return f"Error applying diff: {e}"


def search_and_replace_file(
    filename: str,
    search: str,
    replace: str,
    expected_count: int = None,
    normalize_whitespace: bool = False,
    dry_run: bool = False,
    folder_context=None
) -> str:
    """Search and replace text in a file using exact string matching.
    
    This is the PRIMARY method for making targeted code changes. Use apply_diff
    (unified diff) only as a fallback for complex multi-file changes.
    
    Args:
        filename: Path to the file to modify
        search: The exact text to search for (must match exactly)
        replace: The text to replace the search string with
        expected_count: Optional expected number of matches. If provided and count
                       differs, the operation fails (safety check for disambiguation)
        normalize_whitespace: If True, normalize whitespace in search pattern
                             (collapse multiple spaces, trim leading/trailing)
        dry_run: If True, return preview of changes without modifying the file
        folder_context: Workspace folder context for path validation
    
    Returns:
        JSON string with results including:
        - success: bool
        - matches_found: int
        - match_locations: list of {line, column, context}
        - modified: bool (whether file was actually modified)
        - preview: str (unified diff preview of changes)
        - error: str (if error occurred)
    """
    import json
    
    # Validate path
    if not _check_bounds(filename, folder_context):
        logger.warning(f"search_and_replace_file: Access denied or path ignored: {filename}")
        return json.dumps({"success": False, "error": f"Access denied or path ignored. '{filename}'"})
    
    # Validate search string
    if not search:
        return json.dumps({"success": False, "error": "Search string cannot be empty"})
    
    # Check file exists
    if not os.path.exists(filename):
        return json.dumps({"success": False, "error": f"File '{filename}' does not exist"})
    
    # Check if binary file
    try:
        with open(filename, "rb") as f:
            chunk = f.read(8192)
            if b'\x00' in chunk:
                return json.dumps({"success": False, "error": f"File '{filename}' appears to be binary. Search and replace not supported."})
    except Exception as e:
        return json.dumps({"success": False, "error": f"Error reading file: {str(e)}"})
    
    # Read file content
    try:
        with open(filename, "r", encoding="utf-8") as f:
            content = f.read()
    except UnicodeDecodeError:
        return json.dumps({"success": False, "error": f"File '{filename}' is not UTF-8 encoded"})
    except Exception as e:
        return json.dumps({"success": False, "error": f"Error reading file: {str(e)}"})
    
    # Find all matches with locations
    matches = []
    lines = content.split('\n')

    # Build line start positions for column calculation
    line_starts = [0]
    for line in lines:
        line_starts.append(line_starts[-1] + len(line) + 1)  # +1 for newline

    def _line_col_for_offset(offset: int) -> tuple[int, int]:
        line_index = bisect.bisect_right(line_starts, offset) - 1
        line_num = max(1, line_index + 1)
        column = offset - line_starts[line_index] + 1
        return line_num, column

    if normalize_whitespace:
        tokens = [token for token in re.split(r"(\s+)", search) if token != ""]
        pattern_parts = []
        for token in tokens:
            if token.isspace():
                pattern_parts.append(r"\s+")
            else:
                pattern_parts.append(re.escape(token))
        regex = re.compile("".join(pattern_parts))
        regex_matches = list(regex.finditer(content))
        located_matches = [(m.start(), m.end()) for m in regex_matches]
    else:
        located_matches = []
        start_pos = 0
        while True:
            pos = content.find(search, start_pos)
            if pos == -1:
                break
            located_matches.append((pos, pos + len(search)))
            start_pos = pos + 1

    for start_offset, _ in located_matches:
        line_num, column = _line_col_for_offset(start_offset)

        # Get context (2-3 lines around the match for disambiguation)
        if 1 <= line_num <= len(lines):
            context_parts = []
            # Add 1 line before if available
            if line_num > 1:
                before_line = lines[line_num - 2].strip()
                if len(before_line) > 80:
                    before_line = before_line[:77] + "..."
                context_parts.append(f"  {before_line}")
            # Add the match line
            match_line = lines[line_num - 1].strip()
            if len(match_line) > 80:
                match_line = match_line[:77] + "..."
            context_parts.append(f"> {match_line}")
            # Add 1 line after if available
            if line_num < len(lines):
                after_line = lines[line_num].strip()
                if len(after_line) > 80:
                    after_line = after_line[:77] + "..."
                context_parts.append(f"  {after_line}")
            context_line = "\n".join(context_parts)
        else:
            context_line = ""

        matches.append({
            "line": line_num,
            "column": column,
            "context": context_line
        })

    # Check match count
    if len(matches) == 0:
        return json.dumps({
            "success": False,
            "matches_found": 0,
            "error": f"No matches found for search string. Make sure the search string matches exactly, including whitespace.",
            "search_length": len(search)
        })
    
    # Validate expected count
    if expected_count is not None and len(matches) != expected_count:
        return json.dumps({
            "success": False,
            "matches_found": len(matches),
            "expected_count": expected_count,
            "match_locations": matches,
            "error": f"Expected {expected_count} matches but found {len(matches)}. Use expected_count to disambiguate or provide more specific search string."
        })
    
    # Perform replacement
    if normalize_whitespace:
        if not located_matches:
            new_content = content
        else:
            rebuilt = []
            cursor = 0
            for start_offset, end_offset in located_matches:
                rebuilt.append(content[cursor:start_offset])
                rebuilt.append(replace)
                cursor = end_offset
            rebuilt.append(content[cursor:])
            new_content = "".join(rebuilt)
    else:
        new_content = content.replace(search, replace)
    
    # Generate preview (unified diff style)
    import difflib
    diff = difflib.unified_diff(
        content.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        lineterm=""
    )
    preview = "".join(diff)
    
    diff_output = f"DIFF:\n{preview}" if preview else "DIFF:\n(no changes)"

    # If dry_run, return preview without modifying
    if dry_run:
        return json.dumps({
            "success": True,
            "matches_found": len(matches),
            "match_locations": matches,
            "modified": False,
            "preview": preview,
            "diff": diff_output,
            "dry_run": True
        })
    
    # Write file
    try:
        with open(filename, "w", encoding="utf-8") as f:
            f.write(new_content)
        # Track file in workspace so search_for_string can find it
        if folder_context and hasattr(folder_context, 'track_file'):
            folder_context.track_file(filename)
        return json.dumps({
            "success": True,
            "matches_found": len(matches),
            "match_locations": matches,
            "modified": True,
            "preview": preview,
            "diff": diff_output
        })
    except Exception as e:
        logger.error(f"search_and_replace_file: Error writing {filename}: {e}")
        return json.dumps({"success": False, "error": f"Error writing file: {str(e)}"})


def bash_command(
    command: str,
    folder_context,
    *,
    cwd: str | None = None,
    timeout_seconds: int = 120,
    max_output_chars: int = 12000,
) -> str:
    """Executes a raw bash command in the workspace."""
    command = str(command or "").strip()
    if not command:
        return "Error: command is required."

    if not folder_context or not folder_context.folders:
        return "Error: No workspace attached."

    import subprocess

    workdir = str(cwd or folder_context.folders[0]).strip()
    if not _check_bounds(workdir, folder_context):
        logger.warning(f"bash_command: Access denied or path ignored: {workdir}")
        return f"Error: Access denied or path ignored. '{workdir}'"

    timeout_seconds = max(1, int(timeout_seconds or 120))
    max_output_chars = max(512, int(max_output_chars or 12000))

    try:
        process = subprocess.run(
            ["bash", "-lc", command],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            cwd=workdir,
        )
    except subprocess.TimeoutExpired as exc:
        partial = f"{exc.stdout or ''}\n{exc.stderr or ''}".strip()
        if len(partial) > max_output_chars:
            partial = partial[:max_output_chars]
        return (
            f"Error: Command timed out after {timeout_seconds} seconds.\n"
            f"{partial}".strip()
        )
    except Exception as exc:
        logger.error(f"bash_command: Error executing command {command!r}: {exc}")
        return f"Error executing bash command: {exc}"

    chunks = []
    if process.stdout:
        chunks.append(f"STDOUT:\n{process.stdout.rstrip()}")
    if process.stderr:
        chunks.append(f"STDERR:\n{process.stderr.rstrip()}")
    if not chunks:
        chunks.append("Command executed with no output.")
    chunks.append(f"Exit code: {process.returncode}")
    output = "\n\n".join(chunks)

    if len(output) > max_output_chars:
        output = output[:max_output_chars] + "\n\n...[TRUNCATED]..."
    return _scrub_and_annotate(output)


def url_grounding(url: str, folder_context) -> str:
    """Accesses a URL to gather additional context. Supports JavaScript-heavy websites."""
    try:
        from playwright.sync_api import sync_playwright
        from bs4 import BeautifulSoup

        with sync_playwright() as p:
            # Try to launch chromium. We use chromium as it's generally most compatible.
            # If chromium isn't installed (`playwright install chromium` never ran),
            # raise so the outer except block can fall through to the httpx fallback
            # — otherwise the function would short-circuit here and the user would
            # see a hard error even though static HTML fetch would work fine.
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as exc:
                logger.info(
                    "url_grounding: chromium launch failed (%s); falling back to httpx.",
                    exc,
                )
                raise

            page = browser.new_page()

            # Wait for network idle to ensure JS has rendered content
            page.goto(url, wait_until="networkidle")

            content = page.content()
            browser.close()

            soup = BeautifulSoup(content, "html.parser")

            # Remove script and style elements
            for script_or_style in soup(["script", "style"]):
                script_or_style.decompose()

            # Get text
            text = soup.get_text(separator="\n")

            # Clean up whitespace
            lines = (line.strip() for line in text.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            text = "\n".join(chunk for chunk in chunks if chunk)

            # Register source and generate citation
            citation_id = register_source(url=url, title=url, source_type=SourceType.WEB)
            return f"{text}\n\n---\nCitation: [^{citation_id}]"
        
    except (ImportError, Exception):
        # Fallback to a simpler method if playwright is not installed or fails
        try:
            import httpx
            from bs4 import BeautifulSoup

            response = httpx.get(url, follow_redirects=True, timeout=30.0)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            for script_or_style in soup(["script", "style"]):
                script_or_style.decompose()

            text = soup.get_text(separator="\n")
            lines = (line.strip() for line in text.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            text = "\n".join(chunk for chunk in chunks if chunk)

            # Register source and generate citation
            citation_id = register_source(url=url, title=url, source_type=SourceType.WEB)
            return f"(Note: Playwright not installed or failed, JS-heavy content might be missing)\n\n{text}\n\n---\nCitation: [^{citation_id}]"
        except Exception as e:
            return f"Error accessing URL: {e}"


def web_search(query: str, engine: str = "duckduckgo", num_results: int = 10, folder_context=None) -> str:
    """Search the web using DuckDuckGo or Google Custom Search API.
    
    Args:
        query: The search query string
        engine: Search engine to use - 'duckduckgo' (default) or 'google'
        num_results: Maximum number of results to return (default 10, max 50)
        folder_context: Workspace folder context (unused but required for tool signature)
    
    Returns:
        JSON string with search results including title, URL, snippet, and relevance score
    """
    import json
    
    # Cap results
    num_results = min(max(1, num_results), 50)
    
    if not query or not query.strip():
        return json.dumps({"error": "Query cannot be empty", "results": []})
    
    query = query.strip()
    
    def _duckduckgo_instantapi_fallback() -> list[dict]:
        """DuckDuckGo fallback that does not require third-party packages."""
        import urllib.parse
        import urllib.request

        fallback_results: list[dict] = []
        endpoint = (
            "https://api.duckduckgo.com/?"
            + urllib.parse.urlencode(
                {
                    "q": query,
                    "format": "json",
                    "no_html": "1",
                    "no_redirect": "1",
                }
            )
        )
        request = urllib.request.Request(
            endpoint,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8", errors="ignore"))

        def _append_result(title: str, url: str, snippet: str):
            if not url:
                return
            fallback_results.append(
                {
                    "title": title or url,
                    "url": url,
                    "snippet": snippet or "",
                    "relevance_score": max(0.1, 1.0 - (len(fallback_results) * 0.05)),
                    "citation_id": register_source(
                        title=title or url,
                        url=url,
                        source_type="web",
                    ),
                }
            )

        abstract_url = str(payload.get("AbstractURL", "") or "").strip()
        if abstract_url:
            _append_result(
                str(payload.get("Heading", "") or "").strip() or "DuckDuckGo Abstract",
                abstract_url,
                str(payload.get("AbstractText", "") or "").strip(),
            )

        def _consume_topics(topics):
            for topic in topics:
                if len(fallback_results) >= num_results:
                    return
                if not isinstance(topic, dict):
                    continue
                if isinstance(topic.get("Topics"), list):
                    _consume_topics(topic.get("Topics", []))
                    continue
                url = str(topic.get("FirstURL", "") or "").strip()
                text = str(topic.get("Text", "") or "").strip()
                if url:
                    title = text.split(" - ")[0].strip() if text else url
                    _append_result(title, url, text)

        _consume_topics(payload.get("RelatedTopics", []) if isinstance(payload.get("RelatedTopics"), list) else [])
        return fallback_results[:num_results]

    if engine.lower() == "duckduckgo":
        # Tiered DuckDuckGo strategy. Each tier feeds the same `results`
        # list so a partial hit from an early tier short-circuits the
        # later ones. Tiers, in order of result quality:
        #
        #   1. `ddgs` package — best quality, ranked results.
        #   2. HTML scrape of html.duckduckgo.com — works without any
        #      third-party search dep; ~10 results per query.
        #   3. InstantAnswer API (api.duckduckgo.com) — Wikipedia-class
        #      topics only, returns nothing for most real queries.
        #
        # The previous arrangement put ImportError from tier 1 directly
        # onto tier 3, skipping the strong HTML fallback. Now tier 1
        # missing or returning 0 results both flow into tier 2.
        results: list[dict] = []

        def _scrape_html_fallback() -> list[dict]:
            try:
                import httpx
                from bs4 import BeautifulSoup
            except ImportError:
                return []
            try:
                response = httpx.get(
                    "https://html.duckduckgo.com/html/",
                    params={"q": query},
                    timeout=30.0,
                    follow_redirects=True,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                response.raise_for_status()
            except Exception as exc:
                logger.warning(
                    "web_search: DuckDuckGo HTML fallback failed for '%s': %s",
                    query,
                    exc,
                )
                return []
            soup = BeautifulSoup(response.text, "html.parser")
            scraped: list[dict] = []
            for i, row in enumerate(soup.select(".result")[:num_results]):
                link = row.select_one(".result__a")
                snippet = row.select_one(".result__snippet")
                href = link.get("href", "") if link else ""
                title = link.get_text(strip=True) if link else ""
                body = snippet.get_text(strip=True) if snippet else ""
                if not href and not title:
                    continue
                scraped.append(
                    {
                        "title": title,
                        "url": href,
                        "snippet": body,
                        "relevance_score": 1.0 - (i * 0.05),
                        "citation_id": register_source(
                            title=title, url=href, source_type="web"
                        ),
                    }
                )
            return scraped

        # Tier 1: ddgs package.
        try:
            for i, r in enumerate(_ddgs_text_search(query, max_results=num_results)):
                results.append(
                    {
                        "title": r.get("title", ""),
                        "url": r.get("href", ""),
                        "snippet": r.get("body", ""),
                        "relevance_score": 1.0 - (i * 0.05),
                        "citation_id": register_source(
                            title=r.get("title", ""),
                            url=r.get("href", ""),
                            source_type="web",
                        ),
                    }
                )
        except ImportError:
            logger.info(
                "web_search: `ddgs` package not installed; using HTML fallback."
            )
        except Exception as e:
            logger.warning(
                "web_search: ddgs search failed for '%s': %s — trying HTML fallback.",
                query,
                e,
            )

        # Tier 2: HTML scrape. Fires whenever tier 1 produced no results
        # (because it raised, OR because it succeeded-but-empty).
        if not results:
            results = _scrape_html_fallback()

        # Tier 3: InstantAnswer API. Only useful for Wikipedia-class
        # queries, but better than zero results for those that do match.
        if not results:
            try:
                results = _duckduckgo_instantapi_fallback()
            except Exception as e:
                logger.warning(
                    "web_search: InstantAnswer fallback failed for '%s': %s",
                    query,
                    e,
                )

        urls_used = [r.get("url", "") for r in results if r.get("url")]
        return json.dumps(
            {
                "query": query,
                "engine": "duckduckgo",
                "num_results": len(results),
                "urls_used": urls_used,
                "results": results,
            },
            indent=2,
        )
    
    elif engine.lower() == "google":
        # Google Custom Search API (requires API key setup)
        api_key = os.environ.get("GOOGLE_SEARCH_API_KEY")
        search_engine_id = os.environ.get("GOOGLE_SEARCH_ENGINE_ID")
        
        if not api_key or not search_engine_id:
            return json.dumps({
                "error": "Google Custom Search requires GOOGLE_SEARCH_API_KEY and GOOGLE_SEARCH_ENGINE_ID environment variables",
                "results": []
            })
        
        try:
            import httpx
            url = f"https://www.googleapis.com/customsearch/v1?key={api_key}&cx={search_engine_id}&q={query}&num={num_results}"
            response = httpx.get(url, timeout=30.0)
            response.raise_for_status()
            
            data = response.json()
            results = []
            for i, item in enumerate(data.get("items", [])):
                results.append({
                    "title": item.get("title", ""),
                    "url": item.get("link", ""),
                    "snippet": item.get("snippet", ""),
                    "relevance_score": 1.0 - (i * 0.05),
                    "citation_id": register_source(
                        title=item.get("title", ""),
                        url=item.get("link", ""),
                        source_type="web"
                    )
                })
            
            urls_used = [r.get("url", "") for r in results if r.get("url")]
            return json.dumps({
                "query": query, "engine": "google",
                "num_results": len(results),
                "urls_used": urls_used,
                "results": results
            }, indent=2)
        except ImportError:
            return json.dumps({"error": "httpx package required for Google search", "results": []})
        except Exception as e:
            logger.error(f"web_search: Error searching Google for '{query}': {e}")
            return json.dumps({"error": f"Search failed: {str(e)}", "results": []})
    
    else:
        return json.dumps({"error": f"Unknown search engine: {engine}. Use 'duckduckgo' or 'google'", "results": []})


def _ddgs_text_search(query: str, max_results: int):
    """Wrapper around ddgs import/search to make fallback testing deterministic."""
    from ddgs import DDGS

    with DDGS() as ddg:
        return list(ddg.text(query, max_results=max_results))


def arxiv_search(query: str, folder_context=None, max_results: int = 10, category: str = "") -> str:
    """Search arXiv for academic papers.
    
    Args:
        query: The search query string
        max_results: Maximum number of results to return (default 10, max 50)
        folder_context: Workspace folder context (unused but required for tool signature)
    
    Args:
        category: Optional arXiv category filter (e.g., 'cs.AI', 'physics', 'math.CO')
    Returns:
        JSON string with search results including title, authors, abstract, arXiv ID, PDF link
    """
    import json
    import urllib.parse
    import xml.etree.ElementTree as ET
    
    # Handle None for max_results
    if max_results is None:
        max_results = 10
    # Cap results
    max_results = min(max(1, max_results), 50)
    
    if not query or not query.strip():
        return json.dumps({"engine": "arxiv", "error": "Query cannot be empty", "results": []})
    
    query = query.strip()
    
    try:
        import httpx
        
        # arXiv API endpoint
        base_url = "http://export.arxiv.org/api/query"
        
        # Build the query
        params = {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": max_results,
            "sortBy": "relevance",
            "sortOrder": "descending"
        }
        
        # Make the request
        from utils.anti_detection import get_spoofed_headers
        headers = get_spoofed_headers()
        
        response = httpx.get(base_url, params=params, headers=headers, timeout=30.0, follow_redirects=True)
        response.raise_for_status()
        
        # Parse XML response
        root = ET.fromstring(response.content)
        
        # Define namespaces
        namespaces = {
            'atom': 'http://www.w3.org/2005/Atom',
            'arxiv': 'http://arxiv.org/schemas/atom'
        }
        
        results = []
        for i, entry in enumerate(root.findall('atom:entry', namespaces)):
            title_elem = entry.find('atom:title', namespaces)
            summary_elem = entry.find('atom:summary', namespaces)
            published_elem = entry.find('atom:published', namespaces)
            link_elem = entry.find('atom:id', namespaces)
            
            # Get authors
            authors = []
            for author in entry.findall('atom:author', namespaces):
                name_elem = author.find('atom:name', namespaces)
                if name_elem is not None:
                    authors.append(name_elem.text)
            
            # Get categories
            categories = []
            for category in entry.findall('atom:category', namespaces):
                term = category.get('term')
                if term:
                    categories.append(term)
            
            # Get PDF link
            pdf_link = None
            for link in entry.findall('atom:link', namespaces):
                if link.get('type') == 'application/pdf':
                    pdf_link = link.get('href')
                elif link.get('title') == 'pdf':
                    pdf_link = link.get('href')
            
            # Fallback PDF link construction
            arxiv_id = link_elem.text.split('/abs/')[-1] if link_elem is not None and link_elem.text else None
            if not pdf_link and arxiv_id:
                pdf_link = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
            
            results.append({
                "title": title_elem.text.strip() if title_elem is not None else "",
                "authors": authors,
                "abstract": summary_elem.text.strip()[:500] + "..." if summary_elem is not None and summary_elem.text else "",
                "arxiv_id": arxiv_id,
                "categories": categories,
                "url": link_elem.text if link_elem is not None else "",
                "pdf_link": pdf_link,
                "published": published_elem.text if published_elem is not None else "",
                "relevance_score": 1.0 - (i * 0.05)
            })
        
        # Register citations and add citation_id
        results_with_citations = []
        for result in results:
            citation_id = register_source(
                url=result.get("url", ""),
                title=result.get("title", ""),
                # SourceType has no ARXIV member — arxiv papers ARE academic.
                # The old `SourceType.ARXIV` raised AttributeError every call,
                # which the broad except below collapsed into the cryptic
                # "arXiv search failed: ARXIV" message users saw.
                source_type=SourceType.ACADEMIC,
                authors=result.get("authors", []),
                date=result.get("published", "")
            )
            result["citation_id"] = citation_id
            results_with_citations.append(result)
        
        urls_used = [r.get("pdf_link", "") or r.get("url", "") for r in results_with_citations if r.get("pdf_link") or r.get("url")]
        return json.dumps({"query": query, "engine": "arxiv", "num_results": len(results_with_citations),
                           "urls_used": urls_used, "results": results_with_citations}, indent=2)
    
    except ImportError:
        return json.dumps({"error": "httpx package required for arXiv search. Install with: pip install httpx", "results": []})
    except Exception as e:
        logger.error(f"arxiv_search: Error searching for '{query}': {e}")
        return json.dumps({"engine": "arxiv", "error": f"arXiv search failed: {str(e)}", "results": []})


def doi_resolve(doi: str, format: str = "full", folder_context=None) -> str:
    """Resolve a DOI to get metadata about the publication.
    
    Args:
        doi: The DOI string to resolve (e.g., "10.1000/xyz123")
        format: Output format - 'full' (complete metadata) or 'citation' (formatted citation). Default is 'full'.
        folder_context: Workspace folder context (unused but required for tool signature)
    
    Returns:
        JSON string with publication metadata including title, authors, journal, year, etc.
    """
    import json
    import re
    
    if not doi or not doi.strip():
        return json.dumps({"error": "DOI cannot be empty", "results": []})
    
    doi = doi.strip()
    
    # Clean up DOI - remove 'doi:' prefix if present, and URL prefixes
    doi = re.sub(r'^doi:\s*', '', doi, flags=re.IGNORECASE)
    doi = re.sub(r'^https?://(dx\.)?doi\.org/', '', doi)
    
    # Validate DOI format
    if not re.match(r'^10\.\d{4,}/[^\s]+$', doi):
        return json.dumps({"error": f"Invalid DOI format: {doi}. Expected format: 10.XXXX/...", "doi": doi})
    
    try:
        import httpx
        
        # Use CrossRef API for DOI resolution
        url = f"https://api.crossref.org/works/{doi}"
        
        headers = {
            "Accept": "application/json",
            "User-Agent": "Mu-CLI Research Tool (mailto:contact@example.com)"
        }
        
        with httpx.Client(timeout=30.0) as client:
            response = client.get(url, headers=headers, follow_redirects=True)
            response.raise_for_status()
            
            data = response.json()
            message = data.get("message", {})
            
            # Extract relevant metadata
            result = {
                "doi": message.get("DOI", doi),
                "title": message.get("title", [""])[0] if message.get("title") else "",
                "authors": [
                    f"{a.get('given', '')} {a.get('family', '')}".strip()
                    for a in message.get("author", [])
                ],
                "journal": message.get("container-title", [""])[0] if message.get("container-title") else "",
                "year": message.get("published-print", {}).get("date-parts", [[None]])[0][0] or
                        message.get("published-online", {}).get("date-parts", [[None]])[0][0] or
                        message.get("created", {}).get("date-parts", [[None]])[0][0],
                "publisher": message.get("publisher", ""),
                "type": message.get("type", ""),
                "url": message.get("URL", f"https://doi.org/{doi}"),
                "abstract": message.get("abstract", ""),
                "is_open_access": False,  # CrossRef doesn't provide OA status directly
            }
            
            # Handle citation format output
            if format == "citation" or format == "apa":
                # APA format
                authors_str = ", ".join(result["authors"][:-1]) + (" & " + result["authors"][-1] if len(result["authors"]) > 1 else result["authors"][0] if result["authors"] else "")
                return json.dumps({
                    "citation": f'{authors_str} ({result["year"]}). {result["title"]}. {result["journal"]}, {result["doi"]}.',
                    "doi": result["doi"],
                    "format": "apa"
                }, indent=2)
            elif format == "mla":
                # MLA format
                author = result["authors"][0] if result["authors"] else ""
                last_first = author.split()[-1] + ", " + " ".join(author.split()[:-1]) if author else ""
                return json.dumps({
                    "citation": f'{last_first}. "{result["title"]}." {result["journal"]}, {result["year"]}, {result["doi"]}.',
                    "doi": result["doi"],
                    "format": "mla"
                }, indent=2)
            elif format == "chicago":
                # Chicago format
                authors_str = ", ".join(result["authors"][:-1]) + (" and " + result["authors"][-1] if len(result["authors"]) > 1 else result["authors"][0] if result["authors"] else "")
                return json.dumps({
                    "citation": f'{authors_str}. "{result["title"]}." {result["journal"]} ({result["year"]}): {result["doi"]}.',
                    "doi": result["doi"],
                    "format": "chicago"
                }, indent=2)
            elif format == "bibtex":
                # BibTeX format
                first_author = result["authors"][0].split() if result["authors"] else ["Unknown"]
                cite_key = f'{first_author[-1].lower()}{result["year"] or "nodate"}'
                authors_bibtex = " and ".join(result["authors"])
                bibtex = f'''@article{{{cite_key},
  author = {{{authors_bibtex}}},
  title = {{{result["title"]}}},
  journal = {{{result["journal"]}}},
  year = {{{result["year"] or "n.d."}}},
  doi = {{{result["doi"]}}}
}}'''
                return json.dumps({
                    "citation": bibtex,
                    "doi": result["doi"],
                    "format": "bibtex"
                }, indent=2)
            
            return json.dumps(result, indent=2)
    
    except ImportError:
        return json.dumps({"error": "httpx package required for DOI resolution. Install with: pip install httpx", "doi": doi})
    except httpx.HTTPStatusError as e:
        return json.dumps({"error": f"DOI not found: {doi}", "status_code": e.response.status_code, "doi": doi})
    except Exception as e:
        logger.error(f"doi_resolve: Error resolving DOI '{doi}': {e}")
        return json.dumps({"error": f"DOI resolution failed: {str(e)}", "doi": doi})


def reddit_search(query: str, subreddit: str = None, sort: str = "relevance", limit: int = 10, folder_context=None) -> str:
    """Searches Reddit for posts and comments using Reddit's JSON API with anti-detection measures."""
    if not _check_bounds(query, folder_context):
        logger.warning(f"reddit_search: Access denied for query: {query}")
        return json.dumps({"error": "Access denied"})

    if limit is None:
        limit = 10

    # Use old.reddit.com JSON API (no auth required)
    base_url = "https://old.reddit.com"
    
    # Build search URL
    if subreddit:
        search_url = f"{base_url}/r/{subreddit}/search.json?q={quote(query)}&restrict_sr=on&sort={sort}&limit={limit}"
    else:
        search_url = f"{base_url}/search.json?q={quote(query)}&sort={sort}&limit={limit}"
    
    try:
        import httpx
        
        # Anti-detection headers
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json,text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        }
        
        with httpx.Client(follow_redirects=True, timeout=30.0) as client:
            response = client.get(search_url, headers=headers)
            response.raise_for_status()
            data = response.json()
        
        results = []
        for child in data.get("data", {}).get("children", []):
            post = child.get("data", {})
            citation_id = register_source(url=f"https://reddit.com{post.get('permalink', '')}", title=post.get("title", ""), source_type=SourceType.SOCIAL)
            results.append({
                "title": post.get("title", ""),
                "author": post.get("author", "[deleted]"),
                "subreddit": post.get("subreddit", ""),
                "score": post.get("score", 0),
                "upvote_ratio": post.get("upvote_ratio", 0),
                "num_comments": post.get("num_comments", 0),
                "url": f"https://reddit.com{post.get('permalink', '')}",
                "created_utc": post.get("created_utc", 0),
                "selftext": post.get("selftext", "")[:500] if post.get("selftext") else "",
                "citation_id": citation_id,
                "link_flair_text": post.get("link_flair_text"),
                "is_video": post.get("is_video", False),
            })
        
        urls_used = [r.get("url", "") for r in results if r.get("url")]
        num_results = len(results)
        
        return json.dumps({
            "query": query,
            "subreddit": subreddit,
            "sort": sort,
            "count": len(results),
            "num_results": num_results,
            "urls_used": urls_used,
            "total_results": len(results),
            "urls_used": urls_used,
            "results": results
        }, indent=2)
    
    except ImportError:
        return json.dumps({"error": "httpx package required for Reddit search. Install with: pip install httpx", "query": query})
    except httpx.HTTPStatusError as e:
        return json.dumps({"error": f"Reddit search failed: HTTP {e.response.status_code}", "query": query})
    except Exception as e:
        logger.error(f"reddit_search: Error searching Reddit for '{query}': {e}")
        return json.dumps({"error": f"Reddit search failed: {str(e)}", "query": query})


def stackoverflow_search(query: str, tags: list = None, sort: str = "relevance", limit: int = 10, folder_context=None) -> str:
    """Searches Stack Overflow for questions using the Stack Exchange API with tag filtering support."""
    if not str(query or "").strip():
        return json.dumps({"error": "query is required", "query": query, "results": []})

    query = query.strip()

    if limit is None:
        limit = 10

    # Stack Exchange API endpoint
    base_url = "https://api.stackexchange.com/2.3/search/advanced"
    
    # Build API parameters
    params = {
        "order": "desc",
        "sort": sort,
        "q": query,
        "site": "stackoverflow",
        "pagesize": limit,
        "filter": "withbody",  # Include question body
    }
    
    # Add tag filtering if specified
    if tags:
        params["tagged"] = ";".join(tags)
    
    try:
        import httpx
        
        # Anti-detection headers
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
        }
        
        with httpx.Client(follow_redirects=True, timeout=30.0) as client:
            response = client.get(base_url, params=params, headers=headers)
            response.raise_for_status()
            data = response.json()
        
        results = []
        for item in data.get("items", []):
            citation_id = register_source(url=item.get("link", ""), title=item.get("title", ""), source_type=SourceType.FORUM)
            result = {
                "title": item.get("title", ""),
                "question_id": item.get("question_id"),
                "link": item.get("link", ""),
                "score": item.get("score", 0),
                "answer_count": item.get("answer_count", 0),
                "is_answered": item.get("is_answered", False),
                "view_count": item.get("view_count", 0),
                "tags": item.get("tags", []),
                "citation_id": citation_id,
                "body": item.get("body", "")[:500] if item.get("body") else "",
                "creation_date": item.get("creation_date", 0),
                "last_activity_date": item.get("last_activity_date", 0),
                "owner": {
                    "display_name": item.get("owner", {}).get("display_name", ""),
                    "reputation": item.get("owner", {}).get("reputation", 0),
                }
            }
            results.append(result)
        
        urls_used = [r.get("link", "") for r in results if r.get("link")]
        
        return json.dumps({
            "query": query,
            "tags": tags,
            "sort": sort,
            "count": len(results),
            "total_results": len(results),
            "urls_used": urls_used,
            "has_more": data.get("has_more", False),
            "results": results
        }, indent=2)
    
    except ImportError:
        return json.dumps({"error": "httpx package required for Stack Overflow search. Install with: pip install httpx", "query": query})
    except httpx.HTTPStatusError as e:
        return json.dumps({"error": f"Stack Overflow search failed: HTTP {e.response.status_code}", "query": query})
    except Exception as e:
        logger.error(f"stackoverflow_search: Error searching Stack Overflow for '{query}': {e}")
        return json.dumps({"error": f"Stack Overflow search failed: {str(e)}", "query": query})


def hackernews_search(query: str, sort: str = "relevance", num_results: int = 10, folder_context=None) -> str:
    """Searches Hacker News for relevant stories and discussions using the Algolia HN API."""
    if not query or not query.strip():
        return json.dumps({"error": "Query is required for Hacker News search", "query": query})
    
    query = query.strip()
    num_results = min(max(1, num_results), 50)  # Clamp between 1-50
    
    # Validate sort parameter
    if sort not in ["relevance", "date"]:
        sort = "relevance"
    
    try:
        import httpx
        
        # Algolia HN API endpoint
        # API supports 'search' for stories and 'search_by_date' for chronological
        base_url = "https://hn.algolia.com/api/v1"
        endpoint = "search" if sort == "relevance" else "search_by_date"
        url = f"{base_url}/{endpoint}"
        
        params = {
            "query": query,
            "hitsPerPage": num_results,
            "tags": "story",  # Focus on stories (not comments or polls)
        }
        
        headers = {
            "User-Agent": "Mu-CLI Research Tool",
            "Accept": "application/json",
        }
        
        with httpx.Client(timeout=15.0) as client:
            response = client.get(url, params=params, headers=headers)
            response.raise_for_status()
            data = response.json()
        
        results = []
        for hit in data.get("hits", []):
            citation_id = register_source(url=hit.get("url", ""), title=hit.get("title", ""), source_type=SourceType.NEWS)
            result = {
                "title": hit.get("title", ""),
                "url": hit.get("url", ""),
                "author": hit.get("author", ""),
                "points": hit.get("points", 0),
                "num_comments": hit.get("num_comments", 0),
                "objectID": hit.get("objectID", ""),
                "created_at": hit.get("created_at", ""),
                "story_text": hit.get("story_text", "")[:500] if hit.get("story_text") else "",
                "citation_id": citation_id,
            }
            results.append(result)
        
        urls_used = [r.get("url", "") for r in results if r.get("url")]
        
        return json.dumps({
            "query": query,
            "sort": sort,
            "count": len(results),
            "total_results": len(results),
            "urls_used": urls_used,
            "results": results
        }, indent=2)
    
    except ImportError:
        return json.dumps({"error": "httpx package required for Hacker News search. Install with: pip install httpx", "query": query})
    except httpx.HTTPStatusError as e:
        return json.dumps({"error": f"Hacker News search failed: HTTP {e.response.status_code}", "query": query})
    except Exception as e:
        logger.error(f"hackernews_search: Error searching Hacker News for '{query}': {e}")
        return json.dumps({"error": f"Hacker News search failed: {str(e)}", "query": query})


def _looks_like_url(target: str) -> bool:
    target = (target or "").strip().lower()
    return target.startswith("http://") or target.startswith("https://")


def _extract_pdf_text(reader) -> str:
    """Concatenate every page's extracted text. Empty pages skipped."""
    chunks = []
    for page in reader.pages:
        extracted = page.extract_text()
        if extracted:
            chunks.append(extracted)
    return "\n".join(chunks)


def _pdf_metadata_title(reader) -> str:
    """Best-effort title pulled from the PDF's `/Info` dict. Returns ''
    if not present so the caller can fall back to the URL."""
    try:
        info = reader.metadata
    except Exception:
        return ""
    if not info:
        return ""
    title = getattr(info, "title", None) or info.get("/Title", "")  # type: ignore[union-attr]
    return str(title or "").strip()


def _read_pdf_from_url(url: str) -> str:
    """Fetch a PDF over the network, extract text, register the source
    in the citation engine, and return text plus a citation footer.

    Saves the agent the curl + read_document dance — one tool call.
    """
    try:
        import httpx
    except ImportError:
        return "Error: 'httpx' not installed. Cannot fetch PDFs by URL."
    try:
        from pypdf import PdfReader
    except ImportError:
        return "Error: 'pypdf' not installed. Cannot parse PDF files."

    try:
        response = httpx.get(
            url,
            follow_redirects=True,
            timeout=60.0,
            headers={"User-Agent": "Mozilla/5.0 (mucli read_document)"},
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return f"Error fetching PDF: HTTP {exc.response.status_code} for {url}"
    except httpx.HTTPError as exc:
        return f"Error fetching PDF: {exc}"
    except Exception as exc:  # pragma: no cover — defensive
        logger.error("read_document: fetch failed for %s: %s", url, exc)
        return f"Error fetching PDF: {exc}"

    content_type = (response.headers.get("content-type") or "").lower()
    # Servers sometimes mislabel PDFs as octet-stream — sniff the magic
    # number as a tiebreaker. Real PDFs start with `%PDF-`.
    body = response.content
    looks_pdf = body.startswith(b"%PDF-")
    if "pdf" not in content_type and not looks_pdf:
        return (
            f"Error: URL did not return a PDF (content-type={content_type!r}, "
            f"first bytes={body[:8]!r}). Use url_grounding for HTML pages."
        )

    import io

    try:
        reader = PdfReader(io.BytesIO(body))
    except Exception as exc:
        return f"Error parsing PDF: {exc}"

    if getattr(reader, "is_encrypted", False):
        try:
            # pypdf >=3 supports empty-password decrypt for many files.
            reader.decrypt("")
        except Exception:
            return "Error: PDF is encrypted and cannot be parsed without a password."

    try:
        text = _extract_pdf_text(reader)
    except Exception as exc:
        return f"Error extracting PDF text: {exc}"

    # Auto-register the source so /research bibliography picks it up.
    # Register even when text extraction is empty: the fetch succeeded
    # and the URL is a real source the user pointed at; a blank/OCR-only
    # body is content commentary, not a reason to lose the citation.
    citation_footer = ""
    try:
        from utils.citation_manager import SourceType, register_source

        title = _pdf_metadata_title(reader) or url
        citation_id = register_source(
            title=title,
            url=url,
            source_type=SourceType.ACADEMIC if "arxiv.org" in url.lower() else SourceType.DOCUMENTATION,
        )
        citation_footer = f"\n\n---\nCitation: [^{citation_id}]\nSource: {url}"
    except Exception:  # pragma: no cover — defensive
        logger.debug("read_document: citation registration failed", exc_info=True)

    if not text.strip():
        return (
            f"PDF fetched ({len(body):,} bytes) but no text was extractable. "
            "It may be a scanned/image-only PDF (OCR not supported)."
            + citation_footer
        )

    return text + citation_footer


def read_document(filename: str, folder_context) -> str:
    """Reads and parses documents like PDFs to gather additional context.

    Accepts either a local path (subject to workspace bounds) or an
    http(s) URL — URLs skip the curl + download dance and go straight
    through httpx + pypdf, with the source auto-registered in the
    citation engine on success.
    """
    target = str(filename or "").strip()
    if _looks_like_url(target):
        return _read_pdf_from_url(target)

    if not _check_bounds(filename, folder_context):
        logger.warning(f"read_document: Access denied or file ignored: {filename}")
        return f"Error: Access denied or file ignored. '{filename}'"

    ext = os.path.splitext(filename)[1].lower()
    if ext == ".pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(filename)
            text = _extract_pdf_text(reader)
            return text
        except ImportError:
            logger.error("read_document: 'pypdf' not installed.")
            return (
                "Error: 'pypdf' is not installed. Please install it to parse PDF files."
            )
        except Exception as e:
            logger.error(f"read_document: Error reading PDF {filename}: {e}")
            return f"Error reading PDF: {e}"

    # Default to read_file for other text-based documents
    return read_file(filename, folder_context)


def tool_requires_approval(tool_name: str, args: dict) -> bool:
    """Checks if a tool call requires user approval."""
    tool_def = get_tool_definition(tool_name)
    if not tool_def:
        return False

    if tool_name == "batch_job":
        commands = args.get("commands", [])
        for cmd in commands:
            if tool_requires_approval(cmd.get("tool_name"), cmd.get("tool_args", {})):
                return True
        return False

    return tool_def.requires_approval


def get_modifications(
    tool_name: str, args: dict, folder_context
) -> list[tuple[str, str, str]]:
    """
    Returns a list of (original_content, new_content, filename) for tools that modify files.
    Used for showing diffs before approval.
    """
    if tool_name == "batch_job":
        results = []
        commands = args.get("commands", [])
        for cmd in commands:
            mod = get_modifications(
                cmd.get("tool_name"), cmd.get("tool_args", {}), folder_context
            )
            if mod:
                results.extend(mod)
        return results

    filename = args.get("filename") or args.get("file")
    if not filename:
        return []

    abs_path = os.path.abspath(os.path.expanduser(filename))

    original_content = ""
    if os.path.exists(abs_path):
        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                original_content = f.read()
        except:
            pass

    if tool_name == "write_file":
        return [(original_content, args.get("content", ""), filename)]

    elif tool_name == "apply_diff":
        diff = args.get("diff", "")
        if not original_content:
            return [("", "", filename)]

        # Pre-sanitize the diff
        diff = _sanitize_diff(diff, filename)

        # Use patch to get new content without writing to disk
        import tempfile
        import subprocess

        with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp_orig:
            tmp_orig.write(original_content)
            tmp_orig_path = tmp_orig.name

        with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp_diff:
            tmp_diff.write(diff)
            tmp_diff_path = tmp_diff.name

        try:
            # -u: unified diff
            # -l: ignore whitespace in context
            # -F3: set fuzz factor to 3 lines
            # -o -: output result to stdout
            result = subprocess.run(
                [
                    "patch",
                    "-u",
                    "-l",
                    "-F3",
                    tmp_orig_path,
                    "-i",
                    tmp_diff_path,
                    "-o",
                    "-",
                ],
                capture_output=True,
                text=True,
            )
            new_content = (
                result.stdout if result.returncode == 0 else f"ERROR: {result.stderr}"
            )
            return [(original_content, new_content, filename)]
        except FileNotFoundError:
            return [
                (
                    original_content,
                    "ERROR: 'patch' utility not found on system.",
                    filename,
                )
            ]
        except Exception as e:
            return [(original_content, f"ERROR: {e}", filename)]
        finally:
            if os.path.exists(tmp_orig_path):
                os.unlink(tmp_orig_path)
            if os.path.exists(tmp_diff_path):
                os.unlink(tmp_diff_path)

    return []


# Envelope construction + normalization moved to `mu/tools/_envelope.py`.
# The aliases are bound lazily on first call to avoid the
# `mu.tools.__init__` registry mirror running before `TOOL_HANDLERS` /
# `TOOL_DESCRIPTORS` are defined further down this file. (After the
# bottom-of-file bridge import fires, `mu.tools` is fully loaded and
# every subsequent call goes through the rebound `mu/tools/_envelope`
# implementations directly.)
def infer_tool_error_code(tool_name, result):
    from mu.tools._envelope import infer_tool_error_code as _impl
    global infer_tool_error_code
    infer_tool_error_code = _impl
    return _impl(tool_name, result)


def _build_tool_envelope(**kwargs):
    from mu.tools._envelope import _build_tool_envelope as _impl
    global _build_tool_envelope
    _build_tool_envelope = _impl
    return _impl(**kwargs)


def _envelope_from_handler_result(tool_name, handler_result):
    from mu.tools._envelope import _envelope_from_handler_result as _impl
    global _envelope_from_handler_result
    _envelope_from_handler_result = _impl
    return _impl(tool_name, handler_result)


def serialize_tool_descriptor(tool_name: str) -> dict | None:
    descriptor = get_tool_descriptor(tool_name)
    if not descriptor:
        return None

    definition = descriptor.definition
    return {
        "name": definition.name,
        "description": definition.description,
        "parameters": definition.parameters,
        "requires_approval": definition.requires_approval,
        "execution_kind": descriptor.execution_kind,
        "preview_policy": descriptor.preview_policy,
        "server_policy": descriptor.server_policy,
        "result_mode": descriptor.result_mode,
        "handler_key": descriptor.handler_key,
        "error_mode": descriptor.error_mode,
        "summary_builder": descriptor.summary_builder,
    }


def _path_arg_error(key: str) -> str:
    return (
        f"Error: The '{key}' argument is empty. "
        "You must provide a valid file path from the workspace map."
    )


# `_handle_flush` moved to `mu/tools/batch/handlers.py:flush`.


def _legacy_handler(
    handler: Callable[[dict, Any, Any, dict | None], str],
) -> Callable[[dict, ToolExecutionContext], str]:
    def _wrapped(args: dict, context: ToolExecutionContext) -> str:
        return handler(args, context.folder_context, context.ui, context.variables)

    return _wrapped


# Workspace inspector handlers (get_workspace_details, read_file,
# search_for_string, search_references, retrieve_relevant_context,
# get_chunk, list_dir) moved to `mu/tools/workspace/handlers.py`.


# invoke_skill handler lives in `mu/tools/skill/handlers.py`.
# bash + bg-bash handlers live in `mu/tools/shell/handlers.py`.


# ----------------------------------------------------------- security audit


def _resolve_security_report(session):
    """Lazy-load (or open from session) the current SecurityReport."""
    from core.security_mode import load_report, create_security_report  # noqa
    if session is None:
        return None
    return getattr(session, "_active_security_report", None)


def _handle_create_security_report(
    args: dict, context: ToolExecutionContext
) -> str:
    from core.security_mode import create_security_report, summarize_report
    session = getattr(context, "session", None)
    folder_context = context.folder_context
    session_id = ""
    if session is not None and hasattr(session, "session_manager"):
        session_id = getattr(session.session_manager, "current_session_name", "") or ""
    report = create_security_report(
        title=str(args.get("title", "") or "Security audit"),
        summary=str(args.get("summary", "") or ""),
        folder_context=folder_context,
        session_id=session_id or None,
    )
    if session is not None:
        session._active_security_report = report
        session._active_security_session_id = session_id
    return json.dumps(summarize_report(report), indent=2)


def _require_report(session) -> Any:
    report = _resolve_security_report(session)
    if report is None:
        raise ValueError(
            "no active security report — call `create_security_report` first"
        )
    return report


def _save_active_report(session) -> None:
    from core.security_mode import save_report
    if session is None:
        return
    report = getattr(session, "_active_security_report", None)
    if report is None:
        return
    sid = getattr(session, "_active_security_session_id", "") or ""
    save_report(sid, report)


def _handle_add_security_finding(args: dict, context: ToolExecutionContext) -> str:
    from core.security_mode import add_finding
    session = getattr(context, "session", None)
    try:
        report = _require_report(session)
        finding = add_finding(
            report,
            title=str(args.get("title", "") or ""),
            summary=str(args.get("summary", "") or ""),
            vulnerability_class=str(args.get("vulnerability_class", "") or ""),
            severity=str(args.get("severity", "medium") or "medium"),
            affected_paths=list(args.get("affected_paths", []) or []),
            exploit_path=str(args.get("exploit_path", "") or ""),
            references=list(args.get("references", []) or []),
        )
        _save_active_report(session)
        return json.dumps(
            {
                "finding_id": finding.finding_id,
                "status": finding.status,
                "title": finding.title,
                "severity": finding.severity,
            },
            indent=2,
        )
    except ValueError as e:
        return json.dumps({"error": str(e)})


def _handle_attach_security_proof(
    args: dict, context: ToolExecutionContext
) -> str:
    from core.security_mode import attach_proof
    session = getattr(context, "session", None)
    try:
        report = _require_report(session)
        finding_id = str(args.get("finding_id", "") or "")
        finding = report.find(finding_id)
        if finding is None:
            return json.dumps({"error": f"no such finding: {finding_id}"})
        attach_proof(
            finding,
            command=str(args.get("command", "") or ""),
            expected_markers=list(args.get("expected_markers", []) or []),
            description=str(args.get("description", "") or ""),
            kind=str(args.get("kind", "command") or "command"),
        )
        _save_active_report(session)
        return json.dumps(
            {
                "finding_id": finding.finding_id,
                "status": finding.status,
                "proof_command": finding.proof.command,
                "expected_markers": finding.proof.expected_markers,
            },
            indent=2,
        )
    except ValueError as e:
        return json.dumps({"error": str(e)})


def _handle_verify_security_proof(
    args: dict, context: ToolExecutionContext
) -> str:
    from core.security_mode import verify_proof
    session = getattr(context, "session", None)
    try:
        report = _require_report(session)
        finding_id = str(args.get("finding_id", "") or "")
        finding = report.find(finding_id)
        if finding is None:
            return json.dumps({"error": f"no such finding: {finding_id}"})
        cwd = (
            report.workspace_root
            or (
                context.folder_context.folders[0]
                if context.folder_context and context.folder_context.folders
                else os.getcwd()
            )
        )
        timeout = float(args.get("timeout_seconds", 60) or 60)
        verify_proof(finding, cwd=cwd, report=report, timeout=timeout)
        _save_active_report(session)
        return json.dumps(
            {
                "finding_id": finding.finding_id,
                "status": finding.status,
                "proof_verified": finding.proof.verified if finding.proof else False,
                "exit_code": finding.proof.exit_code if finding.proof else None,
                "notes": finding.proof.notes if finding.proof else "",
                "stdout_tail": (finding.proof.captured_stdout or "")[-1500:] if finding.proof else "",
                "stderr_tail": (finding.proof.captured_stderr or "")[-1500:] if finding.proof else "",
                "evidence_path": finding.proof.last_run_evidence_path if finding.proof else "",
            },
            indent=2,
        )
    except ValueError as e:
        return json.dumps({"error": str(e)})


def _handle_attach_remediation_patch(
    args: dict, context: ToolExecutionContext
) -> str:
    from core.security_mode import attach_remediation
    session = getattr(context, "session", None)
    try:
        report = _require_report(session)
        finding_id = str(args.get("finding_id", "") or "")
        finding = report.find(finding_id)
        if finding is None:
            return json.dumps({"error": f"no such finding: {finding_id}"})
        attach_remediation(
            finding,
            description=str(args.get("description", "") or ""),
            patch_diff=str(args.get("patch_diff", "") or ""),
        )
        _save_active_report(session)
        return json.dumps(
            {
                "finding_id": finding.finding_id,
                "status": finding.status,
                "patch_chars": len(finding.remediation.patch_diff) if finding.remediation else 0,
            },
            indent=2,
        )
    except ValueError as e:
        return json.dumps({"error": str(e)})


def _handle_verify_remediation(
    args: dict, context: ToolExecutionContext
) -> str:
    from core.security_mode import verify_remediation
    session = getattr(context, "session", None)
    try:
        report = _require_report(session)
        finding_id = str(args.get("finding_id", "") or "")
        finding = report.find(finding_id)
        if finding is None:
            return json.dumps({"error": f"no such finding: {finding_id}"})
        cwd = (
            report.workspace_root
            or (
                context.folder_context.folders[0]
                if context.folder_context and context.folder_context.folders
                else os.getcwd()
            )
        )
        timeout = float(args.get("timeout_seconds", 60) or 60)
        verify_remediation(finding, cwd=cwd, report=report, timeout=timeout)
        _save_active_report(session)
        return json.dumps(
            {
                "finding_id": finding.finding_id,
                "status": finding.status,
                "remediation_verified": finding.remediation.verified if finding.remediation else False,
                "post_patch_exit_code": finding.remediation.post_patch_exit_code if finding.remediation else None,
                "notes": finding.remediation.notes if finding.remediation else "",
                "stdout_tail": (finding.remediation.post_patch_stdout or "")[-1500:] if finding.remediation else "",
                "stderr_tail": (finding.remediation.post_patch_stderr or "")[-1500:] if finding.remediation else "",
            },
            indent=2,
        )
    except ValueError as e:
        return json.dumps({"error": str(e)})


def _handle_approve_security_finding(
    args: dict, context: ToolExecutionContext
) -> str:
    from core.security_mode import approve_finding
    session = getattr(context, "session", None)
    try:
        report = _require_report(session)
        finding_id = str(args.get("finding_id", "") or "")
        finding = report.find(finding_id)
        if finding is None:
            return json.dumps({"error": f"no such finding: {finding_id}"})
        approve_finding(finding)
        _save_active_report(session)
        return json.dumps(
            {
                "finding_id": finding.finding_id,
                "status": finding.status,
                "title": finding.title,
                "severity": finding.severity,
            },
            indent=2,
        )
    except ValueError as e:
        return json.dumps({"error": str(e)})


def _handle_refute_security_finding(
    args: dict, context: ToolExecutionContext
) -> str:
    from core.security_mode import refute_finding
    session = getattr(context, "session", None)
    try:
        report = _require_report(session)
        finding_id = str(args.get("finding_id", "") or "")
        finding = report.find(finding_id)
        if finding is None:
            return json.dumps({"error": f"no such finding: {finding_id}"})
        refute_finding(finding, reason=str(args.get("reason", "") or ""))
        _save_active_report(session)
        return json.dumps(
            {"finding_id": finding.finding_id, "status": finding.status}, indent=2
        )
    except ValueError as e:
        return json.dumps({"error": str(e)})


def _handle_get_security_state(
    args: dict, context: ToolExecutionContext
) -> str:
    from core.security_mode import summarize_report
    session = getattr(context, "session", None)
    try:
        report = _require_report(session)
    except ValueError as e:
        return json.dumps({"error": str(e)})
    return json.dumps(summarize_report(report), indent=2)


# Research handlers (url_grounding, web_search, arxiv_search,
# doi_resolve, reddit_search, stackoverflow_search, hackernews_search,
# read_document) live in `mu/tools/research/handlers.py`.


def _handle_raise_blocker(args, folder_context, ui, variables) -> str:
    payload = {
        "kind": "feature_blocker",
        "summary": str(args.get("summary", "")).strip(),
        "details": str(args.get("details", "")).strip(),
        "requested_input": str(args.get("requested_input", "")).strip(),
        "questions": [
            str(item).strip() for item in args.get("questions", []) if str(item).strip()
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def _resolve_feature_state(session, requested_feature_id: str | None = None):
    feature_state = None
    if requested_feature_id:
        feature_state = session.session_manager.get_feature(requested_feature_id)
    if not feature_state:
        feature_state = session.session_manager.get_feature_state()
    if not isinstance(feature_state, dict):
        return feature_state

    feature_id = str(feature_state.get("feature_id", "") or "").strip()
    directory = str(feature_state.get("directory", "") or "").strip()
    metadata_path = str(feature_state.get("metadata_path", "") or "").strip()

    candidates = [metadata_path]
    if feature_id and hasattr(session.session_manager, "get_feature_metadata_path"):
        try:
            candidates.append(
                str(session.session_manager.get_feature_metadata_path(feature_id) or "").strip()
            )
        except TypeError:
            pass
    if directory and hasattr(session.session_manager, "get_feature_metadata_index"):
        metadata_index = session.session_manager.get_feature_metadata_index() or {}
        if isinstance(metadata_index, dict):
            candidates.append(str(metadata_index.get(directory, "") or "").strip())
    if directory:
        candidates.append(os.path.join(directory, "feature_plan.json"))

    resolved = next((path for path in candidates if path and os.path.exists(path)), "")
    if resolved and resolved != metadata_path:
        feature_state["metadata_path"] = resolved
        if feature_id:
            session.session_manager.upsert_feature(feature_state)
        if session.session_manager.get_feature_state():
            session.session_manager.set_feature_state(feature_state)
        session.session_manager.save_history()
    return feature_state


def _resolve_feature_metadata_path(
    session,
    context: ToolExecutionContext,
    *,
    feature_id: str | None = None,
    directory: str | None = None,
) -> str:
    feature_state = _resolve_feature_state(session, feature_id)
    candidates: list[str] = []
    if isinstance(feature_state, dict):
        candidates.append(str(feature_state.get("metadata_path", "") or "").strip())
        if not directory:
            directory = str(feature_state.get("directory", "") or "").strip()
    if feature_id and hasattr(session.session_manager, "get_feature_metadata_path"):
        try:
            candidates.append(
                str(session.session_manager.get_feature_metadata_path(feature_id) or "").strip()
            )
        except TypeError:
            pass
    if directory and hasattr(session.session_manager, "get_feature_metadata_index"):
        metadata_index = session.session_manager.get_feature_metadata_index() or {}
        if isinstance(metadata_index, dict):
            candidates.append(str(metadata_index.get(directory, "") or "").strip())
    if directory:
        candidates.append(os.path.join(directory, "feature_plan.json"))
    folder_index = getattr(context.folder_context, "feature_metadata_index", {}) or {}
    if directory and isinstance(folder_index, dict):
        candidates.append(str(folder_index.get(directory, "") or "").strip())
    return next((path for path in candidates if path and os.path.exists(path)), "")


def _handle_create_feature(args: dict, context: ToolExecutionContext) -> str:
    session = context.session
    if not session:
        return "Error: This tool requires an active session context."
    feature_name = str(args.get("feature_name", "")).strip()
    feature_request = str(args.get("feature_request", "")).strip()
    feature_id = str(args.get("feature_id", "")).strip() or None
    design_plan = str(args.get("design_plan", "")).strip()

    if not feature_name:
        return "Error: feature_name is required."
    if not feature_request:
        return "Error: feature_request is required."

    requested_feature_id = feature_id or re.sub(
        r"[^a-zA-Z0-9]+", "_", feature_name.lower()
    ).strip("_")
    feature_id = session.session_manager.allocate_feature_id(requested_feature_id)
    metadata_path = session.session_manager.get_feature_metadata_path(feature_id)
    plan = create_feature_shell(
        feature_name=feature_name,
        feature_request=feature_request,
        folder_context=context.folder_context,
        feature_id=feature_id,
        metadata_path=metadata_path,
    )
    if design_plan:
        plan.review_notes = design_plan
        plan = update_feature_plan_metadata(
            path_or_session_id=plan.metadata_path,
            review_notes=design_plan,
            metadata_path=plan.metadata_path,
        )

    summary = summarize_feature_plan(plan)
    feature_record = {
        "type": "feature",
        "status": "draft",
        "feature_id": plan.feature_id,
        "feature_name": plan.feature_name,
        "directory": plan.directory,
        "metadata_path": plan.metadata_path,
        "feature_plan": summary,
        "blocker": None,
        "updated_at": time.time(),
    }
    session.session_manager.upsert_feature(feature_record)
    session.session_manager.activate_feature(plan.feature_id)
    session.session_manager.save_history()
    return json.dumps(
        {
            "ok": True,
            "feature_id": plan.feature_id,
            "metadata_path": plan.metadata_path,
            "plan": summary,
        },
        indent=2,
        sort_keys=True,
    )


def _handle_create_phases(args: dict, context: ToolExecutionContext) -> str:
    session = context.session
    if not session:
        return "Error: This tool requires an active session context."
    phases = args.get("phases", [])
    if not isinstance(phases, list) or not phases:
        return "Error: phases array is required."
    feature_id = str(args.get("feature_id", "")).strip() or None
    feature_state = _resolve_feature_state(session, feature_id)
    if not feature_state:
        return "Error: No active feature in session. Call create_feature first."
    metadata_path = feature_state.get("metadata_path", "")
    if not metadata_path or not os.path.exists(metadata_path):
        return "Error: Feature metadata not found."

    plan = create_feature_phases(
        metadata_path,
        phases,
        replace_existing=bool(args.get("replace_existing", True)),
        actor="agent",
    )
    summary = summarize_feature_plan(plan)
    feature_state["feature_plan"] = summary
    feature_state["updated_at"] = time.time()
    session.session_manager.upsert_feature(feature_state)
    session.session_manager.save_history()
    return json.dumps(
        {
            "ok": True,
            "feature_id": plan.feature_id,
            "phase_count": len(plan.phases_meta),
            "plan": summary,
        },
        indent=2,
        sort_keys=True,
    )


def _handle_create_task(args: dict, context: ToolExecutionContext) -> str:
    session = context.session
    if not session:
        return "Error: This tool requires an active session context."
    feature_id = str(args.get("feature_id", "")).strip() or None
    feature_state = _resolve_feature_state(session, feature_id)
    if not feature_state:
        return "Error: No active feature in session. Call create_feature first."
    metadata_path = feature_state.get("metadata_path", "")
    if not metadata_path or not os.path.exists(metadata_path):
        return "Error: Feature metadata not found."
    title = str(args.get("title", "")).strip()
    exit_criteria = args.get("exit_criteria", [])
    if not title:
        return "Error: title is required."
    if not isinstance(exit_criteria, list) or not exit_criteria:
        return "Error: exit_criteria must be a non-empty array."
    task_data = {
        "phase_id": args.get("phase_id"),
        "title": title,
        "objectives": [str(args.get("overview", "")).strip()] if args.get("overview") else [],
        "action_points": [str(item).strip() for item in args.get("design", [])],
        "exit_criteria": [str(item).strip() for item in exit_criteria],
        "notes": str(args.get("notes", "") or ""),
    }
    plan, task = create_feature_task(metadata_path, task_data, actor="agent")
    summary = summarize_feature_plan(plan)
    feature_state["feature_plan"] = summary
    feature_state["updated_at"] = time.time()
    session.session_manager.upsert_feature(feature_state)
    session.session_manager.save_history()
    return json.dumps(
        {
            "ok": True,
            "feature_id": plan.feature_id,
            "task_id": task.id,
            "plan": summary,
        },
        indent=2,
        sort_keys=True,
    )


def _handle_get_execution_state(args: dict, context: ToolExecutionContext) -> str:
    session = context.session
    if not session:
        return "Error: This tool requires an active session context."
    feature_id = str(args.get("feature_id", "")).strip() or None
    feature_state = _resolve_feature_state(session, feature_id)
    if not feature_state:
        return "Error: No active feature in session."
    metadata_path = _resolve_feature_metadata_path(
        session,
        context,
        feature_id=feature_id,
        directory=str(feature_state.get("directory", "") or "").strip(),
    )
    if not metadata_path:
        return "Error: Feature metadata not found."
    plan = load_feature_plan(metadata_path)
    snapshot = feature_execution_snapshot(plan)
    return json.dumps({"ok": True, "execution": snapshot}, indent=2, sort_keys=True)


def _handle_block_task(args: dict, context: ToolExecutionContext) -> str:
    session = context.session
    if not session:
        return "Error: This tool requires an active session context."
    task_id = args.get("task_id")
    reason = str(args.get("reason", "")).strip()
    if task_id is None:
        return "Error: task_id is required."
    if not reason:
        return "Error: reason is required."
    feature_id = str(args.get("feature_id", "")).strip() or None
    feature_state = _resolve_feature_state(session, feature_id)
    if not feature_state:
        return "Error: No active feature in session."
    metadata_path = feature_state.get("metadata_path", "")
    if not metadata_path or not os.path.exists(metadata_path):
        return "Error: Feature metadata not found."
    plan = load_feature_plan(metadata_path)
    transition_task_status(
        plan,
        task_id=int(task_id),
        to_status="blocked",
        notes=str(args.get("requested_input", "") or ""),
        blocked_reason=reason,
        actor="agent",
    )
    plan = save_feature_plan("", plan)
    summary = summarize_feature_plan(plan)
    feature_state["feature_plan"] = summary
    feature_state["updated_at"] = time.time()
    session.session_manager.upsert_feature(feature_state)
    session.session_manager.save_history()
    return json.dumps(
        {
            "ok": True,
            "task_id": int(task_id),
            "status": "blocked",
            "plan": summary,
        },
        indent=2,
        sort_keys=True,
    )


def _handle_resume_task(args: dict, context: ToolExecutionContext) -> str:
    session = context.session
    if not session:
        return "Error: This tool requires an active session context."
    task_id = args.get("task_id")
    if task_id is None:
        return "Error: task_id is required."
    feature_id = str(args.get("feature_id", "")).strip() or None
    feature_state = _resolve_feature_state(session, feature_id)
    if not feature_state:
        return "Error: No active feature in session."
    metadata_path = feature_state.get("metadata_path", "")
    if not metadata_path or not os.path.exists(metadata_path):
        return "Error: Feature metadata not found."
    plan = load_feature_plan(metadata_path)
    transition_task_status(
        plan,
        task_id=int(task_id),
        to_status="in_progress",
        notes=str(args.get("notes", "") or ""),
        actor="agent",
    )
    plan = save_feature_plan("", plan)
    summary = summarize_feature_plan(plan)
    feature_state["feature_plan"] = summary
    feature_state["updated_at"] = time.time()
    session.session_manager.upsert_feature(feature_state)
    session.session_manager.save_history()
    return json.dumps(
        {
            "ok": True,
            "task_id": int(task_id),
            "status": "in_progress",
            "plan": summary,
        },
        indent=2,
        sort_keys=True,
    )


def _handle_review_completed_tasks(args: dict, context: ToolExecutionContext) -> str:
    session = context.session
    if not session:
        return "Error: This tool requires an active session context."
    feature_state = _resolve_feature_state(
        session, str(args.get("feature_id", "")).strip() or None
    )
    if not feature_state:
        return "Error: No active feature in session."
    metadata_path = feature_state.get("metadata_path", "")
    if not metadata_path or not os.path.exists(metadata_path):
        return "Error: Feature metadata not found."
    plan, review = create_task_review_record(
        metadata_path,
        task_id=int(args.get("task_id")),
        summary=str(args.get("summary", "")),
        limitations=args.get("limitations", []),
        issues=args.get("issues", []),
        actor="agent",
    )
    summary = summarize_feature_plan(plan)
    feature_state["feature_plan"] = summary
    feature_state["updated_at"] = time.time()
    session.session_manager.upsert_feature(feature_state)
    session.session_manager.save_history()
    return json.dumps(
        {
            "ok": True,
            "review": asdict(review),
            "plan": summary,
        },
        indent=2,
        sort_keys=True,
    )


def _handle_review_all_completed_tasks(args: dict, context: ToolExecutionContext) -> str:
    session = context.session
    if not session:
        return "Error: This tool requires an active session context."
    feature_state = _resolve_feature_state(
        session, str(args.get("feature_id", "")).strip() or None
    )
    if not feature_state:
        return "Error: No active feature in session."
    metadata_path = feature_state.get("metadata_path", "")
    if not metadata_path or not os.path.exists(metadata_path):
        return "Error: Feature metadata not found."
    plan, created = create_reviews_for_completed_tasks(metadata_path, actor="agent")
    summary = summarize_feature_plan(plan)
    feature_state["feature_plan"] = summary
    feature_state["updated_at"] = time.time()
    session.session_manager.upsert_feature(feature_state)
    session.session_manager.save_history()
    return json.dumps(
        {
            "ok": True,
            "created_review_count": len(created),
            "reviews": [asdict(item) for item in created],
            "plan": summary,
        },
        indent=2,
        sort_keys=True,
    )


def _handle_propose_task_diff(args: dict, context: ToolExecutionContext) -> str:
    session = context.session
    if not session:
        return "Error: This tool requires an active session context."
    feature_state = _resolve_feature_state(
        session, str(args.get("feature_id", "")).strip() or None
    )
    if not feature_state:
        return "Error: No active feature in session."
    metadata_path = feature_state.get("metadata_path", "")
    if not metadata_path or not os.path.exists(metadata_path):
        return "Error: Feature metadata not found."
    plan, proposal = create_diff_proposal(
        metadata_path,
        review_id=str(args.get("review_id", "")),
        issue_id=str(args.get("issue_id", "")),
        diff=str(args.get("diff", "")),
        actor="agent",
    )
    summary = summarize_feature_plan(plan)
    feature_state["feature_plan"] = summary
    feature_state["updated_at"] = time.time()
    session.session_manager.upsert_feature(feature_state)
    session.session_manager.save_history()
    return json.dumps(
        {"ok": True, "proposal": asdict(proposal), "plan": summary},
        indent=2,
        sort_keys=True,
    )


def _handle_decide_task_diff(args: dict, context: ToolExecutionContext) -> str:
    session = context.session
    if not session:
        return "Error: This tool requires an active session context."
    feature_state = _resolve_feature_state(
        session, str(args.get("feature_id", "")).strip() or None
    )
    if not feature_state:
        return "Error: No active feature in session."
    metadata_path = feature_state.get("metadata_path", "")
    if not metadata_path or not os.path.exists(metadata_path):
        return "Error: Feature metadata not found."
    plan, proposal = decide_diff_proposal(
        metadata_path,
        proposal_id=str(args.get("proposal_id", "")),
        decision=str(args.get("decision", "")),
        reason=str(args.get("reason", "")),
        actor="user",
    )
    summary = summarize_feature_plan(plan)
    feature_state["feature_plan"] = summary
    feature_state["updated_at"] = time.time()
    session.session_manager.upsert_feature(feature_state)
    session.session_manager.save_history()
    return json.dumps(
        {"ok": True, "proposal": asdict(proposal), "plan": summary},
        indent=2,
        sort_keys=True,
    )


def _handle_archive_task(args: dict, context: ToolExecutionContext) -> str:
    session = context.session
    if not session:
        return "Error: This tool requires an active session context."
    feature_state = _resolve_feature_state(
        session, str(args.get("feature_id", "")).strip() or None
    )
    if not feature_state:
        return "Error: No active feature in session."
    metadata_path = feature_state.get("metadata_path", "")
    if not metadata_path or not os.path.exists(metadata_path):
        return "Error: Feature metadata not found."
    plan = archive_feature_task(metadata_path, task_id=int(args.get("task_id")), actor="user")
    summary = summarize_feature_plan(plan)
    feature_state["feature_plan"] = summary
    feature_state["updated_at"] = time.time()
    session.session_manager.upsert_feature(feature_state)
    session.session_manager.save_history()
    return json.dumps(
        {
            "ok": True,
            "task_id": int(args.get("task_id")),
            "status": "archived",
            "plan": summary,
        },
        indent=2,
        sort_keys=True,
    )


def _handle_create_feature_task(args: dict, context: ToolExecutionContext) -> str:
    """Creates a structured feature implementation plan."""
    session = context.session
    if not session:
        return "Error: This tool requires an active session context."

    feature_name = args.get("feature_name", "").strip()
    feature_request = args.get("feature_request", "").strip()
    feature_id = args.get("feature_id", "").strip() or None
    tasks_data = args.get("tasks", [])

    if not feature_name:
        return "Error: feature_name is required."
    if not tasks_data:
        return "Error: tasks array is required."

    if isinstance(tasks_data, str):
        raw_tasks = tasks_data.strip()
        try:
            tasks_data = json.loads(raw_tasks)
        except json.JSONDecodeError as exc:
            return (
                "Error: tasks must be a JSON array of task objects. "
                f"Received an invalid JSON string ({exc.msg} at pos {exc.pos})."
            )

    if not isinstance(tasks_data, list):
        return (
            "Error: tasks must be an array of task objects, "
            f"got {type(tasks_data).__name__}."
        )

    first_invalid = next(
        (
            (idx, item)
            for idx, item in enumerate(tasks_data, start=1)
            if not isinstance(item, dict)
        ),
        None,
    )
    if first_invalid:
        idx, item = first_invalid
        return (
            "Error: tasks must be an array of objects. "
            f"Task #{idx} is {type(item).__name__}: {item!r}"
        )

    # Get or create feature record
    existing_feature = session.session_manager.get_feature(feature_id)
    if existing_feature:
        metadata_path = existing_feature.get("metadata_path", "")
        directory = existing_feature.get("directory", "")
    else:
        # Create new feature
        directory = _workspace_root(context.folder_context)
        requested_feature_id = feature_id or re.sub(
            r"[^a-zA-Z0-9]+", "_", feature_name.lower()
        ).strip("_")
        feature_id = session.session_manager.allocate_feature_id(requested_feature_id)
        metadata_path = session.session_manager.get_feature_metadata_path(feature_id)
        os.makedirs(os.path.dirname(metadata_path), exist_ok=True)

    # Create the feature plan
    plan = create_feature_plan(
        feature_name=feature_name,
        feature_request=feature_request,
        tasks_data=tasks_data,
        folder_context=context.folder_context,
        feature_id=feature_id,
        metadata_path=metadata_path,
    )

    # Update session state
    summary = summarize_feature_plan(plan)
    feature_record = {
        "type": "feature",
        "status": "draft",
        "feature_id": plan.feature_id,
        "feature_name": plan.feature_name,
        "directory": directory or plan.directory,
        "metadata_path": plan.metadata_path,
        "feature_plan": summary,
        "blocker": None,
        "updated_at": time.time(),
    }
    session.session_manager.upsert_feature(feature_record)
    session.session_manager.activate_feature(plan.feature_id)
    session.session_manager.save_history()

    if context.ui:
        context.ui.show_info(
            f"Created feature plan: {plan.feature_id} with {len(plan.tasks)} tasks"
        )

    return json.dumps(
        {
            "ok": True,
            "feature_id": plan.feature_id,
            "task_count": len(plan.tasks),
            "plan": summary,
        },
        indent=2,
        sort_keys=True,
    )


def _handle_update_feature_task(args: dict, context: ToolExecutionContext) -> str:
    """Updates task content before approval."""
    session = context.session
    if not session:
        return "Error: This tool requires an active session context."

    task_id = args.get("task_id")
    if task_id is None:
        return "Error: task_id is required."

    feature_state = session.session_manager.get_feature_state()
    if not feature_state:
        return "Error: No active feature in session."

    metadata_path = feature_state.get("metadata_path", "")
    if not metadata_path or not os.path.exists(metadata_path):
        return "Error: Feature metadata not found."

    plan = update_task_content(
        metadata_path,
        task_id,
        title=args.get("title"),
        objectives=args.get("objectives"),
        action_points=args.get("action_points"),
        exit_criteria=args.get("exit_criteria"),
        notes=args.get("notes"),
    )

    summary = summarize_feature_plan(plan)
    session.session_manager.set_feature_state(
        {
            "feature_plan": summary,
            **feature_state,
        }
    )

    return json.dumps(
        {"ok": True, "task_id": task_id, "plan": summary}, indent=2, sort_keys=True
    )


def _handle_approve_feature_task(args: dict, context: ToolExecutionContext) -> str:
    """Approves or rejects the feature plan."""
    session = context.session
    if not session:
        return "Error: This tool requires an active session context."

    approved = args.get("approved", True)

    feature_state = session.session_manager.get_feature_state() or {}
    directory = str(
        args.get("directory")
        or feature_state.get("directory", "")
        or ""
    ).strip()
    metadata_path = str(feature_state.get("metadata_path", "") or "").strip()
    if not metadata_path:
        metadata_path = str(
            getattr(context.folder_context, "feature_metadata_index", {}).get(
                directory, ""
            )
            or ""
        ).strip()
    if not metadata_path and directory:
        metadata_path = os.path.join(directory, "feature_plan.json")
    if not metadata_path or not os.path.exists(metadata_path):
        return "Error: Feature metadata not found."

    plan = update_feature_plan_metadata(
        directory or feature_state.get("directory", ""),
        approved=approved,
        review_status=args.get("review_status"),
        review_notes=args.get("review_notes"),
        metadata_path=metadata_path,
    )

    summary = summarize_feature_plan(plan)
    status = "approved" if approved else "rejected"

    if context.ui:
        context.ui.show_info(f"Feature plan {status}: {plan.feature_id}")

    # Update in-memory feature state so status reflects approval/review
    feature_state = session.session_manager.get_feature_state() or {}
    updated_feature = {
        **feature_state,
        "directory": directory or feature_state.get("directory", ""),
        "metadata_path": metadata_path,
        "feature_plan": summary,
    }
    session.session_manager.set_feature_state(updated_feature)

    return json.dumps(
        {
            "ok": True,
            "approved": approved,
            "feature_id": plan.feature_id,
            "plan": summary,
        },
        indent=2,
        sort_keys=True,
    )


def _handle_get_current_task(args: dict, context: ToolExecutionContext) -> str:
    """Gets the current active task."""
    session = context.session
    if not session:
        return "Error: This tool requires an active session context."

    feature_state = _resolve_feature_state(session)
    if not feature_state:
        return json.dumps(
            {"error": "No active feature in session.", "task": None}, indent=2
        )

    metadata_path = _resolve_feature_metadata_path(
        session,
        context,
        feature_id=str(feature_state.get("feature_id", "") or "").strip() or None,
        directory=str(feature_state.get("directory", "") or "").strip(),
    )
    if not metadata_path:
        return json.dumps(
            {"error": "Feature metadata not found.", "task": None}, indent=2
        )

    plan = load_feature_plan(metadata_path)
    next_task = plan.next_incomplete_task()

    if next_task:
        return json.dumps(
            {"task": asdict(next_task), "feature_id": plan.feature_id},
            indent=2,
            sort_keys=True,
        )
    else:
        return json.dumps(
            {
                "task": None,
                "message": "All tasks completed.",
                "feature_id": plan.feature_id,
            },
            indent=2,
        )


def _handle_get_tasks(args: dict, context: ToolExecutionContext) -> str:
    """Gets all tasks in the feature plan."""
    session = context.session
    if not session:
        return "Error: This tool requires an active session context."

    feature_state = _resolve_feature_state(session)
    if not feature_state:
        return json.dumps(
            {"error": "No active feature in session.", "tasks": []}, indent=2
        )

    metadata_path = _resolve_feature_metadata_path(
        session,
        context,
        feature_id=str(feature_state.get("feature_id", "") or "").strip() or None,
        directory=str(feature_state.get("directory", "") or "").strip(),
    )
    if not metadata_path:
        return json.dumps(
            {"error": "Feature metadata not found.", "tasks": []}, indent=2
        )

    plan = load_feature_plan(metadata_path)
    tasks = [asdict(t) for t in plan.tasks]

    return json.dumps(
        {
            "tasks": tasks,
            "feature_id": plan.feature_id,
            "feature_name": plan.feature_name,
        },
        indent=2,
        sort_keys=True,
    )


def _handle_update_task_status(args: dict, context: ToolExecutionContext) -> str:
    """Updates task status during execution."""
    session = context.session
    if not session:
        return "Error: This tool requires an active session context."

    task_id = args.get("task_id")
    status = args.get("status")
    notes = args.get("notes")
    verified_exit_criteria = args.get("verified_exit_criteria", [])

    if task_id is None:
        return "Error: task_id is required."
    if not status:
        return "Error: status is required."

    valid_statuses = [
        "pending",
        "not_started",
        "in_progress",
        "blocked",
        "completed",
        "archived",
    ]
    if status not in valid_statuses:
        return f"Error: status must be one of {valid_statuses}."

    feature_state = _resolve_feature_state(session) or {}
    directory = str(
        args.get("directory")
        or feature_state.get("directory", "")
        or ""
    ).strip()
    metadata_path = _resolve_feature_metadata_path(
        session,
        context,
        feature_id=str(feature_state.get("feature_id", "") or "").strip() or None,
        directory=directory,
    )
    if not metadata_path:
        return "Error: Feature metadata not found."

    if verified_exit_criteria is not None and not isinstance(verified_exit_criteria, list):
        return "Error: verified_exit_criteria must be an array when provided."

    if status == "completed":
        plan_snapshot = load_feature_plan(metadata_path)
        target_task = next(
            (item for item in plan_snapshot.tasks if item.id == int(task_id)),
            None,
        )
        if target_task is None:
            return f"Error: Task {task_id} not found."
        expected = [str(item).strip() for item in target_task.exit_criteria if str(item).strip()]
        already_verified = {
            str(item).strip()
            for item in getattr(target_task, "verified_exit_criteria", []) or []
            if str(item).strip()
        }
        provided = {
            str(item).strip() for item in (verified_exit_criteria or []) if str(item).strip()
        }
        effective_verified = already_verified | provided
        missing = [criterion for criterion in expected if criterion not in effective_verified]
        if missing:
            return (
                "Error: Cannot mark task completed until all exit criteria are verified. "
                f"Missing: {missing}"
            )
    else:
        plan_snapshot = load_feature_plan(metadata_path)
        target_task = next(
            (item for item in plan_snapshot.tasks if item.id == int(task_id)),
            None,
        )
        if target_task is None:
            return f"Error: Task {task_id} not found."
        already_verified = {
            str(item).strip()
            for item in getattr(target_task, "verified_exit_criteria", []) or []
            if str(item).strip()
        }
        provided = {
            str(item).strip() for item in (verified_exit_criteria or []) if str(item).strip()
        }
        effective_verified = already_verified | provided

    plan = update_task_status(
        metadata_path,
        task_id,
        status,
        notes,
        verified_exit_criteria=sorted(effective_verified),
    )
    summary = summarize_feature_plan(plan)

    # Update session state
    updated_feature = {
        **feature_state,
        "directory": directory or feature_state.get("directory", summary.get("directory")),
        "metadata_path": metadata_path,
        "feature_plan": summary,
    }
    session.session_manager.set_feature_state(updated_feature)

    if context.ui:
        context.ui.show_info(f"Task {task_id} status updated to '{status}'")

    return json.dumps(
        {"ok": True, "task_id": task_id, "status": status, "plan": summary},
        indent=2,
        sort_keys=True,
    )


# `_handle_batch_job` moved to `mu/tools/batch/handlers.py:batch_job`.
# Body archived below in `_handle_batch_job_legacy_archive` for diff
# history; never called.
def _handle_batch_job_legacy_archive(args: dict, context: ToolExecutionContext) -> str:  # pragma: no cover
    commands = args.get("commands", [])
    if not isinstance(commands, list):
        return json.dumps(
            _build_tool_envelope(
                tool_name="batch_job",
                ok=False,
                error_code="invalid_args",
                message="Error: 'commands' must be a list.",
                data={"children": []},
            )
        )

    children = []
    for i, cmd in enumerate(commands):
        if not isinstance(cmd, dict):
            children.append(
                {
                    "index": i,
                    "tool_name": None,
                    "result": _build_tool_envelope(
                        tool_name="batch_job",
                        ok=False,
                        error_code="invalid_args",
                        message=f"Error: Command {i} - invalid command entry.",
                    ),
                }
            )
            continue

        name = cmd.get("tool_name")
        t_args = cmd.get("tool_args", {})

        if not name:
            children.append(
                {
                    "index": i,
                    "tool_name": None,
                    "result": _build_tool_envelope(
                        tool_name="batch_job",
                        ok=False,
                        error_code="invalid_args",
                        message=f"Error: Command {i} - tool_name missing.",
                    ),
                }
            )
            continue

        nested_descriptor = get_tool_descriptor(name)
        if not nested_descriptor:
            children.append(
                {
                    "index": i,
                    "tool_name": name,
                    "result": _build_tool_envelope(
                        tool_name=name,
                        ok=False,
                        error_code="not_found",
                        message=f"Error: Command {i} - unknown tool: {name}",
                    ),
                }
            )
            continue

        if nested_descriptor.execution_kind == "composite":
            children.append(
                {
                    "index": i,
                    "tool_name": name,
                    "result": _build_tool_envelope(
                        tool_name=name,
                        ok=False,
                        error_code="unsupported",
                        message=f"Error: Command {i} - nested batch_job not allowed.",
                    ),
                }
            )
            continue

        if context.ui:
            context.ui.show_info(
                f"  [{i + 1}/{len(commands)}] Executing in batch: {name}"
            )

        res = execute_tool(
            name,
            t_args,
            context.folder_context,
            context.ui,
            context.variables,
            invocation_source=context.invocation_source,
        )
        try:
            child_result = json.loads(res)
        except Exception:
            child_result = _envelope_from_handler_result(name, res)
        children.append({"index": i, "tool_name": name, "result": child_result})

    ok = all(bool(item.get("result", {}).get("ok")) for item in children)
    message = (
        f"Batch completed with {len(children)} command(s)."
        if ok
        else f"Batch completed with failures in {sum(1 for c in children if not c.get('result', {}).get('ok'))} command(s)."
    )
    return json.dumps(
        _build_tool_envelope(
            tool_name="batch_job",
            ok=ok,
            error_code=None if ok else "execution_failed",
            message=message,
            data={"children": children, "count": len(children)},
        ),
        indent=2,
    )


TOOL_HANDLERS: dict[str, Callable[[dict, ToolExecutionContext], str]] = {
    # Every tool now registers via the `@tool` decorator under
    # `mu/tools/<group>/handlers.py`. The legacy `TOOL_HANDLERS` dict is
    # kept for backward compatibility with code that mutates it directly
    # (eg. some test fixtures) — the bridge in `mu/tools/__init__.py`
    # mirrors new registrations into it at decoration time.
    # Security-mode handlers (create_security_report, add_security_finding,
    # attach_security_proof, verify_security_proof, attach_remediation_patch,
    # verify_remediation, approve_security_finding, refute_security_finding,
    # get_security_state) register via the `@tool` decorator in
    # mu/tools/security/handlers.py.
    # Feature-mode handlers (create_feature, create_phases, create_task,
    # get_execution_state, block_task, resume_task, review_*,
    # propose/decide_task_diff, archive_task, create/update/approve_feature_task,
    # get_current_task, get_tasks, update_task_status, raise_blocker)
    # register via the `@tool` decorator in mu/tools/feature/handlers.py.
    # batch_job + flush register via the `@tool` decorator in
    # mu/tools/batch/handlers.py.
    # write_file / apply_diff / search_and_replace_file register via the
    # `@tool` decorator in mu/tools/file/handlers.py.
}


def execute_tool(
    tool_name: str,
    args: dict,
    folder_context,
    ui=None,
    variables: dict = None,
    *,
    invocation_source: str = "session",
    session: Any = None,
) -> str:
    """Descriptor-backed dispatcher with argument validation.

    The body lives in `mu/tools/_dispatcher.py:dispatch`. This thin
    shim preserves the legacy `from core.tools import execute_tool`
    import path used by `Session._execute_tool_with_memory` and a
    handful of tests.
    """
    from mu.tools._dispatcher import dispatch

    return dispatch(
        tool_name,
        args,
        folder_context,
        ui,
        variables,
        invocation_source=invocation_source,
        session=session,
    )


# ============================================================================
# Bottom-of-file: import `mu.tools`.
#
# Triggering the registry HERE — after every legacy TOOL_DESCRIPTORS,
# TOOL_HANDLERS and TOOLS structure is populated — lets the `@tool`
# decorators in `mu/tools/<group>/handlers.py` mirror their descriptors
# back into the legacy maps without hitting a "partially initialized
# module" circular-import error. Without this import, the @tool
# registrations only happen when something else (eg. `core.session`)
# imports `mu.tools` later in the program lifetime, which makes the
# legacy `execute_tool` look like it has lost its handlers when tests
# import `core.tools` directly.
import mu.tools as _mu_tools_bridge  # noqa: E402, F401
