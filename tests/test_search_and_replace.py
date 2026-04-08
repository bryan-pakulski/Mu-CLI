"""
Comprehensive unit tests for search_and_replace_file function.

Tests cover:
- Single match scenarios
- Multiple matches with disambiguation
- No match scenarios
- Empty search string
- expected_count parameter validation
- Context disambiguation
- normalize_whitespace parameter
- dry_run preview mode
- Binary file handling
- Boundary enforcement
- File encoding handling
"""

import os
import pytest
import json
from core.tools import execute_tool, TOOL_HANDLERS
from core.workspace import FolderContext


class TestSearchAndReplaceSingleMatch:
    """Tests for single match scenarios."""

    def test_single_match_replaces_correctly(self, tmp_path):
        """Test that a single match is replaced correctly."""
        ctx = FolderContext()
        ctx.add_folder(str(tmp_path))
        
        test_file = tmp_path / "test.py"
        test_file.write_text("def hello():\n    print('hello')\n")
        
        result = execute_tool(
            "search_and_replace_file",
            {
                "filename": str(test_file),
                "search": "print('hello')",
                "replace": "print('goodbye')"
            },
            ctx
        )
        
        payload = json.loads(result)
        assert payload["success"] is True
        assert payload["matches_found"] == 1
        assert payload["modified"] is True
        assert "print('goodbye')" in test_file.read_text()

    def test_single_match_returns_match_location(self, tmp_path):
        """Test that match location includes line number and context."""
        ctx = FolderContext()
        ctx.add_folder(str(tmp_path))
        
        test_file = tmp_path / "test.py"
        test_file.write_text("line one\ndef hello():\n    print('hello')\nline four\n")
        
        result = execute_tool(
            "search_and_replace_file",
            {
                "filename": str(test_file),
                "search": "print('hello')",
                "replace": "print('goodbye')"
            },
            ctx
        )
        
        payload = json.loads(result)
        assert payload["success"] is True
        assert len(payload["match_locations"]) == 1
        match = payload["match_locations"][0]
        assert match["line"] == 3
        assert "hello" in match["context"]

    def test_single_match_with_multiline_search(self, tmp_path):
        """Test replacing multiline content."""
        ctx = FolderContext()
        ctx.add_folder(str(tmp_path))
        
        test_file = tmp_path / "test.py"
        original = "def foo():\n    x = 1\n    y = 2\n"
        test_file.write_text(original)
        
        search_text = "    x = 1\n    y = 2"
        replace_text = "    x = 10\n    y = 20"
        
        result = execute_tool(
            "search_and_replace_file",
            {
                "filename": str(test_file),
                "search": search_text,
                "replace": replace_text
            },
            ctx
        )
        
        payload = json.loads(result)
        assert payload["success"] is True
        assert payload["matches_found"] == 1
        content = test_file.read_text()
        assert "x = 10" in content
        assert "y = 20" in content


