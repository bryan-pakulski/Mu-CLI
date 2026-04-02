import json
import os
import time
import datetime
import difflib
import re
from dataclasses import dataclass, asdict, field
from typing import Any, Callable
from providers.base import ToolDefinition
from utils.logger import logger
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
                    "description": "The unified diff content to apply. MUST follow standard unified diff format: --- filename, +++ filename, @@ -L,C +L,C @@ headers, and +/-/space line markers.",
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
                "title": {
                    "type": "string",
                    "description": "The title of the merge request.",
                },
                "description": {
                    "type": "string",
                    "description": "The description of the changes.",
                },
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
    ToolDefinition(
        name="url_grounding",
        description="Accesses a URL to gather additional context. Supports JavaScript-heavy websites.",
        parameters={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to access.",
                }
            },
            "required": ["url"],
        },
        requires_approval=False,
    ),
    ToolDefinition(
        name="read_document",
        description="Reads and parses documents like PDFs to gather additional context.",
        parameters={
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "The path to the document file (e.g., a PDF).",
                }
            },
            "required": ["filename"],
        },
        requires_approval=False,
    ),
    ToolDefinition(
        name="flush",
        description="Flushes the collation buffer and returns all the gathered context to the model. Use this when you have finished gathering all the necessary information and are ready to process it.",
        parameters={"type": "object", "properties": {}},
        requires_approval=False,
    ),
    ToolDefinition(
        name="save_memory",
        description="Saves a short, important fact into the in-task memory store so it can be reused later without replaying large context.",
        parameters={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The concise fact, decision, or reminder to store.",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags to help later retrieval.",
                },
                "source": {
                    "type": "string",
                    "description": "Optional note about where this memory came from.",
                },
            },
            "required": ["content"],
        },
        requires_approval=False,
    ),
    ToolDefinition(
        name="save_scratchpad",
        description="Saves a temporary note in the current turn scratchpad. Use this for short-lived plans or observations that do not need durable memory.",
        parameters={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The temporary note to store for the current turn.",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags to help later retrieval during this turn.",
                },
                "source": {
                    "type": "string",
                    "description": "Optional source note for the scratchpad entry.",
                },
            },
            "required": ["content"],
        },
        requires_approval=False,
    ),
    ToolDefinition(
        name="search_memory",
        description="Searches the in-task memory store for previously saved facts.",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search terms to match against memory content, tags, and sources.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of memory entries to return.",
                    "default": 5,
                },
            },
        },
        requires_approval=False,
    ),
    ToolDefinition(
        name="search_scratchpad",
        description="Searches turn-local scratchpad notes saved during the current task loop.",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search terms to match against scratchpad content, tags, and sources.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of scratchpad entries to return.",
                    "default": 5,
                },
            },
        },
        requires_approval=False,
    ),
    ToolDefinition(
        name="list_memory",
        description="Lists the most recent in-task memory entries.",
        parameters={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of memory entries to return.",
                    "default": 10,
                }
            },
        },
        requires_approval=False,
    ),
    ToolDefinition(
        name="list_scratchpad",
        description="Lists the most recent turn-local scratchpad entries.",
        parameters={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of scratchpad entries to return.",
                    "default": 10,
                }
            },
        },
        requires_approval=False,
    ),
    ToolDefinition(
        name="clear_scratchpad",
        description="Clears the current turn scratchpad.",
        parameters={"type": "object", "properties": {}},
        requires_approval=False,
    ),
    ToolDefinition(
        name="create_feature",
        description="Creates (or upserts) a feature shell from a confirmed design plan. Stage 1 of feature mode planning.",
        parameters={
            "type": "object",
            "properties": {
                "feature_name": {"type": "string"},
                "feature_request": {"type": "string"},
                "feature_id": {"type": "string"},
                "design_plan": {"type": "string"},
            },
            "required": ["feature_name", "feature_request"],
        },
        requires_approval=False,
    ),
    ToolDefinition(
        name="create_phases",
        description="Creates or replaces phases/epics for an active feature. Stage 2 of feature mode planning.",
        parameters={
            "type": "object",
            "properties": {
                "feature_id": {"type": "string"},
                "replace_existing": {"type": "boolean", "default": True},
                "phases": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "title": {"type": "string"},
                            "goal": {"type": "string"},
                            "order": {"type": "integer"},
                            "status": {"type": "string"},
                        },
                        "required": ["title", "goal"],
                    },
                },
            },
            "required": ["phases"],
        },
        requires_approval=False,
    ),
    ToolDefinition(
        name="create_task",
        description="Creates a single task/ticket for an active feature phase. Stage 3 of feature mode planning.",
        parameters={
            "type": "object",
            "properties": {
                "feature_id": {"type": "string"},
                "phase_id": {"type": "integer"},
                "title": {"type": "string"},
                "overview": {"type": "string"},
                "design": {"type": "array", "items": {"type": "string"}},
                "exit_criteria": {"type": "array", "items": {"type": "string"}},
                "notes": {"type": "string"},
            },
            "required": ["title", "exit_criteria"],
        },
        requires_approval=False,
    ),
    ToolDefinition(
        name="get_execution_state",
        description="Returns the phase/task execution cursor, including blocked tasks and next actionable work item.",
        parameters={
            "type": "object",
            "properties": {
                "feature_id": {"type": "string"},
            },
        },
        requires_approval=False,
    ),
    ToolDefinition(
        name="block_task",
        description="Moves a task to blocked with an explicit reason and optional user input request.",
        parameters={
            "type": "object",
            "properties": {
                "feature_id": {"type": "string"},
                "task_id": {"type": "integer"},
                "reason": {"type": "string"},
                "requested_input": {"type": "string"},
            },
            "required": ["task_id", "reason"],
        },
        requires_approval=False,
    ),
    ToolDefinition(
        name="resume_task",
        description="Moves a blocked task back to in_progress after required user input has been provided.",
        parameters={
            "type": "object",
            "properties": {
                "feature_id": {"type": "string"},
                "task_id": {"type": "integer"},
                "notes": {"type": "string"},
            },
            "required": ["task_id"],
        },
        requires_approval=False,
    ),
    ToolDefinition(
        name="review_completed_tasks",
        description="Creates structured review records for completed tasks with categorized issues (bug/risk/enhancement).",
        parameters={
            "type": "object",
            "properties": {
                "feature_id": {"type": "string"},
                "task_id": {"type": "integer"},
                "summary": {"type": "string"},
                "limitations": {"type": "array", "items": {"type": "string"}},
                "issues": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "title": {"type": "string"},
                            "category": {
                                "type": "string",
                                "enum": ["bug", "risk", "enhancement"],
                            },
                            "details": {"type": "string"},
                        },
                        "required": ["title", "category"],
                    },
                },
            },
            "required": ["task_id", "summary"],
        },
        requires_approval=False,
    ),
    ToolDefinition(
        name="review_all_completed_tasks",
        description="Auto-creates baseline review records for every completed task that does not yet have one.",
        parameters={
            "type": "object",
            "properties": {"feature_id": {"type": "string"}},
        },
        requires_approval=False,
    ),
    ToolDefinition(
        name="propose_task_diff",
        description="Creates a diff proposal for a review issue, requiring later user decision.",
        parameters={
            "type": "object",
            "properties": {
                "feature_id": {"type": "string"},
                "review_id": {"type": "string"},
                "issue_id": {"type": "string"},
                "diff": {"type": "string"},
            },
            "required": ["review_id", "issue_id", "diff"],
        },
        requires_approval=False,
    ),
    ToolDefinition(
        name="decide_task_diff",
        description="Stores user decision (approved/denied) for a proposed task diff.",
        parameters={
            "type": "object",
            "properties": {
                "feature_id": {"type": "string"},
                "proposal_id": {"type": "string"},
                "decision": {"type": "string", "enum": ["approved", "denied"]},
                "reason": {"type": "string"},
            },
            "required": ["proposal_id", "decision"],
        },
        requires_approval=False,
    ),
    ToolDefinition(
        name="archive_task",
        description="Archives an archive-ready task after review and diff decisions are complete.",
        parameters={
            "type": "object",
            "properties": {
                "feature_id": {"type": "string"},
                "task_id": {"type": "integer"},
            },
            "required": ["task_id"],
        },
        requires_approval=False,
    ),
    ToolDefinition(
        name="create_feature_task",
        description="Creates a structured feature implementation plan consisting of one or more tasks. Each task must include explicit exit_criteria. Stores metadata internally.",
        parameters={
            "type": "object",
            "properties": {
                "feature_name": {
                    "type": "string",
                    "description": "Short feature name.",
                },
                "feature_request": {
                    "type": "string",
                    "description": "Full description of the feature request.",
                },
                "feature_id": {
                    "type": "string",
                    "description": "Optional stable identifier.",
                },
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "objectives": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "action_points": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "exit_criteria": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "notes": {"type": "string"},
                        },
                        "required": [
                            "title",
                            "objectives",
                            "action_points",
                            "exit_criteria",
                        ],
                    },
                },
            },
            "required": ["feature_name", "feature_request", "tasks"],
        },
        requires_approval=False,
    ),
    ToolDefinition(
        name="update_feature_task",
        description="Modifies the details of a task before approval.",
        parameters={
            "type": "object",
            "properties": {
                "task_id": {"type": "integer"},
                "title": {"type": "string"},
                "objectives": {"type": "array", "items": {"type": "string"}},
                "action_points": {"type": "array", "items": {"type": "string"}},
                "exit_criteria": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["task_id"],
        },
        requires_approval=True,
    ),
    ToolDefinition(
        name="approve_feature_task",
        description="Approves the feature plan, allowing implementation to begin.",
        parameters={
            "type": "object",
            "properties": {
                "approved": {"type": "boolean", "default": True},
            },
        },
        requires_approval=True,
    ),
    ToolDefinition(
        name="get_current_task",
        description="Retrieves the currently active task in the feature plan.",
        parameters={"type": "object", "properties": {}},
        requires_approval=False,
    ),
    ToolDefinition(
        name="get_tasks",
        description="Retrieves all tasks in the feature plan (previous, current, and upcoming).",
        parameters={"type": "object", "properties": {}},
        requires_approval=False,
    ),
    ToolDefinition(
        name="update_task_status",
        description="Updates the status of a specific task. Set status='completed' only after all exit_criteria for the task are verified.",
        parameters={
            "type": "object",
            "properties": {
                "task_id": {"type": "integer"},
                "status": {
                    "type": "string",
                    "enum": [
                        "pending",
                        "not_started",
                        "in_progress",
                        "blocked",
                        "completed",
                        "archived",
                    ],
                },
                "notes": {"type": "string"},
                "verified_exit_criteria": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Required when marking a task completed. Must include every task exit criterion.",
                },
                "directory": {"type": "string"},
            },
            "required": ["task_id", "status"],
        },
        requires_approval=False,
    ),
    ToolDefinition(
        name="clear_context",
        description="Clears the current conversation history to free up space, usually done between tasks. System instructions and feature state are preserved.",
        parameters={"type": "object", "properties": {}},
        requires_approval=True,
    ),
    ToolDefinition(
        name="raise_blocker",
        description="Raises a structured blocker when the feature loop needs user input or an external decision before it can safely continue.",
        parameters={
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Short blocker summary shown to the user.",
                },
                "details": {
                    "type": "string",
                    "description": "Longer explanation of what is blocked and what has already been tried.",
                },
                "requested_input": {
                    "type": "string",
                    "description": "Describe the exact information or decision needed from the user.",
                },
                "questions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional focused questions for the user to answer.",
                },
            },
            "required": ["summary", "requested_input"],
        },
        requires_approval=False,
    ),
]

