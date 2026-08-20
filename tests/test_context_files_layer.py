"""Tests for L1A — Workspace context files layer.

Tests the whole-file-or-skip behavior: files that fit remaining budget
are included in full; files that exceed budget are skipped with a marker.
No truncation — middle content never lost.
"""
import os
import tempfile
from unittest.mock import MagicMock, patch

from mu.session.session import Session

import pytest


@pytest.fixture
def session_with_folder():
    """Create a session with a temp workspace folder and mocked variables."""
    tmpdir = tempfile.mkdtemp()
    session = MagicMock()
    session.folder_context = MagicMock()
    session.folder_context.folders = [tmpdir]
    session.variables = {"context_files_max_chars": 8000}
    yield session, tmpdir
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)


def _make_file(folder, name, content):
    path = os.path.join(folder, name)
    with open(path, "w") as f:
        f.write(content)
    return path


def test_discovers_agents_md(session_with_folder):
    """AGENTS.md is discovered and included."""
    session_obj, folder = session_with_folder
    _make_file(folder, "AGENTS.md", "# Project rules\nUse 4-space indent.")
    result = Session._build_context_files_block(session_obj)
    assert "AGENTS.md" in result
    assert "Use 4-space indent" in result


def test_discovers_claude_md(session_with_folder):
    """CLAUDE.md is discovered."""
    session_obj, folder = session_with_folder
    _make_file(folder, "CLAUDE.md", "# Claude rules\nAlways test.")
    result = Session._build_context_files_block(session_obj)
    assert "CLAUDE.md" in result
    assert "Always test." in result


def test_first_match_wins(session_with_folder):
    """When AGENTS.md and CLAUDE.md both exist, only AGENTS.md is included."""
    session_obj, folder = session_with_folder
    _make_file(folder, "AGENTS.md", "# AGENTS.md content")
    _make_file(folder, "CLAUDE.md", "# CLAUDE.md content")
    result = Session._build_context_files_block(session_obj)
    assert "AGENTS.md content" in result
    assert "CLAUDE.md content" not in result


def test_falls_back_to_claude_md(session_with_folder):
    """When no AGENTS.md, CLAUDE.md is used."""
    session_obj, folder = session_with_folder
    _make_file(folder, "CLAUDE.md", "# CLAUDE.md fallback")
    result = Session._build_context_files_block(session_obj)
    assert "CLAUDE.md fallback" in result


def test_falls_back_to_mucli_md(session_with_folder):
    """When no AGENTS.md or CLAUDE.md, MUCLI.md is used."""
    session_obj, folder = session_with_folder
    _make_file(folder, "MUCLI.md", "# MUCLI.md specific")
    result = Session._build_context_files_block(session_obj)
    assert "MUCLI.md specific" in result


def test_falls_back_to_context_md(session_with_folder):
    """When no top-level files, .mu/CONTEXT.md is used."""
    session_obj, folder = session_with_folder
    os.makedirs(os.path.join(folder, ".mu"), exist_ok=True)
    _make_file(os.path.join(folder, ".mu"), "CONTEXT.md", "# Context content")
    result = Session._build_context_files_block(session_obj)
    assert "Context content" in result


def test_no_files_returns_empty(session_with_folder):
    """No context files → empty string."""
    session_obj, folder = session_with_folder
    result = Session._build_context_files_block(session_obj)
    assert result == ""


def test_whole_file_or_skip(session_with_folder):
    """File exceeding budget is skipped with marker, not truncated."""
    session_obj, folder = session_with_folder
    session_obj.variables["context_files_max_chars"] = 100
    _make_file(folder, "AGENTS.md", "A" * 200)
    result = Session._build_context_files_block(session_obj)
    assert "[skipped:" in result
    assert "200 chars" in result
    assert "A" * 200 not in result


