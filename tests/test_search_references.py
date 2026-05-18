import os
import json
import pytest
from mu.tools.workspace.handlers import search_references
import mu.tools as _mu_tools
from mu.workspace.folder_context import FolderContext


def test_basic_search_with_context(tmp_path):
    """search_references returns matches with filepath, line_number, and context_snippet."""
    ctx = FolderContext()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ctx.add_folder(str(workspace))

    # Create a file with a unique marker
    src = workspace / "example.py"
    src.write_text("line 1\nline 2\nTARGET_STRING\nline 4\nline 5\n")

    result = search_references("TARGET_STRING", ctx)
    payload = json.loads(result)

    assert payload["count"] == 1
    match = payload["results"][0]
    assert str(workspace) in match["filepath"]
    assert match["line_number"] == 3
    assert "TARGET_STRING" in match["context_snippet"]
    # Context lines should include surrounding lines
    assert "line 2" in match["context_snippet"]
    assert "line 4" in match["context_snippet"]


def test_no_matches(tmp_path):
    """search_references returns empty results when query not found."""
    ctx = FolderContext()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ctx.add_folder(str(workspace))

    src = workspace / "example.py"
    src.write_text("nothing relevant here\n")

    result = search_references("NONEXISTENT_QUERY_XYZ", ctx)
    payload = json.loads(result)

    assert payload["count"] == 0
    assert payload["results"] == []


def test_context_lines_parameter(tmp_path):
    """search_references respects context_lines parameter."""
    ctx = FolderContext()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ctx.add_folder(str(workspace))

    lines = [f"line {i}" for i in range(10)]
    lines[5] = "TARGET"
    src = workspace / "multi.py"
    src.write_text("\n".join(lines) + "\n")

    # context_lines=0 should show only the matching line
    result = search_references("TARGET", ctx, context_lines=0)
    payload = json.loads(result)
    snippet = payload["results"][0]["context_snippet"]
    assert snippet.strip() == "TARGET"

    # context_lines=1 should show one line before and after
    result = search_references("TARGET", ctx, context_lines=1)
    payload = json.loads(result)
    snippet = payload["results"][0]["context_snippet"]
    assert "line 4" in snippet
    assert "TARGET" in snippet
    assert "line 6" in snippet


def test_binary_file_skipping(tmp_path):
    """search_references skips files that cannot be decoded as text."""
    ctx = FolderContext()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ctx.add_folder(str(workspace))

    # Write a binary file
    binary = workspace / "data.bin"
    binary.write_bytes(b"\x00\x01\x02\xff\xfe\xfdTARGET\x80\x90")

    # Also write a text file with the same marker
    text = workspace / "readme.txt"
    text.write_text("Found TARGET here\n")

    result = search_references("TARGET", ctx)
    payload = json.loads(result)

    # Should find at least the text file; binary file may or may not match
    # but should not crash
    assert payload["count"] >= 1
    # Text file match must be present
    text_matches = [m for m in payload["results"] if m["filepath"].endswith("readme.txt")]
    assert len(text_matches) == 1


def test_empty_query(tmp_path):
    """search_references returns error for empty query."""
    ctx = FolderContext()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ctx.add_folder(str(workspace))

    result = search_references("", ctx)
    payload = json.loads(result)
    assert "error" in payload


def test_no_folder_context():
    """search_references returns error when no folder context."""
    result = search_references("anything", None)
    payload = json.loads(result)
    assert "error" in payload


def test_handler_delegates_correctly(tmp_path):
    """The `@tool`-registered `search_references` handler forwards args
    to the underlying implementation. Post-migration the handler lives
    in `mu/tools/workspace/handlers.py`; we exercise it through the
    public `mu.tools.execute` dispatcher."""
    ctx = FolderContext()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ctx.add_folder(str(workspace))

    src = workspace / "handler_test.py"
    src.write_text("line a\nFINDME\nline c\n")

    tool_ctx = _mu_tools.build_tool_context(folder_context=ctx, ui=None, variables={})
    envelope = _mu_tools.execute(
        "search_references", {"query": "FINDME", "context_lines": 1}, tool_ctx
    )
    assert envelope["ok"] is True
    payload = json.loads(envelope["message"])

    assert payload["count"] == 1
    assert "FINDME" in payload["results"][0]["context_snippet"]