class TestSearchAndReplaceMultipleMatches:
    """Tests for multiple matches scenarios."""

    def test_multiple_matches_reports_count(self, tmp_path):
        """Test that multiple matches are counted correctly."""
        ctx = FolderContext()
        ctx.add_folder(str(tmp_path))
        
        test_file = tmp_path / "test.py"
        test_file.write_text("x = 1\ny = 2\nx = 1\nz = 3\nx = 1\n")
        
        result = execute_tool(
            "search_and_replace_file",
            {
                "filename": str(test_file),
                "search": "x = 1",
                "replace": "x = 10"
            },
            ctx
        )
        
        payload = json.loads(result)
        assert payload["success"] is True
        assert payload["matches_found"] == 3
        assert payload["modified"] is True
        # All occurrences should be replaced
        content = test_file.read_text()
        assert content.count("x = 10") == 3
        # Check that "x = 1" doesn't appear as a standalone line (it's substring of "x = 10")
        assert content.count("x = 1\n") == 0  # No standalone "x = 1" lines

    def test_multiple_matches_returns_all_locations(self, tmp_path):
        """Test that all match locations are returned."""
        ctx = FolderContext()
        ctx.add_folder(str(tmp_path))
        
        test_file = tmp_path / "test.py"
        test_file.write_text("x = 1\ny = 2\nx = 1\n")
        
        result = execute_tool(
            "search_and_replace_file",
            {
                "filename": str(test_file),
                "search": "x = 1",
                "replace": "x = 10"
            },
            ctx
        )
        
        payload = json.loads(result)
        assert len(payload["match_locations"]) == 2
        lines = [m["line"] for m in payload["match_locations"]]
        assert 1 in lines
        assert 3 in lines

    def test_expected_count_validates_correctly(self, tmp_path):
        """Test that expected_count validates match count."""
        ctx = FolderContext()
        ctx.add_folder(str(tmp_path))
        
        test_file = tmp_path / "test.py"
        test_file.write_text("x = 1\ny = 2\nx = 1\n")
        
        # Should succeed with correct count
        result = execute_tool(
            "search_and_replace_file",
            {
                "filename": str(test_file),
                "search": "x = 1",
                "replace": "x = 10",
                "expected_count": 2
            },
            ctx
        )
        
        payload = json.loads(result)
        assert payload["success"] is True
        assert payload["matches_found"] == 2

    def test_expected_count_fails_on_mismatch(self, tmp_path):
        """Test that expected_count fails when count differs."""
        ctx = FolderContext()
        ctx.add_folder(str(tmp_path))
        
        test_file = tmp_path / "test.py"
        test_file.write_text("x = 1\ny = 2\nx = 1\n")
        
        result = execute_tool(
            "search_and_replace_file",
            {
                "filename": str(test_file),
                "search": "x = 1",
                "replace": "x = 10",
                "expected_count": 1  # Wrong count
            },
            ctx
        )
        
        payload = json.loads(result)
        assert payload["success"] is False
        assert "Expected 1 matches but found 2" in payload["error"]
        assert payload["matches_found"] == 2
        # File should not be modified
        assert "x = 1" in test_file.read_text()


class TestSearchAndReplaceNoMatch:
    """Tests for no match scenarios."""

    def test_no_match_returns_error(self, tmp_path):
        """Test that no match returns clear error."""
        ctx = FolderContext()
        ctx.add_folder(str(tmp_path))
        
        test_file = tmp_path / "test.py"
        test_file.write_text("x = 1\ny = 2\n")
        
        result = execute_tool(
            "search_and_replace_file",
            {
                "filename": str(test_file),
                "search": "nonexistent",
                "replace": "replacement"
            },
            ctx
        )
        
        payload = json.loads(result)
        assert payload["success"] is False
        assert payload["matches_found"] == 0
        assert "No matches found" in payload["error"]

    def test_no_match_includes_search_length(self, tmp_path):
        """Test that no match error includes search string length."""
        ctx = FolderContext()
        ctx.add_folder(str(tmp_path))
        
        test_file = tmp_path / "test.py"
        test_file.write_text("x = 1\n")
        
        result = execute_tool(
            "search_and_replace_file",
            {
                "filename": str(test_file),
                "search": "nonexistent_pattern",
                "replace": "replacement"
            },
            ctx
        )
        
        payload = json.loads(result)
        assert "search_length" in payload
        assert payload["search_length"] == len("nonexistent_pattern")


class TestSearchAndReplaceEmptySearch:
    """Tests for empty search string handling."""

    def test_empty_search_returns_error(self, tmp_path):
        """Test that empty search string is rejected."""
        ctx = FolderContext()
        ctx.add_folder(str(tmp_path))
        
        test_file = tmp_path / "test.py"
        test_file.write_text("x = 1\n")
        
        result = execute_tool(
            "search_and_replace_file",
            {
                "filename": str(test_file),
                "search": "",
                "replace": "replacement"
            },
            ctx
        )
        
        payload = json.loads(result)
        assert payload["success"] is False
        assert "empty" in payload["error"].lower()


