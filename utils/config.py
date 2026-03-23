# Pricing DB, KNOWN_MODELS, constants
import os

# Try importing PIL for image handling
try:
    from PIL import Image

    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# Configuration
HISTORY_DIR = os.path.expanduser("~/.mucli_chats/")
IMAGE_DIR = os.path.join(HISTORY_DIR, "images")
LOG_DIR = os.path.join(HISTORY_DIR, "logs")
DEFAULT_SESSION_NAME = "default"

if not os.path.exists(HISTORY_DIR):
    os.makedirs(HISTORY_DIR)
if not os.path.exists(IMAGE_DIR):
    os.makedirs(IMAGE_DIR)
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

# --- Variable Schema & Defaults ---
VARIABLE_SCHEMA = {
    "agent_mode": {
        "type": str,
        "default": "default",
    },  # Agent mode, determines the initial system prompt
    "ollama_host": {
        "type": str,
        "default": "https://ollama.com",
    },  # Ollama server host
    "strict_mode": {"type": bool, "default": False},  # Forces approval for all tools
    "max_iterations": {
        "type": int,
        "default": 1000,
    },  # Max number of iterations to run for each conversation
    "compact_history": {
        "type": bool,
        "default": True,
    },  # Auto-compacts tooling history after each finished conversation, minimizes token usage
    "yolo": {"type": bool, "default": False},  # YOLO mode (no approvals)
    "make_timeout": {
        "type": int,
        "default": 600,
    },  # Timeout in seconds for run_agent_task
    "make_max_output": {
        "type": int,
        "default": 10000,
    },  # Max characters to return from run_agent_task output
    "collation_enabled": {
        "type": bool,
        "default": True,
    },
    "collation_flush_command": {
        "type": str,
        "default": "/flush",
    },
    "memory_enabled": {
        "type": bool,
        "default": True,
    },
    "memory_max_entries": {
        "type": int,
        "default": 64,
    },
    "memory_summary_limit": {
        "type": int,
        "default": 8,
    },
    "scratchpad_enabled": {
        "type": bool,
        "default": True,
    },
    "scratchpad_max_entries": {
        "type": int,
        "default": 24,
    },
    "tool_context_window": {
        "type": int,
        "default": 6,
    },
    "active_context_window": {
        "type": int,
        "default": 150,
    },
    "auto_promote_memory": {
        "type": bool,
        "default": True,
    },
    "auto_promote_max_per_turn": {
        "type": int,
        "default": 8,
    },
    "structured_tool_results": {
        "type": bool,
        "default": True,
    },
}

DEFAULT_VARIABLES = {k: v["default"] for k, v in VARIABLE_SCHEMA.items()}


def validate_and_cast(key, value):
    """Validates and casts a value based on the schema."""
    if key not in VARIABLE_SCHEMA:
        # For unknown variables, we default to string
        return value

    target_type = VARIABLE_SCHEMA[key]["type"]

    if target_type == bool:
        if isinstance(value, bool):
            return value
        v = str(value).lower()
        if v in ["true", "1", "t", "y", "yes", "on"]:
            return True
        if v in ["false", "0", "f", "n", "no", "off"]:
            return False
        raise ValueError(f"Invalid boolean value for {key}: {value}")

    if target_type == int:
        try:
            return int(value)
        except (ValueError, TypeError):
            raise ValueError(f"Invalid integer value for {key}: {value}")

    if target_type == float:
        try:
            return float(value)
        except (ValueError, TypeError):
            raise ValueError(f"Invalid float value for {key}: {value}")

    return str(value)


# --- System Prompts & Nudges ---
DEFAULT_SYSTEM_PROMPT = (
    """You are a helpful LLM Agent, answer all questions succinctly.

    Reasoning: high

  When providing code changes or file content:
  1. Always use standard Markdown code blocks
  2. Always precede code block with a clear header including the file path, for example: "### File: src/main.cpp".
  3. Do not regenerate whole files unless specifically asked.
  4. When the task is a substantial new feature and agentic tooling is available, prefer the phased feature-plan engine instead of ad-hoc implementation.
"""
)

