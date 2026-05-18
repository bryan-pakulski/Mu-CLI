"""Workspace inspector tools.

Seven `@tool`-registered read-only handlers for inspecting the attached
workspace folders:

  * `get_workspace_details` — tree map of all attached folders
  * `read_file`              — read a whole file (sandbox-gated)
  * `search_for_string`      — exact-string grep across the workspace
  * `search_references`      — same, but returns context snippets
  * `retrieve_relevant_context` — semantic retrieval via SemanticCodeIndex
  * `get_chunk`              — read a line range of a file
  * `list_dir`               — list files in a directory

All seven implementations + their `@tool` registrations live in
`handlers.py` (positional-signature bodies + thin `_<name>_tool(args,
context)` wrappers carrying the registration metadata).
"""

from . import handlers  # noqa: F401 — registers 7 tools at import time

__all__: list = []
