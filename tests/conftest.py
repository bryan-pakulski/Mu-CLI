"""Shared pytest fixtures for test isolation.

- Resets SemanticCodeIndex singleton between tests to prevent cross-test state leaks.
- Cleans up FolderContext snapshots to prevent memory accumulation.
"""
import pytest
from mu.retrieval.index import SemanticCodeIndex
from mu.retrieval.index import RETRIEVAL_INDEX as _RETRIEVAL_INDEX
from mu.workspace.folder_context import FolderContext


@pytest.fixture(autouse=True)
def _reset_global_singletons():
    """Reset module-level singletons that accumulate state across tests.

    The SemanticCodeIndex singleton (_RETRIEVAL_INDEX in mu.tools) indexes
    workspace files and caches them in .documents. Without resetting, state
    from one test leaks into the next, causing memory growth and flaky tests.
    """
    _RETRIEVAL_INDEX.reset()
    yield
    _RETRIEVAL_INDEX.reset()


@pytest.fixture(autouse=True)
def _cleanup_folder_context():
    """Clear FolderContext state between tests to prevent memory accumulation.

    FolderContext tracks all live instances in _instances. Without cleanup,
    snapshots and folder references accumulate across tests, causing memory
    growth and potential hangs.
    """
    yield
    FolderContext.reset_all()