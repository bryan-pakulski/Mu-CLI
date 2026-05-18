"""Backward-compatible re-export shim.

The body of this module moved to `mu/memory/stores.py` during the
Phase 6 namespace rename. New code should import from ``mu.memory``.
"""

from mu.memory.stores import *  # noqa: F401,F403
from mu.memory.stores import (  # noqa: F401
    BaseNoteStore,
    MemoryEntry,
    ScratchpadStore,
    TaskMemoryStore,
)