class TestSearchAndReplaceDisambiguation:
    """Tests for context-based disambiguation."""

    def test_match_location_includes_context(self, tmp_path):
        """Test that match location includes surrounding context."""
        ctx = FolderContext()
        ctx.add_folder(str(tmp_path))
        
        test_file = tmp_path / "test.py"
        test_file.write_text("line one\ndef hello():\n    print('hello')\nline four\n")
        
        result = execute_tool(
            "search_and_replace_file",
            {
                "filename": str(test_file),
                "search": "print('hello')",
                "replace": "print('goodbye')"
            },
            ctx
        )
        
        payload = json.loads(result)
        match = payload["match_locations"][0]
        assert "context" in match
        # Context should include line before and after
        context_lines = match["context"].split("\n")
        assert len(context_lines) >= 2  # At least the match line and one context line

    def test_match_location_context_lines(self, tmp_path):
        """Test that context includes meaningful surrounding lines."""
        ctx = FolderContext()
        ctx.add_folder(str(tmp_path))
        
        test_file = tmp_path / "test.py"
        lines = ["first", "second", "target_line", "fourth", "fifth"]
        test_file.write_text("\n".join(lines) + "\n")
        
        result = execute_tool(
            "search_and_replace_file",
            {
                "filename": str(test_file),
                "search": "target_line",
                "replace": "replaced"
            },
            ctx
        )
        
        payload = json.loads(result)
        match = payload["match_locations"][0]
        # Context should help identify the location
        assert "target_line" in match["context"] or "target" in match["context"].lower()

    def test_multiple_matches_have_different_contexts(self, tmp_path):
        """Test that multiple matches have distinct contexts."""
        ctx = FolderContext()
        ctx.add_folder(str(tmp_path))
        
        test_file = tmp_path / "test.py"
        test_file.write_text("context_a\nx = 1\nmiddle\ncontext_b\nx = 1\nend\n")
        
        result = execute_tool(
            "search_and_replace_file",
            {
                "filename": str(test_file),
                "search": "x = 1",
                "replace": "x = 10"
            },
            ctx
        )
        
        payload = json.loads(result)
        contexts = [m["context"] for m in payload["match_locations"]]
        # Contexts should be different for different occurrences
        assert contexts[0] != contexts[1]


class TestSearchAndReplaceNormalizeWhitespace:
    """Tests for normalize_whitespace parameter."""

    def test_normalize_whitespace_enabled(self, tmp_path):
        """Test that normalize_whitespace allows flexible matching."""
        ctx = FolderContext()
        ctx.add_folder(str(tmp_path))
        
        test_file = tmp_path / "test.py"
        # File has extra whitespace
        test_file.write_text("def  hello():\n    x   =    1\n")
        
        result = execute_tool(
            "search_and_replace_file",
            {
                "filename": str(test_file),
                "search": "def hello():",
                "replace": "def goodbye():",
                "normalize_whitespace": True
            },
            ctx
        )
        
        payload = json.loads(result)
        assert payload["success"] is True
        updated = test_file.read_text()
        assert "def goodbye():" in updated
        # Preserve surrounding file formatting rather than normalizing whole file.
        assert "x   =    1" in updated

    def test_normalize_whitespace_disabled_exact_match(self, tmp_path):
        """Test that without normalize_whitespace, matching is exact."""
        ctx = FolderContext()
        ctx.add_folder(str(tmp_path))
        
        test_file = tmp_path / "test.py"
        test_file.write_text("def  hello():\n")  # Two spaces
        
        result = execute_tool(
            "search_and_replace_file",
            {
                "filename": str(test_file),
                "search": "def hello():",  # One space
                "replace": "def goodbye():"
            },
            ctx
        )
        
        payload = json.loads(result)
        assert payload["success"] is False
        assert "No matches found" in payload["error"]


