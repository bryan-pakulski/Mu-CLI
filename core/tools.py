import os
import datetime
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
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        with open(filename, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully wrote to {filename}"
    except Exception as e:
        return f"Error writing file: {e}"


def execute_tool(tool_name: str, args: dict, folder_context) -> str:
    """Dispatcher to execute the local Python functions based on tool name."""
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
    else:
        return f"Unknown tool: {tool_name}"
