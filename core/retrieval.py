"""Backward-compatible re-export shim.

The body of this module moved to `mu/retrieval/index.py` during the
Phase 6 namespace rename. New code should import from ``mu.retrieval``.
"""

from mu.retrieval.index import *  # noqa: F401,F403
from mu.retrieval.index import IndexedDocument, SemanticCodeIndex  # noqa: F401
