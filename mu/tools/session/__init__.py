"""Session-level tools (history search).

Read-only `@tool` handler for searching conversation history via
`SessionManager.search_history(...)`.
"""

from . import handlers  # noqa: F401 — registers search_history at import time

__all__: list = []