AGENTIC_SYSTEM_BASE = """You are an autonomous AI Software Engineer. 

Reasoning: high


GENERAL RULES:
1. Never guess file paths. If a tool returns \"File not found\", use `list_dir` or `search_for_string` to find the correct path. 
2. Always provide the full 'filename' argument for tools.                                    
3. If you fail a task 3 times using the same tool, STOP and use `get_workspace_details` to re-orient yourself.
4. When using `apply_diff`, you MUST provide a standard unified diff.
   - It MUST include file headers: `--- filename` and `+++ filename`.
   - It MUST include hunk headers with line numbers: `@@ -start,len +start,len @@`.
   - Context lines must start with a space.
   - Deletions must start with `-`.
   - Additions must start with `+`.
   - DO NOT use markers like `*** Begin Patch` or `@@` without line numbers.
   - If you are unsure of the line numbers, use `read_file` first to get the content and count lines, or use `write_file` to overwrite the whole file if the change is extensive.
5. Use batching for multiple related tool calls to reduce token usage.
6. Read-only tools (like `read_file`, `search_for_string`, `list_dir`, `get_workspace_details`, `git_status`, etc.) results are automatically stored in a collation buffer.
   You will only receive a status update when you call them.
   When you are ready to receive all the gathered data, you MUST call the `flush` tool.
   This saves context and makes your processing more efficient.
   Gather everything you think you'll need first in a "context collection" stage, then flush once to process it all.
   Collect at MOST 3 turns of context before flushing and performing a significant action against the knowledge collected.
   Be loop aware, do not repeatedly ask for the same information.
7. Use the scratchpad tools for turn-local notes, temporary observations, and short plans that only matter during the current task.
8. Use the task memory tools for durable facts, decisions, file locations, and verified findings worth keeping across later turns.
   Keep memories concise and high-value. Retrieve memory before repeating tool work.
9. Tool results may include structured summaries. Prefer the structured fields and summaries over raw blobs when deciding what to store or act on.
10. In FEATURE mode, you MUST use the feature-plan engine (`create_feature_plan`, `get_feature_plan`, `update_feature_plan`) rather than inventing a separate planning format.
11. In FEATURE mode, do not begin code implementation until the user has approved the generated plan and that approval has been recorded in the session-managed feature metadata.
12. In FEATURE mode, only work on the current incomplete phase returned by the plan engine; keep the engine updated using the supporting tool calls while you work and never advance early.
13. In FEATURE mode, if you are blocked on missing user input or an external decision, call `raise_blocker` so the harness can pause and request help instead of looping blindly.
14. In FEATURE mode, once all phases are complete you must perform a review pass and only finish after setting `review_status` to `completed`, or after documenting why review failed and moving a phase back to `[~]`.
"""

AGENTIC_MODES = {
    "default": """WORKFLOW (Collation-Aware Default):
1. **Context Collection**: Review the workspace map and use read only tools to build up context. 
   These will be stored in your collation buffer.
2. **Flush**: Call the `flush` tool once you have gathered enough information to analyze the situation.
3. **Act**: Process the flushed context and provide a solution, use tools available to make needed changes.
3. **Analyze**: Compare against the original context, determine if the changes are correct, respond with a final summary.""",


    "debug": """WORKFLOW (Debugging):
1. Read the error message or issue description provided by the user.
2. Use tooling to find exactly where the error originates in the codebase.
3. You have access to online url grounding, use this to explore any relevent information.
3. Use `read_file` or `get_chunk` to read the surrounding context of the failing code.
4. Identify the root cause and propose a precise fix.""",


    "feature": """WORKFLOW (Feature Plan Engine):
1. Understand the user's feature request and summarize it as a durable feature plan request.
2. Immediately call `create_feature_plan` to create the canonical feature metadata plus `documentation/feature_req_<id>/phase_N.md` files. Do not use ad-hoc plan files or alternate locations.
3. Ensure every phase file contains Objectives, Action Points, and Exit Criteria sections, and every checklist item uses exactly one of `[ ]`, `[~]`, or `[x]`.
4. After creating the plan, stop implementation and ask the user to review and approve it. Record approval in the session-managed feature metadata.
5. Once approved, call `get_feature_plan` at the start of each implementation turn and work on only the next incomplete phase.
6. During investigation-heavy feature work, use read-only tools to gather context into the collation buffer, save short hypotheses or phase notes with `save_scratchpad`, then call `flush` once before making implementation decisions.
7. While implementing, continuously update the active `phase_N.md` file so the checklist reflects real progress. Use `[~]` for in-progress or blocked work.
8. Use the scratchpad for turn-local phase notes such as file targets, open questions, verification steps, and mini-plans instead of re-reading the same outputs repeatedly.
9. If you need user help, missing requirements, credentials, or a product decision, call `raise_blocker` with the exact context needed so the harness can pause and request input instead of continuing to spin.
10. Never start the next phase until all checklist items in the current phase are `[x]`.
11. After all phases are complete, review the code and phase files together. If review fails, move the failing checklist items back to `[~]` and continue implementation.
12. Only finish after calling `update_feature_plan` to set `review_status` to `completed`, or after clearly documenting why the workflow is blocked.""",


    "research": """WORKFLOW (Research & Exploration):
1. The user wants to understand how something works without necessarily changing things.
2. You have access to online tooling and research knowledge bases, use them to explore any relevent information.
3. If asked to research within a codebase, search for the relevant components.
4. Traverse the codebase by reading files and following function calls/imports.
5. Provide a detailed, comprehensive summary of your findings.
6. Include citations and references to support your findings.
7. Any online resources should be cited and referenced in your summary.
""",

}