class TestSearchAndReplaceDryRun:
    """Tests for dry_run preview mode."""

    def test_dry_run_does_not_modify_file(self, tmp_path):
        """Test that dry_run=True does not modify the file."""
        ctx = FolderContext()
        ctx.add_folder(str(tmp_path))
        
        test_file = tmp_path / "test.py"
        original = "def hello():\n    print('hello')\n"
        test_file.write_text(original)
        
        result = execute_tool(
            "search_and_replace_file",
            {
                "filename": str(test_file),
                "search": "print('hello')",
                "replace": "print('goodbye')",
                "dry_run": True
            },
            ctx
        )
        
        payload = json.loads(result)
        assert payload["success"] is True
        assert payload["modified"] is False
        assert payload["dry_run"] is True
        # File should be unchanged
        assert test_file.read_text() == original

    def test_dry_run_returns_preview(self, tmp_path):
        """Test that dry_run returns unified diff preview."""
        ctx = FolderContext()
        ctx.add_folder(str(tmp_path))
        
        test_file = tmp_path / "test.py"
        test_file.write_text("def hello():\n    print('hello')\n")
        
        result = execute_tool(
            "search_and_replace_file",
            {
                "filename": str(test_file),
                "search": "print('hello')",
                "replace": "print('goodbye')",
                "dry_run": True
            },
            ctx
        )
        
        payload = json.loads(result)
        assert "preview" in payload
        assert payload["preview"] != ""
        # Preview should show the change
        assert "goodbye" in payload["preview"]

    def test_dry_run_reports_matches(self, tmp_path):
        """Test that dry_run reports match count without modifying."""
        ctx = FolderContext()
        ctx.add_folder(str(tmp_path))
        
        test_file = tmp_path / "test.py"
        test_file.write_text("x = 1\ny = 2\nx = 1\n")
        
        result = execute_tool(
            "search_and_replace_file",
            {
                "filename": str(test_file),
                "search": "x = 1",
                "replace": "x = 10",
                "dry_run": True
            },
            ctx
        )
        
        payload = json.loads(result)
        assert payload["matches_found"] == 2
        assert payload["modified"] is False


class TestSearchAndReplaceBinaryFiles:
    """Tests for binary file handling."""

    def test_binary_file_returns_error(self, tmp_path):
        """Test that binary files are rejected with clear error."""
        ctx = FolderContext()
        ctx.add_folder(str(tmp_path))
        
        test_file = tmp_path / "test.bin"
        test_file.write_bytes(b"\x00\x01\x02\x03\x04\x05")
        
        result = execute_tool(
            "search_and_replace_file",
            {
                "filename": str(test_file),
                "search": "something",
                "replace": "else"
            },
            ctx
        )
        
        payload = json.loads(result)
        assert payload["success"] is False
        assert "binary" in payload["error"].lower()


class TestSearchAndReplaceBoundaries:
    """Tests for workspace boundary enforcement."""

    def test_access_denied_outside_workspace(self, tmp_path):
        """Test that files outside workspace are rejected."""
        ctx = FolderContext()
        safe_dir = tmp_path / "workspace"
        safe_dir.mkdir()
        ctx.add_folder(str(safe_dir))
        
        # File outside workspace
        outside_file = tmp_path / "secret.txt"
        outside_file.write_text("secret data")
        
        result = execute_tool(
            "search_and_replace_file",
            {
                "filename": str(outside_file),
                "search": "secret",
                "replace": "public"
            },
            ctx
        )
        
        payload = json.loads(result)
        assert payload["success"] is False
        assert "Access denied" in payload["error"]

    def test_path_traversal_blocked(self, tmp_path):
        """Test that path traversal outside workspace is blocked."""
        ctx = FolderContext()
        safe_dir = tmp_path / "workspace"
        safe_dir.mkdir()
        ctx.add_folder(str(safe_dir))
        
        # Create secret file outside
        secret = tmp_path / "secret.txt"
        secret.write_text("secret")
        
        # Try to access via traversal
        hacked_path = str(safe_dir / ".." / "secret.txt")
        
        result = execute_tool(
            "search_and_replace_file",
            {
                "filename": hacked_path,
                "search": "secret",
                "replace": "public"
            },
            ctx
        )
        
        payload = json.loads(result)
        assert payload["success"] is False


