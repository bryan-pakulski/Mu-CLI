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

The underlying implementations still live in `core/tools.py` for now;
this module hosts the descriptors and `@tool` registrations so the new
agent loop sees them via `mu.tools` rather than the legacy bridge.
"""

from . import handlers  # noqa: F401 — registers 7 tools at import time

__all__: list = []
