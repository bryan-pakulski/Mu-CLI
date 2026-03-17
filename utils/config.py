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
DEFAULT_SESSION_NAME = "default"

if not os.path.exists(HISTORY_DIR):
    os.makedirs(HISTORY_DIR)
if not os.path.exists(IMAGE_DIR):
    os.makedirs(IMAGE_DIR)

# --- Variable Schema & Defaults ---
VARIABLE_SCHEMA = {
    "agent_mode": {"type": str, "default": "default"},
    "ollama_host": {"type": str, "default": "http://localhost:11434"},
    "auto_approve": {"type": bool, "default": True},
    "max_iterations": {"type": int, "default": 50},
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
    """You are a helpful assistant, answer all questions succinctly.
  When providing code changes or file content:
  1. Always use standard Markdown 6-double-quote code blocks ("""
    """language ... """
    """).
  2. For code modifications/diffs, use the same code block style as point .1
  3. For new files or partial snippets, use the specific language tag (e.g., 'python', 'cpp')
  4. Always precede the code block with a clear header including the file path, for example: "### File: src/main.cpp".
  5. Only provide the new code or specific changes; do not regenerate whole files unless specifically asked.
"""
)

AGENTIC_SYSTEM_BASE = """You are an autonomous AI programming agent. You have access to tools to explore the user's workspace.
AVAILABLE TOOLS:
{tool_descriptions}
GENERAL RULES:
1. NEVER guess file paths or content. ALWAYS use your tools to discover and read files.
2. If a file is large, use `get_chunk` to read specific lines instead of the whole file.
3. If a tool returns an error, read the error carefully and try a different approach (e.g., search for a string instead of guessing a filename).
4. Do no overwrite existing files, only update necessary parts using patch tooling.
5. Once you have enough context, stop using tools and provide your final response to the user.
"""

AGENTIC_MODES = {
    "default": """WORKFLOW (Default):
1. Review the provided workspace map to understand the project structure.
2. Use `search_for_string` or `read_file` to drill down into the specific files mentioned or implied by the user.
3. Analyze the code.
4. Provide your solution or answer.""",

    "debug": """WORKFLOW (Debugging):
1. Read the error message or issue description provided by the user.
2. Use `search_for_string` to find exactly where the error originates in the codebase.
3. You have access to online tooling and research knowledge bases, use them to explore any relevent information.
3. Use `read_file` or `get_chunk` to read the surrounding context of the failing code.
4. Identify the root cause and propose a precise fix.""",

    "feature": """WORKFLOW (New Feature):
1. Understand the new feature request.
2. Create a thorough implementation plan that includes the design and architecture of the new feature, split into actionable tasks, this should be saved as a markdown file in the workspace - FEATURE_<feature_name>.md.
3. Use the workspace map and `search_for_string` to identify integration points (e.g., where routes, models, or UI components are defined).
4. Use `read_file` to understand the interfaces and patterns of existing code.
5. Write the new code following the existing project architecture, ensure it is maintainable and follows best practices.
6. Ensure that the new feature is well tested and has sufficient documentatation""",

    "research": """WORKFLOW (Research & Exploration):
1. The user wants to understand how something works without necessarily changing things.
2. You have access to online tooling and research knowledge bases, use them to explore any relevent information.
3. If asked to research within a codebase, search for the relevant components.
4. Traverse the codebase by reading files and following function calls/imports.
5. Provide a detailed, comprehensive summary of your findings.
6. Include citations and references to support your findings.
7. Any online resources should be cited and referenced in your summary.""",
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