_COLLATED_TOOL_NAMES = {
    "get_workspace_details",
    "read_file",
    "search_for_string",
    "get_chunk",
    "list_dir",
    "list_agent_tasks",
    "git_status",
    "git_log",
    "git_diff",
    "git_branch",
    "url_grounding",
    "read_document",
    "get_tasks",
    "get_current_task",
}


def _default_result_mode(tool_name: str) -> str:
    return "structured+collated" if tool_name in _COLLATED_TOOL_NAMES else "structured"


def _default_server_policy(tool_name: str) -> str:
    if tool_name in {
        "flush",
        "save_memory",
        "save_scratchpad",
        "search_memory",
        "search_scratchpad",
        "list_memory",
        "list_scratchpad",
        "clear_scratchpad",
    }:
        return "session_only"
    return "allowed"


TOOL_DESCRIPTOR_OVERRIDES = {
    "get_workspace_details": {
        "execution_kind": "read",
        "preview_policy": "none",
        "summary_builder": "parse_workspace_details",
    },
    "read_file": {
        "execution_kind": "read",
        "preview_policy": "none",
        "summary_builder": "read_file_preview",
    },
    "search_for_string": {
        "execution_kind": "read",
        "preview_policy": "none",
        "summary_builder": "parse_search_results",
    },
    "get_chunk": {
        "execution_kind": "read",
        "preview_policy": "none",
        "summary_builder": "chunk_preview",
    },
    "get_current_time": {
        "execution_kind": "read",
        "preview_policy": "none",
        "result_mode": "raw",
    },
    "list_dir": {
        "execution_kind": "read",
        "preview_policy": "none",
        "summary_builder": "parse_list_dir",
    },
    "write_file": {
        "execution_kind": "mutate",
        "preview_policy": "required",
    },
    "apply_diff": {
        "execution_kind": "mutate",
        "preview_policy": "required",
    },
    "batch_job": {
        "execution_kind": "composite",
        "preview_policy": "optional",
        "result_mode": "structured",
    },
    "list_agent_tasks": {
        "execution_kind": "read",
        "preview_policy": "none",
        "summary_builder": "parse_list_agent_tasks",
    },
    "run_agent_task": {
        "execution_kind": "mutate",
        "preview_policy": "optional",
        "summary_builder": "agent_task_preview",
    },
    "git_status": {
        "execution_kind": "read",
        "preview_policy": "none",
        "summary_builder": "git_preview",
    },
    "git_log": {
        "execution_kind": "read",
        "preview_policy": "none",
        "summary_builder": "git_preview",
    },
    "git_diff": {
        "execution_kind": "read",
        "preview_policy": "none",
        "summary_builder": "git_preview",
    },
    "git_checkout": {
        "execution_kind": "mutate",
        "preview_policy": "optional",
    },
    "git_add": {
        "execution_kind": "mutate",
        "preview_policy": "optional",
    },
    "git_commit": {
        "execution_kind": "mutate",
        "preview_policy": "optional",
    },
    "git_push": {
        "execution_kind": "mutate",
        "preview_policy": "optional",
    },
    "git_pull": {
        "execution_kind": "mutate",
        "preview_policy": "optional",
    },
    "git_init": {
        "execution_kind": "mutate",
        "preview_policy": "optional",
    },
    "git_merge_request": {
        "execution_kind": "mutate",
        "preview_policy": "optional",
        "server_policy": "session_only",
    },
    "git_branch": {
        "execution_kind": "read",
        "preview_policy": "none",
        "summary_builder": "git_preview",
    },
    "url_grounding": {
        "execution_kind": "read",
        "preview_policy": "none",
    },
    "read_document": {
        "execution_kind": "read",
        "preview_policy": "none",
    },
    "flush": {
        "execution_kind": "control",
        "preview_policy": "none",
        "result_mode": "raw",
    },
    "save_memory": {
        "execution_kind": "memory",
        "preview_policy": "none",
        "result_mode": "raw",
        "server_policy": "session_only",
    },
    "save_scratchpad": {
        "execution_kind": "memory",
        "preview_policy": "none",
        "result_mode": "raw",
        "server_policy": "session_only",
    },
    "search_memory": {
        "execution_kind": "memory",
        "preview_policy": "none",
        "result_mode": "raw",
        "server_policy": "session_only",
    },
    "search_scratchpad": {
        "execution_kind": "memory",
        "preview_policy": "none",
        "result_mode": "raw",
        "server_policy": "session_only",
    },
    "list_memory": {
        "execution_kind": "memory",
        "preview_policy": "none",
        "result_mode": "raw",
        "server_policy": "session_only",
    },
    "list_scratchpad": {
        "execution_kind": "memory",
        "preview_policy": "none",
        "result_mode": "raw",
        "server_policy": "session_only",
    },
    "clear_scratchpad": {
        "execution_kind": "memory",
        "preview_policy": "none",
        "result_mode": "raw",
        "server_policy": "session_only",
    },
    "create_feature": {
        "execution_kind": "mutate",
        "preview_policy": "optional",
    },
    "create_phases": {
        "execution_kind": "mutate",
        "preview_policy": "optional",
    },
    "create_task": {
        "execution_kind": "mutate",
        "preview_policy": "optional",
    },
    "get_execution_state": {
        "execution_kind": "read",
        "preview_policy": "none",
    },
    "block_task": {
        "execution_kind": "mutate",
        "preview_policy": "optional",
    },
    "resume_task": {
        "execution_kind": "mutate",
        "preview_policy": "optional",
    },
    "review_completed_tasks": {
        "execution_kind": "mutate",
        "preview_policy": "optional",
    },
    "review_all_completed_tasks": {
        "execution_kind": "mutate",
        "preview_policy": "optional",
    },
    "propose_task_diff": {
        "execution_kind": "mutate",
        "preview_policy": "optional",
    },
    "decide_task_diff": {
        "execution_kind": "mutate",
        "preview_policy": "optional",
    },
    "archive_task": {
        "execution_kind": "mutate",
        "preview_policy": "optional",
    },
    "create_feature_task": {
        "execution_kind": "mutate",
        "preview_policy": "optional",
    },
    "update_feature_task": {
        "execution_kind": "mutate",
        "preview_policy": "optional",
    },
    "approve_feature_task": {
        "execution_kind": "mutate",
        "preview_policy": "optional",
    },
    "get_current_task": {
        "execution_kind": "read",
        "preview_policy": "none",
    },
    "get_tasks": {
        "execution_kind": "read",
        "preview_policy": "none",
    },
    "update_task_status": {
        "execution_kind": "mutate",
        "preview_policy": "optional",
    },
    "clear_context": {
        "execution_kind": "control",
        "preview_policy": "none",
        "result_mode": "raw",
        "server_policy": "session_only",
    },
    "raise_blocker": {
        "execution_kind": "control",
        "preview_policy": "none",
        "result_mode": "structured",
        "server_policy": "session_only",
        "summary_builder": "blocker_summary",
    },
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
    descriptor = TOOL_DESCRIPTORS.get(tool_name)
    return descriptor.definition if descriptor else None


def get_tool_descriptor(tool_name: str) -> ToolDescriptor | None:
    return TOOL_DESCRIPTORS.get(tool_name)


def list_tool_descriptors() -> list[ToolDescriptor]:
    return [
        TOOL_DESCRIPTORS[tool.name] for tool in TOOLS if tool.name in TOOL_DESCRIPTORS
    ]


COLLATED_TOOLS = [
    *sorted(_COLLATED_TOOL_NAMES),
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
        logger.warning(f"read_file: Access denied or file ignored: {filename}")
        return f"Error: Access denied or file ignored. '{filename}' is outside boundaries or in ignore list."
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return f"Error: File '{filename}' not found. Try using search_for_string to locate it."
    except UnicodeDecodeError:
        return f"Error: '{filename}' appears to be a binary file or has an unsupported encoding."
    except Exception as e:
        logger.error(f"read_file: Error reading {filename}: {e}")
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
        logger.warning(f"get_chunk: Access denied or file ignored: {filename}")
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
        logger.error(f"get_chunk: Error reading {filename}: {e}")
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
        logger.error(f"run_agent_task: Error executing {task_name}: {e}")
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
        logger.error(f"run_git_command: Error executing git {' '.join(args_list)}: {e}")
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
            logger.warning(f"git_diff: Access denied or path ignored: {filename}")
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
            logger.warning(f"git_add: Access denied or path ignored: {f}")
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


def url_grounding(url: str, folder_context) -> str:
    """Accesses a URL to gather additional context. Supports JavaScript-heavy websites."""
    try:
        from playwright.sync_api import sync_playwright
        from bs4 import BeautifulSoup

        with sync_playwright() as p:
            # Try to launch chromium. We use chromium as it's generally most compatible
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as e:
                return f"Error: Failed to launch browser. You may need to run 'playwright install chromium'. Details: {e}"

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

            return text
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

            return f"(Note: Playwright not installed or failed, JS-heavy content might be missing)\n\n{text}"
        except Exception as e:
            return f"Error accessing URL: {e}"


def read_document(filename: str, folder_context) -> str:
    """Reads and parses documents like PDFs to gather additional context."""
    if not _check_bounds(filename, folder_context):
        logger.warning(f"read_document: Access denied or file ignored: {filename}")
        return f"Error: Access denied or file ignored. '{filename}'"

    ext = os.path.splitext(filename)[1].lower()
    if ext == ".pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(filename)
            text = ""
            for page in reader.pages:
                extracted = page.extract_text()
                if extracted:
                    text += extracted + "\n"
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


def infer_tool_error_code(tool_name: str, result: Any) -> str | None:
    raw_text = str(result or "")
    lowered = raw_text.lower()

    if not raw_text:
        return None

    if "disabled for this session" in lowered:
        return "access_denied"
    if "access denied" in lowered or "outside boundaries" in lowered:
        return "access_denied"
    if "nested batch_job not allowed" in lowered:
        return "unsupported"
    if "unknown tool" in lowered or "tool_name missing" in lowered:
        return "not_found"
    if "field '" in lowered and "required" in lowered:
        return "invalid_args"
    if "argument is empty" in lowered or "must be a list" in lowered:
        return "invalid_args"
    if (
        "malformed patch" in lowered
        or "'patch' utility not found" in lowered
        or "patch: ****" in lowered
        or "only garbage was found in the patch input" in lowered
    ):
        return "preview_failed"
    if raw_text.startswith("Error"):
        return "execution_failed"
    return None


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


def _handle_get_workspace_details(args, folder_context, ui, variables) -> str:
    return get_workspace_details(folder_context)


def _handle_flush(args, folder_context, ui, variables) -> str:
    return "Buffer flushed."


def _handle_memory_placeholder(message: str) -> Callable[..., str]:
    def _handler(args, folder_context, ui, variables) -> str:
        return message

    return _handler


def _legacy_handler(
    handler: Callable[[dict, Any, Any, dict | None], str],
) -> Callable[[dict, ToolExecutionContext], str]:
    def _wrapped(args: dict, context: ToolExecutionContext) -> str:
        return handler(args, context.folder_context, context.ui, context.variables)

    return _wrapped


def _handle_read_file(args, folder_context, ui, variables) -> str:
    return read_file(args.get("filename", ""), folder_context)


def _handle_search_for_string(args, folder_context, ui, variables) -> str:
    return search_for_string(args.get("string", ""), folder_context)


def _handle_get_chunk(args, folder_context, ui, variables) -> str:
    return get_chunk(
        args.get("file", ""),
        args.get("start_line", 1),
        args.get("end_line", 100),
        folder_context,
    )


def _handle_get_current_time(args, folder_context, ui, variables) -> str:
    return get_current_time(folder_context)


def _handle_list_dir(args, folder_context, ui, variables) -> str:
    return list_dir(args.get("path", ""), folder_context)


def _handle_write_file(args, folder_context, ui, variables) -> str:
    return write_file(args.get("filename", ""), args.get("content", ""), folder_context)


def _handle_list_agent_tasks(args, folder_context, ui, variables) -> str:
    return list_agent_tasks(folder_context)


def _handle_run_agent_task(args, folder_context, ui, variables) -> str:
    return run_agent_task(args.get("task_name", ""), folder_context, variables)


def _handle_apply_diff(args, folder_context, ui, variables) -> str:
    return apply_diff(args.get("filename", ""), args.get("diff", ""), folder_context)


def _handle_git_status(args, folder_context, ui, variables) -> str:
    return git_status(folder_context)


def _handle_git_init(args, folder_context, ui, variables) -> str:
    return git_init(folder_context)


def _handle_git_log(args, folder_context, ui, variables) -> str:
    return git_log(args.get("limit", 10), folder_context)


def _handle_git_diff(args, folder_context, ui, variables) -> str:
    return git_diff(args.get("cached", False), args.get("filename"), folder_context)


def _handle_git_checkout(args, folder_context, ui, variables) -> str:
    return git_checkout(
        args.get("branch", ""), args.get("create", False), folder_context
    )


def _handle_git_add(args, folder_context, ui, variables) -> str:
    return git_add(args.get("files", []), folder_context)


def _handle_git_commit(args, folder_context, ui, variables) -> str:
    return git_commit(args.get("message", ""), folder_context)


def _handle_git_push(args, folder_context, ui, variables) -> str:
    return git_push(args.get("remote", "origin"), args.get("branch"), folder_context)


def _handle_git_merge_request(args, folder_context, ui, variables) -> str:
    return (
        f"Merge Request '{args.get('title')}' launched successfully!\n"
        f"Description: {args.get('description')}"
    )


def _handle_git_pull(args, folder_context, ui, variables) -> str:
    return git_pull(args.get("remote", "origin"), args.get("branch"), folder_context)


def _handle_url_grounding(args, folder_context, ui, variables) -> str:
    return url_grounding(args.get("url", ""), folder_context)


def _handle_read_document(args, folder_context, ui, variables) -> str:
    return read_document(args.get("filename", ""), folder_context)


def _handle_git_branch(args, folder_context, ui, variables) -> str:
    return git_branch(folder_context)


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

    metadata_path = session.session_manager.get_feature_metadata_path(
        feature_id or re.sub(r"[^a-zA-Z0-9]+", "_", feature_name.lower()).strip("_")
    )
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
        feature_id = feature_id or re.sub(
            r"[^a-zA-Z0-9]+", "_", feature_name.lower()
        ).strip("_")
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

    if status == "completed":
        if not isinstance(verified_exit_criteria, list):
            return "Error: verified_exit_criteria must be an array when status='completed'."
        plan_snapshot = load_feature_plan(metadata_path)
        target_task = next(
            (item for item in plan_snapshot.tasks if item.id == int(task_id)),
            None,
        )
        if target_task is None:
            return f"Error: Task {task_id} not found."
        expected = [str(item).strip() for item in target_task.exit_criteria if str(item).strip()]
        provided = {str(item).strip() for item in verified_exit_criteria if str(item).strip()}
        missing = [criterion for criterion in expected if criterion not in provided]
        if missing:
            return (
                "Error: Cannot mark task completed until all exit criteria are verified. "
                f"Missing: {missing}"
            )

    plan = update_task_status(metadata_path, task_id, status, notes)
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


def _handle_batch_job(args: dict, context: ToolExecutionContext) -> str:
    commands = args.get("commands", [])
    if not isinstance(commands, list):
        return "Error: 'commands' must be a list."

    results = []
    for i, cmd in enumerate(commands):
        if not isinstance(cmd, dict):
            results.append(f"Error: Command {i} - invalid command entry.")
            continue

        name = cmd.get("tool_name")
        t_args = cmd.get("tool_args", {})

        if not name:
            results.append(f"Error: Command {i} - tool_name missing.")
            continue

        nested_descriptor = get_tool_descriptor(name)
        if not nested_descriptor:
            results.append(f"Error: Command {i} - unknown tool: {name}")
            continue

        if nested_descriptor.execution_kind == "composite":
            results.append(f"Error: Command {i} - nested batch_job not allowed.")
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
        results.append(f"Tool: {name}\nResult: {res}")

    return (
        "--- Batch Job Results ---\n"
        + "\n\n---\n\n".join(results)
        + "\n------------------------"
    )


TOOL_HANDLERS: dict[str, Callable[[dict, ToolExecutionContext], str]] = {
    "get_workspace_details": _legacy_handler(_handle_get_workspace_details),
    "flush": _legacy_handler(_handle_flush),
    "save_memory": _legacy_handler(
        _handle_memory_placeholder("Memory save requested.")
    ),
    "save_scratchpad": _legacy_handler(
        _handle_memory_placeholder("Scratchpad save requested.")
    ),
    "search_memory": _legacy_handler(
        _handle_memory_placeholder("Memory search requested.")
    ),
    "search_scratchpad": _legacy_handler(
        _handle_memory_placeholder("Scratchpad search requested.")
    ),
    "list_memory": _legacy_handler(
        _handle_memory_placeholder("Memory listing requested.")
    ),
    "list_scratchpad": _legacy_handler(
        _handle_memory_placeholder("Scratchpad listing requested.")
    ),
    "clear_scratchpad": _legacy_handler(
        _handle_memory_placeholder("Scratchpad cleared.")
    ),
    "read_file": _legacy_handler(_handle_read_file),
    "search_for_string": _legacy_handler(_handle_search_for_string),
    "get_chunk": _legacy_handler(_handle_get_chunk),
    "get_current_time": _legacy_handler(_handle_get_current_time),
    "list_dir": _legacy_handler(_handle_list_dir),
    "write_file": _legacy_handler(_handle_write_file),
    "list_agent_tasks": _legacy_handler(_handle_list_agent_tasks),
    "run_agent_task": _legacy_handler(_handle_run_agent_task),
    "apply_diff": _legacy_handler(_handle_apply_diff),
    "git_status": _legacy_handler(_handle_git_status),
    "git_init": _legacy_handler(_handle_git_init),
    "git_log": _legacy_handler(_handle_git_log),
    "git_diff": _legacy_handler(_handle_git_diff),
    "git_checkout": _legacy_handler(_handle_git_checkout),
    "git_add": _legacy_handler(_handle_git_add),
    "git_commit": _legacy_handler(_handle_git_commit),
    "git_push": _legacy_handler(_handle_git_push),
    "git_merge_request": _legacy_handler(_handle_git_merge_request),
    "git_pull": _legacy_handler(_handle_git_pull),
    "url_grounding": _legacy_handler(_handle_url_grounding),
    "read_document": _legacy_handler(_handle_read_document),
    "git_branch": _legacy_handler(_handle_git_branch),
    "create_feature": _handle_create_feature,
    "create_phases": _handle_create_phases,
    "create_task": _handle_create_task,
    "get_execution_state": _handle_get_execution_state,
    "block_task": _handle_block_task,
    "resume_task": _handle_resume_task,
    "review_completed_tasks": _handle_review_completed_tasks,
    "review_all_completed_tasks": _handle_review_all_completed_tasks,
    "propose_task_diff": _handle_propose_task_diff,
    "decide_task_diff": _handle_decide_task_diff,
    "archive_task": _handle_archive_task,
    "create_feature_task": _handle_create_feature_task,
    "update_feature_task": _handle_update_feature_task,
    "approve_feature_task": _handle_approve_feature_task,
    "get_current_task": _handle_get_current_task,
    "get_tasks": _handle_get_tasks,
    "update_task_status": _handle_update_task_status,
    "raise_blocker": _legacy_handler(_handle_raise_blocker),
    "batch_job": _handle_batch_job,
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
    """Descriptor-backed dispatcher with argument validation."""

    descriptor = get_tool_descriptor(tool_name)
    if not descriptor:
        return f"Unknown tool: {tool_name}"

    if not isinstance(args, dict):
        return (
            f"Error: Tool '{tool_name}' arguments must be an object/dict, "
            f"got {type(args).__name__}. Please re-issue the tool call with JSON object arguments."
        )

    path_keys = ["filename", "file", "path"]
    for key in path_keys:
        if key in args and (not args[key] or str(args[key]).strip() == ""):
            return _path_arg_error(key)

    if tool_name == "apply_diff" and session is not None:
        feature_state = (
            session.session_manager.get_feature_state()
            if hasattr(session, "session_manager")
            else None
        ) or {}
        feature_plan = feature_state.get("feature_plan", {}) if isinstance(feature_state, dict) else {}
        in_review_mode = bool(feature_plan.get("tasks_completed")) and (
            str(feature_plan.get("review_status", "")).strip().lower() != "completed"
        )
        if in_review_mode:
            proposal_id = str(args.get("proposal_id", "") or "").strip()
            if not proposal_id:
                return (
                    "Error: apply_diff in review mode requires proposal_id for an approved diff proposal."
                )
            metadata_path = str(feature_state.get("metadata_path", "") or "").strip()
            if not metadata_path or not os.path.exists(metadata_path):
                return "Error: Feature metadata not found for review-mode apply_diff."
            plan = load_feature_plan(metadata_path)
            proposal = next(
                (item for item in plan.diff_proposals if item.id == proposal_id),
                None,
            )
            if proposal is None or proposal.status != "approved":
                return (
                    "Error: apply_diff blocked in review mode. "
                    "proposal_id must reference an approved diff proposal."
                )

    handler = TOOL_HANDLERS.get(descriptor.handler_key)
    if not handler:
        return f"Error: No handler registered for tool '{tool_name}'."

    context = build_tool_context(
        folder_context,
        ui,
        variables,
        invocation_source=invocation_source,
        session=session,
    )
    try:
        return handler(args, context)
    except Exception as exc:
        hint = ""
        if isinstance(exc, AttributeError) and "'str' object has no attribute 'get'" in str(
            exc
        ):
            hint = (
                " Hint: A string was used where an object was expected "
                "(commonly malformed tool arguments like tasks)."
            )
        logger.error("Tool execution failed for %s: %s", tool_name, exc, exc_info=True)
        return (
            f"Error: Tool '{tool_name}' failed with {type(exc).__name__}: {exc}."
            f"{hint} Please fix arguments and retry."
        )