class TestSearchAndReplaceFileErrors:
    """Tests for file error handling."""

    def test_file_not_found_returns_error(self, tmp_path):
        """Test that non-existent file returns clear error."""
        ctx = FolderContext()
        ctx.add_folder(str(tmp_path))
        
        result = execute_tool(
            "search_and_replace_file",
            {
                "filename": str(tmp_path / "nonexistent.py"),
                "search": "something",
                "replace": "else"
            },
            ctx
        )
        
        payload = json.loads(result)
        assert payload["success"] is False
        assert "does not exist" in payload["error"]

    def test_unicode_decode_error_handled(self, tmp_path):
        """Test that non-UTF-8 files are handled gracefully."""
        ctx = FolderContext()
        ctx.add_folder(str(tmp_path))
        
        test_file = tmp_path / "test.latin1"
        # Write Latin-1 encoded content
        test_file.write_bytes(b"\xe9\xe8\xe0")  # Valid Latin-1, invalid UTF-8
        
        result = execute_tool(
            "search_and_replace_file",
            {
                "filename": str(test_file),
                "search": "something",
                "replace": "else"
            },
            ctx
        )
        
        payload = json.loads(result)
        # Should either succeed or fail gracefully
        # Implementation may vary - check for reasonable error handling
        assert "success" in payload


class TestSearchAndReplaceIntegration:
    """Integration tests for search_and_replace_file."""

    def test_full_workflow_with_dry_run_then_apply(self, tmp_path):
        """Test workflow: preview with dry_run, then apply."""
        ctx = FolderContext()
        ctx.add_folder(str(tmp_path))
        
        test_file = tmp_path / "test.py"
        test_file.write_text("def hello():\n    print('hello')\n")
        
        # First, preview
        result = execute_tool(
            "search_and_replace_file",
            {
                "filename": str(test_file),
                "search": "print('hello')",
                "replace": "print('goodbye')",
                "dry_run": True
            },
            ctx
        )
        
        payload = json.loads(result)
        assert payload["modified"] is False
        assert test_file.read_text() == "def hello():\n    print('hello')\n"
        
        # Then, apply
        result = execute_tool(
            "search_and_replace_file",
            {
                "filename": str(test_file),
                "search": "print('hello')",
                "replace": "print('goodbye')"
            },
            ctx
        )
        
        payload = json.loads(result)
        assert payload["modified"] is True
        assert "print('goodbye')" in test_file.read_text()

    def test_expected_count_prevents_unexpected_changes(self, tmp_path):
        """Test that expected_count prevents accidental mass changes."""
        ctx = FolderContext()
        ctx.add_folder(str(tmp_path))
        
        test_file = tmp_path / "test.py"
        # File has 5 occurrences of "x = 1"
        test_file.write_text("x = 1\n" * 5)
        
        # User expects 1 but there are 5
        result = execute_tool(
            "search_and_replace_file",
            {
                "filename": str(test_file),
                "search": "x = 1",
                "replace": "x = 10",
                "expected_count": 1
            },
            ctx
        )
        
        payload = json.loads(result)
        assert payload["success"] is False
        # File should be unchanged
        content = test_file.read_text()
        assert content.count("x = 1") == 5
        assert "x = 10" not in content

    def test_tool_is_registered_in_handlers(self):
        """Test that search_and_replace_file is in TOOL_HANDLERS."""
        assert "search_and_replace_file" in TOOL_HANDLERS