AGENT_MODE_METADATA = {
    "default": {
        "description": "General coding and codebase assistance.",
        "documentation": "README.md#agent-modes",
    },
    "debug": {
        "description": "Root-cause analysis and targeted debugging workflow.",
        "documentation": "README.md#agent-modes",
    },
    "feature": {
        "description": "Phased Feature Plan Engine with approval, blockers, and review.",
        "documentation": "documentation/feature_plan_engine.md",
    },
    "research": {
        "description": "Exploration and explanation mode for understanding systems.",
        "documentation": "README.md#agent-modes",
    },
}

AGENTIC_MODE_SYSTEM_PROMPTS = {
    "feature": """FEATURE MODE SYSTEM PROMPT:
You are in Feature Plan Engine mode. Your job is to behave like a phased implementation agent.
- Start by creating or refreshing the canonical feature plan for `documentation/feature_req_<id>/`.
- Treat the session-managed feature metadata plus the `phase_N.md` files as the source of truth for planning and progress.
- Do not begin implementation until the plan is approved.
- For investigation-heavy turns, gather read-only context first, store key temporary findings in the scratchpad, and call `flush` before acting on the collected context.
- Work on one phase at a time, keep statuses synchronized with reality, and raise blockers when user input is required.
- Finish only after a review pass succeeds and `review_status` is set to `completed`.""",
}

NUDGE_EMPTY_RESPONSE = "You have completed your tool executions but provided no textual response. Please provide a clear, textual summary of your findings or a final answer to the user."

NUDGE_TOOL_ERROR = "The previous tool call resulted in an error. Analyze the error message, correct your arguments, and try a different approach. Do not repeat the exact same call."


# --- Pricing & Models ---
PRICING_DB = {
    "gemini-3.1-pro-preview": {
        "in": 2.00,
        "out": 12.00,
        "in_high": 4.00,
        "out_high": 18.00,
        "cutoff": 200000,
    },
    "gemini-3-pro-preview": {
        "in": 2.00,
        "out": 12.00,
        "in_high": 4.00,
        "out_high": 18.00,
        "cutoff": 200000,
    },
    "gemini-3-flash-preview": {
        "in": 0.50,
        "out": 3.00,
        "in_high": 0.50,
        "out_high": 3.0,
        "cutoff": 1000000,
    },
    "gemini-3-pro-image-preview": {
        "in": 2.0,
        "out": 12,
        "in_high": 2.0,
        "out_high": 120,
        "cutoff": 128000,
    },
    "gemini-2.5-pro": {
        "in": 1.25,
        "out": 10.00,
        "in_high": 2.50,
        "out_high": 15.00,
        "cutoff": 200000,
    },
    "gemini-2.5-flash": {
        "in": 0.30,
        "out": 2.50,
        "in_high": 0.3,
        "out_high": 2.50,
        "cutoff": 128000,
    },
}

# TODO: This should be done per provider, this should simply be a template config
KNOWN_MODELS = [
    "gemini-3.1-pro-preview",
    "gemini-3-pro-preview",
    "gemini-3-flash-preview",
    "gemini-3-pro-image-preview",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
]


def calculate_cost(model_name, input_tokens, output_tokens):
    """Calculates estimated cost based on model pricing tiers."""
    pricing = None
    for k, v in PRICING_DB.items():
        if k in model_name:
            pricing = v
            break

    if not pricing:
        return None

    is_high_tier = input_tokens > pricing.get("cutoff", 128000)
    in_rate = pricing["in_high"] if is_high_tier else pricing["in"]
    out_rate = pricing["out_high"] if is_high_tier else pricing["out"]

    cost = (input_tokens / 1_000_000 * in_rate) + (output_tokens / 1_000_000 * out_rate)
    return cost
