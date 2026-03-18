import os
import datetime
import difflib
import re
from providers.base import ToolDefinition

# --- Tool Definitions (Schemas) ---

TOOLS = [
    ToolDefinition(
        name="get_workspace_details",
        description="Returns a string of the current workspace's path, files, and folders. Use this to discover the structure of the attached project.",
        parameters={"type": "object", "properties": {}},
        requires_approval=False,
    ),
    ToolDefinition(
        name="read_file",
        description="Returns the whole file contents. Use this to read the code or text of a specific file.",
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
    ),
    ToolDefinition(
        name="search_for_string",
        description="Returns a list of all files that contain the string as well as the line number. Use this to find variable usages, function definitions, or specific text.",
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
    ),
    ToolDefinition(
        name="get_chunk",
        description="Returns a string of the file contents between the start and end line numbers. Use this to read a specific portion of a large file.",
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
    ),
    ToolDefinition(
        name="get_current_time",
        description="Returns the current time in ISO format.",
        parameters={"type": "object", "properties": {}},
        requires_approval=False,
    ),
    ToolDefinition(
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
    ),
    ToolDefinition(
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
    ),
    ToolDefinition(
        name="apply_diff",
        description="Applies a unified diff to a file. This is preferred over write_file for incremental changes.",
        parameters={
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Path to the file."},
                "diff": {
                    "type": "string",
                    "description": "The unified diff content to apply.",
                },
            },
            "required": ["filename", "diff"],
        },
        requires_approval=True,
    ),
    ToolDefinition(
        name="batch_job",
        description="Executes multiple tool calls in sequence. Returns the results of all calls in the order they were provided.",
        parameters={
            "type": "object",
            "properties": {
                "commands": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "tool_name": {
                                "type": "string",
                                "description": "The name of the tool to execute.",
                            },
                            "tool_args": {"type": "object"},
                        },
                        "required": ["tool_name", "tool_args"],
                    },
                }
            },
            "required": ["commands"],
        },
        requires_approval=False, # We will query the individual tools and only require a single approval
    ),
]


def _check_bounds(filename: str, folder_context) -> bool:
    """Validates if a file path is within the attached workspace folders and not ignored."""
    if not folder_context or not folder_context.folders:
        return True  # If no workspace attached, bypass boundary strictness

    abs_path = os.path.abspath(os.path.expanduser(filename))

    # Check if it's within any of the workspace folders
    within_bounds = False
    for f in folder_context.folders:
        if abs_path.startswith(os.path.abspath(f)):
            within_bounds = True
            break

    if not within_bounds:
        return False

    # Check if it's ignored
    if folder_context.is_ignored(abs_path):
        return False

    return True


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
        return f"Error: Access denied or file ignored. '{filename}' is outside boundaries or in ignore list."
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return f"Error: File '{filename}' not found. Try using search_for_string to locate it."
    except UnicodeDecodeError:
        return f"Error: '{filename}' appears to be a binary file or has an unsupported encoding."
    except Exception as e:
        return f"Error reading file: {e}"


def search_for_string(search_string: str, folder_context) -> str:
    """Returns a list of all files that contain the string as well as the line number."""
    if not folder_context:
        return "No workspace attached to search."

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
    return "\n".join(results)


