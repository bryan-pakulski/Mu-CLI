"""Per-(tool, error_code) recovery hints.

When a tool fails, the harness now attaches a `hint` to the envelope
guiding the model toward a fix. The hints live in this module so they
are:
  * easy to audit and extend (one dict, one entry per case);
  * decoupled from `core/tools.py` (so new tools registered via
    `@tool` can contribute hints without touching the registry);
  * skippable — if no specific hint matches, a generic per-code
    fallback is used.

Lookup order:
  1. Tool-specific override: `_HINTS[(tool_name, error_code)]`.
  2. Code-level fallback: `_GENERIC_HINTS[error_code]`.
  3. None (no hint attached).

`retryable_for_code(code)` reports whether the failure is worth a
single reflective retry. The default is conservative — only
`invalid_args`, `not_found`, and `preview_failed` are retryable
because each has a deterministic recovery path the model can take.
`access_denied` / `unsupported` are NOT retryable: they need a
human-visible decision, not a tool-side guess.
"""

from __future__ import annotations

from typing import Optional


_GENERIC_HINTS = {
    "not_found": (
        "The target was not found. Use `list_dir`, `search_for_string`, or "
        "`retrieve_relevant_context` to discover the correct path or symbol "
        "before retrying."
    ),
    "invalid_args": (
        "The arguments did not match the tool's schema. Re-issue the call "
        "with the exact required fields (check the tool's `parameters` "
        "definition above). For path arguments, use absolute paths or "
        "paths relative to the workspace root."
    ),
    "preview_failed": (
        "The diff or patch could not be applied as written. Re-read the "
        "target file with `read_file` to confirm current line numbers and "
        "exact context, then re-issue a unified diff with correct hunk "
        "headers (`@@ -start,len +start,len @@`). Or fall back to "
        "`search_and_replace_file` with 3-5 lines of context."
    ),
    "execution_failed": (
        "The tool returned a non-zero exit. Read the error message in "
        "`message`; if it's a transient issue (network, timeout) a second "
        "attempt may succeed. Otherwise change inputs."
    ),
    "access_denied": (
        "The path is outside the attached workspace or matches a gitignore "
        "rule. Use `get_workspace_details` to see allowed paths."
    ),
    "unsupported": (
        "The harness rejected this call shape. `batch_job` cannot nest; "
        "splice tool calls into the outer turn instead."
    ),
    "plan_mode_blocked": (
        "Plan mode is active. Gather context with read-only tools and "
        "present a plan; ask the user to disable plan mode with `/plan off` "
        "before re-attempting writes."
    ),
    "hook_denied": (
        "A user-configured hook in `.mu/hooks.json` denied this call. "
        "Read the hook's message; either adjust the call shape it doesn't "
        "like, or ask the user to update their hook configuration."
    ),
    "depth_exceeded": (
        "Sub-agent recursion limit reached. Continue the work directly in "
        "the current session instead of spawning another child."
    ),
}


# Tool-specific overrides. Use these when the generic hint isn't precise
# enough. Key is (tool_name, error_code).
_HINTS = {
    ("read_file", "not_found"): (
        "Use `list_dir` on the parent directory to find the exact filename, "
        "or `search_for_string` for a token you expect inside the file."
    ),
    ("write_file", "not_found"): (
        "The parent directory likely does not exist. Create it first with "
        "`bash` (`mkdir -p`), then re-issue write_file."
    ),
    ("apply_diff", "preview_failed"): (
        "Unified diff failed to apply. Re-read the file with `read_file` to "
        "get current line numbers, then either: (a) emit a corrected diff "
        "with accurate `@@ -start,len +start,len @@` headers, or (b) use "
        "`search_and_replace_file` with a unique 3-5-line context anchor "
        "for surgical edits."
    ),
    ("search_and_replace_file", "invalid_args"): (
        "Provide both `search` and `replace`; include 3-5 lines of unique "
        "context in `search` to disambiguate. Set `expected_count` if "
        "multiple matches are expected."
    ),
    ("bash", "execution_failed"): (
        "Inspect the stderr in `message`. For 'command not found' install "
        "the binary first; for 'permission denied' check filesystem perms "
        "or run with absolute paths."
    ),
    ("apply_diff", "invalid_args"): (
        "Both `filename` and `diff` are required. The diff must be a "
        "standard unified diff including `--- filename`, `+++ filename`, "
        "and `@@ -start,len +start,len @@` hunk headers."
    ),
    ("spawn_agent", "depth_exceeded"): (
        "You are already inside a sub-agent. Complete the work in this "
        "session — recursive spawning is capped to prevent runaway costs."
    ),
}


# Error codes the agent loop should consider for a single reflective
# retry. Codes outside this set typically need user input (access_denied,
# plan_mode_blocked) or a different strategy entirely (unsupported).
_RETRYABLE_CODES = frozenset(
    {
        "not_found",
        "invalid_args",
        "preview_failed",
        "execution_failed",
    }
)


def hint_for(tool_name: str, error_code: Optional[str]) -> Optional[str]:
    """Return the best available hint for this (tool, code), or None."""
    if not error_code:
        return None
    if (tool_name, error_code) in _HINTS:
        return _HINTS[(tool_name, error_code)]
    return _GENERIC_HINTS.get(error_code)


def retryable_for_code(error_code: Optional[str]) -> bool:
    """Should the harness offer one reflective retry for this code?"""
    if not error_code:
        return False
    return error_code in _RETRYABLE_CODES


__all__ = ["hint_for", "retryable_for_code"]
