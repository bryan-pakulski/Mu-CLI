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
        requires_approval=False,  # We will query the individual tools and only require a single approval
    ),
    ToolDefinition(
        name="list_agent_tasks",
        description="Lists available tasks in Makefile.agents and their descriptions. Use this to discover automation scripts and when to run them.",
        parameters={"type": "object", "properties": {}},
        requires_approval=False,
    ),
    ToolDefinition(
        name="run_agent_task",
        description="Executes a task defined in Makefile.agents. This can be used for running tests, builds, or other automated processes.",
        parameters={
            "type": "object",
            "properties": {
                "task_name": {
                    "type": "string",
                    "description": "The name of the task to execute.",
                }
            },
            "required": ["task_name"],
        },
        requires_approval=True,
    ),
    ToolDefinition(
        name="git_status",
        description="Shows the working tree status.",
        parameters={"type": "object", "properties": {}},
        requires_approval=False,
    ),
    ToolDefinition(
        name="git_log",
        description="Shows the commit logs.",
        parameters={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Limit the number of commits to show.",
                    "default": 10,
                }
            },
        },
        requires_approval=False,
    ),
    ToolDefinition(
        name="git_diff",
        description="Shows changes between commits, commit and working tree, etc.",
        parameters={
            "type": "object",
            "properties": {
                "cached": {
                    "type": "boolean",
                    "description": "Show changes that are staged for the next commit.",
                    "default": False,
                },
                "filename": {
                    "type": "string",
                    "description": "Optional file path to limit the diff to.",
                },
            },
        },
        requires_approval=False,
    ),
    ToolDefinition(
        name="git_checkout",
        description="Switch branches or restore working tree files.",
        parameters={
            "type": "object",
            "properties": {
                "branch": {
                    "type": "string",
                    "description": "The name of the branch to checkout.",
                },
                "create": {
                    "type": "boolean",
                    "description": "Whether to create the branch if it doesn't exist.",
                    "default": False,
                },
            },
            "required": ["branch"],
        },
        requires_approval=True,
    ),
    ToolDefinition(
        name="git_add",
        description="Adds file contents to the index (staging area).",
        parameters={
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of files to add. Use ['.'] for all changes.",
                }
            },
            "required": ["files"],
        },
        requires_approval=True,
    ),
    ToolDefinition(
        name="git_commit",
        description="Record changes to the repository.",
        parameters={
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The commit message.",
                }
            },
            "required": ["message"],
        },
        requires_approval=True,
    ),
    ToolDefinition(
        name="git_push",
        description="Update remote refs along with associated objects.",
        parameters={
            "type": "object",
            "properties": {
                "remote": {
                    "type": "string",
                    "description": "The name of the remote. Defaults to 'origin'.",
                    "default": "origin",
                },
                "branch": {
                    "type": "string",
                    "description": "The name of the branch to push. Defaults to current branch.",
                },
            },
        },
        requires_approval=True,
    ),
    ToolDefinition(
        name="git_pull",
        description="Fetch from and integrate with another repository or a local branch.",
        parameters={
            "type": "object",
            "properties": {
                "remote": {
                    "type": "string",
                    "description": "The name of the remote. Defaults to 'origin'.",
                    "default": "origin",
                },
                "branch": {
                    "type": "string",
                    "description": "The name of the branch to pull. Defaults to current branch.",
                },
            },
        },
        requires_approval=True,
    ),
    ToolDefinition(
        name="git_init",
        description="Initialize a new git repository in the workspace.",
        parameters={"type": "object", "properties": {}},
        requires_approval=True,
    ),
    ToolDefinition(
        name="git_merge_request",
        description="Launch a merge request for the current branch. In this CLI it simulates the action and provides a summary.",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "The title of the merge request."},
                "description": {"type": "string", "description": "The description of the changes."},
            },
        },
        requires_approval=True,
    ),
    ToolDefinition(
        name="git_branch",
        description="List, create, or delete branches.",
        parameters={"type": "object", "properties": {}},
        requires_approval=False,
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


def _sanitize_diff(diff: str, filename: str) -> str:
    """Cleans up common LLM diff issues."""
    lines = diff.splitlines()
    if not lines:
        return diff

    # Strip preamble text before the first header or hunk
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
            # Recount hunk
            header = hlines[0]
            # Try to match standard @@ -start,len +start,len @@
            match = re.match(r"^@@ -(\d+),?\d* \+(\d+),?\d* @@(.*)$", header)
            if match:
                start_old, start_new, tail = match.groups()
                count_old = 0
                count_new = 0
                for hl in hlines[1:]:
                    if hl.startswith("-"):
                        count_old += 1
                    elif hl.startswith("+"):
                        count_new += 1
                    elif hl.startswith(" "):
                        count_old += 1
                        count_new += 1
                new_header = (
                    f"@@ -{start_old},{count_old} +{start_new},{count_new} @@{tail}"
                )
                sanitized.append(new_header)
                sanitized.extend(hlines[1:])
                return
        sanitized.extend(hlines)

    # Ensure file headers
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
                sanitized.append(line)
        else:
            # Likely context missing its space
            if hunk_lines:
                hunk_lines.append(" " + line)
            else:
                sanitized.append(" " + line)

    flush_hunk(hunk_lines)

    return "\n".join(sanitized) + "\n"


def apply_diff(filename: str, diff: str, folder_context) -> str:
    """Applies a unified diff to a file."""
    if not _check_bounds(filename, folder_context):
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


def list_agent_tasks(folder_context) -> str:
    """Lists tasks from Makefile.agents with their descriptions."""
    if not folder_context or not folder_context.folders:
        return "No workspace attached."

    found_any = False
    all_tasks = []

    for folder in folder_context.folders:
        makefile_path = os.path.join(folder, "Makefile.agents")
        if os.path.exists(makefile_path):
            found_any = True
            tasks = []
            try:
                with open(makefile_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                    for i, line in enumerate(lines):
                        # 1. Target with ## description on same line
                        match = re.match(r"^([a-zA-Z0-9_-]+):.*?##\s*(.*)$", line)
                        if match:
                            tasks.append(f"  - {match.group(1)}: {match.group(2)}")
                            continue

                        # 2. Target without ## description on same line
                        match = re.match(r"^([a-zA-Z0-9_-]+):", line)
                        if match:
                            target = match.group(1)
                            if target in ["FORCE", ".PHONY"]:
                                continue
                            # Check line above for ## description
                            description = ""
                            if i > 0:
                                prev_line = lines[i - 1].strip()
                                if prev_line.startswith("##"):
                                    description = prev_line.lstrip("#").strip()
                            if description:
                                tasks.append(f"  - {target}: {description}")
                            else:
                                tasks.append(f"  - {target}")
                if tasks:
                    all_tasks.append(f"In {folder}:\n" + "\n".join(tasks))
            except Exception as e:
                all_tasks.append(f"Error reading {makefile_path}: {e}")

    if not found_any:
        return "No Makefile.agents found in any workspace folder."

    if not all_tasks:
        return "No tasks found in any Makefile.agents."

    return "Available tasks in Makefile.agents:\n\n" + "\n\n".join(all_tasks)


def run_agent_task(task_name: str, folder_context, variables: dict = None) -> str:
    """Executes a task from Makefile.agents."""
    if not variables:
        variables = {}

    if not folder_context or not folder_context.folders:
        return "No workspace attached."

    # Load variables with defaults
    timeout = variables.get("make_timeout", 600)
    max_len = variables.get("make_max_output", 10000)

    makefile_path = None
    for folder in folder_context.folders:
        path = os.path.join(folder, "Makefile.agents")
        if os.path.exists(path):
            makefile_path = path
            break

    if not makefile_path:
        return "Error: Makefile.agents not found in any workspace folder."

    import subprocess

    cmd = ["make", "-f", "Makefile.agents", task_name]
    try:
        process = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=os.path.dirname(makefile_path),
        )

        output = process.stdout
        errors = process.stderr

        combined_output = ""
        if output:
            combined_output += f"STDOUT:\n{output}\n"
        if errors:
            combined_output += f"STDERR:\n{errors}\n"

        if not combined_output:
            combined_output = "Task executed successfully with no output."

        # Truncate if too long (max 10k chars)
        if len(combined_output) > max_len:
            combined_output = (
                combined_output[: max_len // 2]
                + f"\n\n... [TRUNCATED {len(combined_output) - max_len} characters] ...\n\n"
                + combined_output[-max_len // 2 :]
            )

        return combined_output

    except subprocess.TimeoutExpired as e:
        return f"Error: Task timed out after {timeout} seconds. Partial output:\n{e.stdout or ''}\n{e.stderr or ''}"
    except Exception as e:
        return f"Error executing task: {e}"

def run_git_command(args_list: list[str], folder_context) -> str:
    """Executes a git command in the primary workspace folder."""
    if not folder_context or not folder_context.folders:
        return "Error: No workspace attached."

    import subprocess

    # Assume git should run from the first folder (primary project root)
    cwd = folder_context.folders[0]

    cmd = ["git"] + args_list
    try:
        process = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd,
        )

        output = process.stdout
        errors = process.stderr

        combined_output = ""
        if output:
            combined_output += output
        if errors:
            combined_output += f"\nSTDERR:\n{errors}"

        if not combined_output:
            combined_output = "Command executed successfully with no output."

        return combined_output
    except Exception as e:
        return f"Error executing git command: {e}"

def git_status(folder_context) -> str:
    """Shows the working tree status."""
    return run_git_command(["status"], folder_context)

def git_init(folder_context) -> str:
    """Initialize a new git repository."""
    return run_git_command(["init"], folder_context)

def git_log(limit: int = 10, folder_context=None) -> str:
    """Shows the commit logs."""
    return run_git_command(["log", "--oneline", "-n", str(limit)], folder_context)

def git_diff(cached: bool = False, filename: str = None, folder_context=None) -> str:
    """Shows changes between commits, commit and working tree, etc."""
    cmd_args = ["diff"]
    if cached:
        cmd_args.append("--cached")
    if filename:
        if not _check_bounds(filename, folder_context):
             return f"Error: Access denied or path ignored. '{filename}'"
        cmd_args.append(filename)
    return run_git_command(cmd_args, folder_context)

def git_checkout(branch: str, create: bool = False, folder_context=None) -> str:
    """Switch branches or restore working tree files."""
    cmd_args = ["checkout"]
    if create:
        cmd_args.append("-b")
    cmd_args.append(branch)
    return run_git_command(cmd_args, folder_context)

def git_add(files: list[str], folder_context=None) -> str:
    """Adds file contents to the index."""
    for f in files:
        if f != "." and not _check_bounds(f, folder_context):
             return f"Error: Access denied or path ignored. '{f}'"
    
    cmd_args = ["add"] + files
    return run_git_command(cmd_args, folder_context)

def git_commit(message: str, folder_context=None) -> str:
    """Record changes to the repository."""
    return run_git_command(["commit", "-m", message], folder_context)

def git_push(remote: str = "origin", branch: str = None, folder_context=None) -> str:
    """Update remote refs."""
    cmd_args = ["push", remote]
    if branch:
        cmd_args.append(branch)
    return run_git_command(cmd_args, folder_context)

def git_pull(remote: str = "origin", branch: str = None, folder_context=None) -> str:
    """Fetch from and integrate with another repository."""
    cmd_args = ["pull", remote]
    if branch:
        cmd_args.append(branch)
    return run_git_command(cmd_args, folder_context)

def git_branch(folder_context) -> str:
    """List branches."""
    return run_git_command(["branch"], folder_context)


def tool_requires_approval(tool_name: str, args: dict) -> bool:
    """Checks if a tool call requires user approval."""
    tool_def = next((t for t in TOOLS if t.name == tool_name), None)
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


def execute_tool(
    tool_name: str, args: dict, folder_context, ui=None, variables: dict = None
) -> str:
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
    elif tool_name == "list_agent_tasks":
        return list_agent_tasks(folder_context)
    elif tool_name == "run_agent_task":
        return run_agent_task(args.get("task_name", ""), folder_context, variables)
    elif tool_name == "apply_diff":
        return apply_diff(
            args.get("filename", ""), args.get("diff", ""), folder_context
        )
    elif tool_name == "git_status":
        return git_status(folder_context)
    elif tool_name == "git_init":
        return git_init(folder_context)
    elif tool_name == "git_log":
        return git_log(args.get("limit", 10), folder_context)
    elif tool_name == "git_diff":
        return git_diff(args.get("cached", False), args.get("filename"), folder_context)
    elif tool_name == "git_checkout":
        return git_checkout(
            args.get("branch", ""), args.get("create", False), folder_context
        )
    elif tool_name == "git_add":
        return git_add(args.get("files", []), folder_context)
    elif tool_name == "git_commit":
        return git_commit(args.get("message", ""), folder_context)
    elif tool_name == "git_push":
        return git_push(
            args.get("remote", "origin"), args.get("branch"), folder_context
        )
    elif tool_name == "git_merge_request":
        return f"Merge Request '{args.get('title')}' launched successfully!\nDescription: {args.get('description')}"
    elif tool_name == "git_pull":
        return git_pull(
            args.get("remote", "origin"), args.get("branch"), folder_context
        )
    elif tool_name == "git_branch":
        return git_branch(folder_context)
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
            res = execute_tool(name, t_args, folder_context, ui, variables)
            results.append(f"Tool: {name}\nResult: {res}")

        return (
            "--- Batch Job Results ---\n"
            + "\n\n---\n\n".join(results)
            + "\n------------------------"
        )
    else:
        return f"Unknown tool: {tool_name}"