def get_chunk(filename: str, start_line: int, end_line: int, folder_context) -> str:
    """Returns a string of the file contents between the start and end line numbers."""
    if not _check_bounds(filename, folder_context):
        return f"Error: Access denied or file ignored. '{filename}' is outside boundaries or in ignore list."
    try:
        with open(filename, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # 1-indexed to 0-indexed
        start_idx = max(0, start_line - 1)
        end_idx = min(len(lines), end_line)

        chunk = lines[start_idx:end_idx]
        return "".join(chunk)
    except FileNotFoundError:
        return f"Error: File '{filename}' not found. Try using search_for_string to locate it."
    except UnicodeDecodeError:
        return f"Error: '{filename}' appears to be a binary file or has an unsupported encoding."
    except Exception as e:
        return f"Error reading file chunk: {e}"


def get_current_time(folder_context) -> str:
    """Returns the current time in ISO format."""
    try:
        return datetime.datetime.now().isoformat()
    except Exception as e:
        return f"Error getting current time: {e}"


def list_dir(path: str, folder_context) -> str:
    """Returns a list of files and directories in the specified path."""
    target_path = path or "."
    # We check bounds for the directory itself
    if not _check_bounds(target_path, folder_context):
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
        return f"Error listing directory: {e}"


def write_file(filename: str, content: str, folder_context) -> str:
    """Creates or overwrites a file with the provided content."""
    if not _check_bounds(filename, folder_context):
        return f"Error: Access denied or path ignored. '{filename}'"

    try:
        dirname = os.path.dirname(filename)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
        with open(filename, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully wrote to {filename}"
    except Exception as e:
        return f"Error writing file: {e}"


def apply_diff(filename: str, diff: str, folder_context) -> str:
    """Applies a unified diff to a file."""
    if not _check_bounds(filename, folder_context):
        return f"Error: Access denied or path ignored. '{filename}'"

    try:
        if not os.path.exists(filename):
            return f"Error: File '{filename}' does not exist. Cannot apply diff."

        with open(filename, "r", encoding="utf-8") as f:
            original_content = f.read()

        # Ensure diff header is present or fix common LLM mistakes
        diff_lines = diff.splitlines()
        if not any(l.startswith("--- ") for l in diff_lines[:3]):
            # Inject dummy header if missing
            diff = f"--- a/{filename}\n+++ b/{filename}\n" + diff

        # Use patch logic (difflib doesn't have apply, so we use a simple approach or external tool if preferred)
        # For simplicity and cross-platform, we can try a basic hunk application or require 'patch' utility
        # Here's a pure python way to try and apply a unified diff

        # We'll use a temporary file and the 'patch' command if available,
        # or implement a basic hunk applier.

        import tempfile
        import subprocess

        with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp_diff:
            tmp_diff.write(diff)
            tmp_diff_path = tmp_diff.name

        try:
            # Try using system 'patch' command first as it is robust
            result = subprocess.run(
                ["patch", "-u", filename, "-i", tmp_diff_path],
                capture_output=True,
                text=True,
            )
            os.unlink(tmp_diff_path)

            if result.returncode == 0:
                return f"Successfully applied diff to {filename}"
            else:
                return (
                    f"Error applying diff via 'patch': {result.stderr or result.stdout}"
                )
        except FileNotFoundError:
            os.unlink(tmp_diff_path)
            return "Error: 'patch' utility not found on system. Please install it to apply diffs."

    except Exception as e:
        return f"Error applying diff: {e}"


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

        # Use patch to get new content without writing to disk
        import tempfile
        import subprocess

        with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp_orig:
            tmp_orig.write(original_content)
            tmp_orig_path = tmp_orig.name

        with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp_diff:
            # Ensure header
            if not any(l.startswith("--- ") for l in diff.splitlines()[:3]):
                diff = f"--- a/{filename}\n+++ b/{filename}\n" + diff
            tmp_diff.write(diff)
            tmp_diff_path = tmp_diff.name

        try:
            result = subprocess.run(
                ["patch", "-u", tmp_orig_path, "-i", tmp_diff_path, "-o", "-"],
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


def execute_tool(tool_name: str, args: dict, folder_context, ui=None) -> str:
    """Dispatcher with argument validation"""

    # 1. Validate Path-based arguments for basic tools
    path_keys = ["filename", "file", "path"]
    for k in path_keys:
        if k in args and (not args[k] or str(args[k]).strip() == ""):
            return f"Error: The '{k}' argument is empty. You must provide a valid file path from the workspace map."

    if tool_name == "get_workspace_details":
        return get_workspace_details(folder_context)
    elif tool_name == "read_file":
        return read_file(args.get("filename", ""), folder_context)
    elif tool_name == "search_for_string":
        return search_for_string(args.get("string", ""), folder_context)
    elif tool_name == "get_chunk":
        return get_chunk(
            args.get("file", ""),
            args.get("start_line", 1),
            args.get("end_line", 100),
            folder_context,
        )
    elif tool_name == "get_current_time":
        return get_current_time(folder_context)
    elif tool_name == "list_dir":
        return list_dir(args.get("path", ""), folder_context)
    elif tool_name == "write_file":
        return write_file(
            args.get("filename", ""), args.get("content", ""), folder_context
        )
    elif tool_name == "apply_diff":
        return apply_diff(
            args.get("filename", ""), args.get("diff", ""), folder_context
        )
    elif tool_name == "batch_job":
        commands = args.get("commands", [])
        if not isinstance(commands, list):
            return "Error: 'commands' must be a list."

        results = []
        for i, cmd in enumerate(commands):
            name = cmd.get("tool_name")
            t_args = cmd.get("tool_args", {})

            if not name:
                results.append(f"Command {i}: Error - tool_name missing.")
                continue

            if name == "batch_job":
                results.append(f"Command {i}: Error - nested batch_job not allowed.")
                continue

            if ui:
                ui.show_info(f"  [{i+1}/{len(commands)}] Executing in batch: {name}")

            # Recursively execute the tool in the batch
            res = execute_tool(name, t_args, folder_context, ui)
            results.append(f"Tool: {name}\nResult: {res}")

        return (
            "--- Batch Job Results ---\n"
            + "\n\n---\n\n".join(results)
            + "\n------------------------"
        )
    else:
        return f"Unknown tool: {tool_name}"
