"""Agent-facing prompts to the user.

`ask_user_choice` lets the agent pose a multiple-choice question with
an optional select-all-that-apply mode. The UI captures the user's
selection synchronously and returns it as a structured result. Useful
for quizzes (teacher mode) and disambiguation prompts (default mode
asking which of several plausible files to edit).
"""

from mu.tools.prompt import handlers as _handlers  # noqa: F401

__all__: list[str] = []