def test_small_file_fits(session_with_folder):
    """Small file fits within budget and is included in full."""
    session_obj, folder = session_with_folder
    session_obj.variables["context_files_max_chars"] = 500
    _make_file(folder, "AGENTS.md", "Short file content")
    result = Session._build_context_files_block(session_obj)
    assert "Short file content" in result
    assert "[skipped:" not in result


def test_budget_shared_across_folders(session_with_folder):
    """Budget is shared — first folder consumes budget, second may not fit."""
    import shutil
    session_obj, folder = session_with_folder
    folder2 = tempfile.mkdtemp()
    session_obj.folder_context.folders = [folder, folder2]
    session_obj.variables["context_files_max_chars"] = 100
    _make_file(folder, "AGENTS.md", "A" * 80)
    _make_file(folder2, "AGENTS.md", "B" * 80)
    result = Session._build_context_files_block(session_obj)
    assert "A" * 80 in result
    assert "[skipped:" in result
    shutil.rmtree(folder2, ignore_errors=True)


def test_dedup_by_content_hash(session_with_folder):
    """Identical files across folders are deduplicated."""
    import shutil
    session_obj, folder = session_with_folder
    folder2 = tempfile.mkdtemp()
    session_obj.folder_context.folders = [folder, folder2]
    content = "# Same content everywhere"
    _make_file(folder, "AGENTS.md", content)
    _make_file(folder2, "AGENTS.md", content)
    result = Session._build_context_files_block(session_obj)
    assert result.count(content) == 1
    shutil.rmtree(folder2, ignore_errors=True)


def test_budget_zero_disables(session_with_folder):
    """Budget=0 disables L1A entirely."""
    session_obj, folder = session_with_folder
    session_obj.variables["context_files_max_chars"] = 0
    _make_file(folder, "AGENTS.md", "Should not appear")
    result = Session._build_context_files_block(session_obj)
    assert result == ""


def test_empty_file_skipped(session_with_folder):
    """Empty or whitespace-only files are skipped."""
    session_obj, folder = session_with_folder
    _make_file(folder, "AGENTS.md", "   \n  \n  ")
    result = Session._build_context_files_block(session_obj)
    assert result == ""


def test_provenance_header(session_with_folder):
    """Each file gets a provenance header."""
    session_obj, folder = session_with_folder
    _make_file(folder, "AGENTS.md", "# My Project")
    result = Session._build_context_files_block(session_obj)
    assert "## AGENTS.md" in result


def test_multiple_folders(session_with_folder):
    """Multiple workspace folders each get their own context file."""
    import shutil
    session_obj, folder = session_with_folder
    folder2 = tempfile.mkdtemp()
    session_obj.folder_context.folders = [folder, folder2]
    _make_file(folder, "AGENTS.md", "# Folder 1 rules")
    _make_file(folder2, "CLAUDE.md", "# Folder 2 rules")
    result = Session._build_context_files_block(session_obj)
    assert "Folder 1 rules" in result
    assert "Folder 2 rules" in result
    shutil.rmtree(folder2, ignore_errors=True)


def test_caching_pattern(session_with_folder):
    """The _build_context_files_block method can be called and cached."""
    session_obj, folder = session_with_folder
    _make_file(folder, "AGENTS.md", "# Cached content")
    result1 = Session._build_context_files_block(session_obj)
    result2 = Session._build_context_files_block(session_obj)
    assert result1 == result2
    assert "Cached content" in result1


def test_middle_content_preserved(session_with_folder):
    """Critical: middle content of a file is never lost (no truncation)."""
    session_obj, folder = session_with_folder
    session_obj.variables["context_files_max_chars"] = 10000
    content = "# Header\n\nIMPORTANT MIDDLE CONTENT\n\n# Footer"
    _make_file(folder, "AGENTS.md", content)
    result = Session._build_context_files_block(session_obj)
    assert "IMPORTANT MIDDLE CONTENT" in result
    assert "# Header" in result
    assert "# Footer" in result
