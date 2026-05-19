"""Shared pytest fixtures for test isolation.

- Resets SemanticCodeIndex singleton between tests to prevent cross-test state leaks.
- Cleans up FolderContext snapshots to prevent memory accumulation.
- Scrubs any `documentation/feature_req_*` or `courses/*` directories
  created during the session — feature/teacher engines resolve their
  workspace root from `os.getcwd()` when `folder_context=None`, which
  in CI is the repo root, so tests that don't explicitly chdir end up
  polluting the working tree.
"""
import glob
import os
import shutil
from pathlib import Path

import pytest
from mu.retrieval.index import SemanticCodeIndex
from mu.retrieval.index import RETRIEVAL_INDEX as _RETRIEVAL_INDEX
from mu.workspace.folder_context import FolderContext


# Directories the feature / teacher engines may create relative to cwd
# when no folder_context is attached. The session-end cleanup walks the
# repo and removes anything in these patterns that wasn't present at
# session start.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEST_ARTIFACT_PATTERNS = (
    "documentation/feature_req_*",
    "courses",
    ".mucli",
)


def _snapshot_artifacts() -> set[str]:
    found: set[str] = set()
    for pattern in _TEST_ARTIFACT_PATTERNS:
        for path in glob.glob(str(_REPO_ROOT / pattern)):
            found.add(path)
    return found


@pytest.fixture(scope="session", autouse=True)
def _cleanup_repo_test_artifacts():
    """Session-end safety net: remove any feature/teacher artifacts that
    weren't on disk before the suite ran.

    Existing artifacts (manual development state) are preserved — we
    only delete what tests created. Belt-and-suspenders alongside the
    per-module autouse chdir fixtures that prevent the writes in the
    first place."""
    pre_existing = _snapshot_artifacts()
    yield
    post = _snapshot_artifacts()
    for path in sorted(post - pre_existing):
        try:
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            elif os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass


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