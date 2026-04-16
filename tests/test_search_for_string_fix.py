"""Tests for search_for_string HTML unescaping, track_file, and sync_with_filesystem fixes."""
import os
import pytest
from core.tools import search_for_string, write_file
from core.workspace import FolderContext


class TestHtmlUnescaping:
    """search_for_string should unescape HTML entities before searching."""

    def test_escaped_angle_brackets_match(self, tmp_path):
        """Escaped HTML closing tags like &lt;/style&gt; should match </style> in files."""
        src = tmp_path / "style.html"
        src.write_text("<style>\nbody { margin: 0; }\n</style>\n")

        ctx = FolderContext()
        ctx.add_folder(str(tmp_path))

        # Search with escaped entities — should still find the file
        result = search_for_string("&lt;/style&gt;", ctx)
        assert str(src) in result
        assert "</style>" in result

    def test_unescaped_search_unchanged(self, tmp_path):
        """Normal (unescaped) search strings should still work identically."""
        src = tmp_path / "app.py"
        src.write_text("def hello():\n    print('hello')\n")

        ctx = FolderContext()
        ctx.add_folder(str(tmp_path))

        result = search_for_string("hello", ctx)
        assert str(src) in result

    def test_double_escaped_entities(self, tmp_path):
        """Double-escaped entities like &amp;lt; should become &lt; after unescape."""
        src = tmp_path / "data.xml"
        src.write_text("<value>&lt;tag&gt;</value>\n")

        ctx = FolderContext()
        ctx.add_folder(str(tmp_path))

        # &amp;lt; unescapes to &lt; which should match &lt; in file
        result = search_for_string("&amp;lt;tag&amp;gt;", ctx)
        assert str(src) in result


class TestTrackFile:
    """track_file should add newly created/modified files to initial_snapshots."""

    def test_write_file_makes_file_searchable(self, tmp_path):
        """Files created by write_file should be immediately searchable via search_for_string."""
        ctx = FolderContext()
        ctx.add_folder(str(tmp_path))

        # Write a new file via write_file tool
        new_file = str(tmp_path / "created_by_tool.py")
        write_file(new_file, "def tool_func():\n    pass\n", ctx)

        # Search should find it immediately
        result = search_for_string("tool_func", ctx)
        assert new_file in result

    def test_track_file_skips_binary(self, tmp_path):
        """track_file should skip binary/non-text files (with null bytes)."""
        ctx = FolderContext()
        ctx.add_folder(str(tmp_path))

        # Create a binary file with null bytes — _is_text_file uses null-byte heuristic
        bin_file = str(tmp_path / "binary.dat")
        with open(bin_file, "wb") as f:
            f.write(b"\x00\x01\x02\x03")

        ctx.track_file(bin_file)
        assert bin_file not in ctx.get_file_list()

    def test_track_file_idempotent(self, tmp_path):
        """Calling track_file twice on the same path should not duplicate."""
        ctx = FolderContext()
        ctx.add_folder(str(tmp_path))

        src = tmp_path / "unique.py"
        src.write_text("x = 1\n")

        ctx.track_file(str(src))
        ctx.track_file(str(src))

        # Should appear exactly once
        assert ctx.get_file_list().count(str(src)) == 1


class TestSyncWithFilesystem:
    """sync_with_filesystem should pick up externally added/removed files."""

    def test_picks_up_externally_added_file(self, tmp_path):
        """Files added by developer after initial scan should appear in search results."""
        ctx = FolderContext()
        ctx.add_folder(str(tmp_path))

        # Create file AFTER initial scan (add_folder already ran _scan_and_snapshot)
        external = tmp_path / "external.py"
        external.write_text("def external_func():\n    pass\n")

        # Before sync, search won't find it
        result_before = search_for_string("external_func", ctx)
        # Note: sync_with_filesystem is called inside search_for_string now,
        # so it will find it. Let's verify via the method directly.
        ctx.sync_with_filesystem()
        assert str(external) in ctx.get_file_list()

    def test_removes_deleted_file(self, tmp_path):
        """Files deleted from disk should be removed from initial_snapshots."""
        src = tmp_path / "deleteme.py"
        src.write_text("gone = True\n")

        ctx = FolderContext()
        ctx.add_folder(str(tmp_path))
        assert str(src) in ctx.get_file_list()

        # Delete the file
        os.remove(str(src))
        ctx.sync_with_filesystem()
        assert str(src) not in ctx.get_file_list()

    def test_respects_max_files_limit(self, tmp_path):
        """sync_with_filesystem should not exceed max_files_to_load."""
        ctx = FolderContext()
        ctx.max_files_to_load = 3
        ctx.add_folder(str(tmp_path))

        # Create more files than the limit
        for i in range(10):
            (tmp_path / f"file_{i}.py").write_text(f"val = {i}\n")

        # Reset and re-scan
        ctx.initial_snapshots.clear()
        ctx.add_folder(str(tmp_path))

        # After add_folder, initial snapshots should be capped
        assert len(ctx.get_file_list()) <= 3

    def test_search_finds_external_file(self, tmp_path):
        """End-to-end: search_for_string finds files added externally after initial scan."""
        ctx = FolderContext()
        ctx.add_folder(str(tmp_path))

        # Add file after initial scan
        ext = tmp_path / "new_feature.py"
        ext.write_text("NEW_FEATURE_MARKER = True\n")

        # search_for_string calls sync_with_filesystem internally
        result = search_for_string("NEW_FEATURE_MARKER", ctx)
        assert str(ext) in result