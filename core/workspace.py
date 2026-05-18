"""Backward-compatible re-export shim.

The body of this module moved to `mu/workspace/folder_context.py` during
the Phase 6 namespace rename. New code should import from
``mu.workspace``.
"""

from mu.workspace.folder_context import *  # noqa: F401,F403
from mu.workspace.folder_context import (  # noqa: F401
    MAX_FILES_TO_LOAD,
    MAX_FILE_SIZE_BYTES,
    FolderContext,
)
