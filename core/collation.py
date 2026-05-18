"""Backward-compatible re-export shim.

The body of this module moved to `mu/agent/collation.py` during the
Phase 6 namespace rename. Imports of the form
``from core.collation import CollationBuffer`` keep working through this
re-export. New code should import from ``mu.agent.collation``.
"""

from mu.agent.collation import *  # noqa: F401,F403
from mu.agent.collation import CollationBuffer  # noqa: F401
