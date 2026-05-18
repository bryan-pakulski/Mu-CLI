import os
import pytest
from mu.workspace.folder_context import FolderContext


def test_workspace_tree_map(tmp_path):
    # Create a dummy folder structure
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "main.py").write_text("print('hello')")
    (tmp_path / "README.md").write_text("docs")

    ctx = FolderContext()
    ctx.add_folder(str(tmp_path))

    tree = ctx.get_tree_map()

    # Assert formatting and expected files
    assert "main.py" in tree
    assert "README.md" in tree
    assert "📁" in tree
    assert "📄" in tree


def test_ignored_directories(tmp_path):
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("secret")
    (tmp_path / "visible.py").write_text("code")

    ctx = FolderContext()
    ctx.add_folder(str(tmp_path))

    tree = ctx.get_tree_map()
    assert "visible.py" in tree
    assert ".git" not in tree
    assert "config" not in tree


def test_is_ignored_path_does_not_prune_unmatched_dirs(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "keep").mkdir()
    (workspace / "keep" / "visible.txt").write_text("ok")
    (workspace / "cache").mkdir()
    (workspace / "cache" / "ignored.txt").write_text("ignore me")
    (workspace / ".gitignore").write_text("cache/\n")

    ctx = FolderContext()
    ctx.add_folder(str(workspace))

    keep_dir = str(workspace / "keep")
    cache_dir = str(workspace / "cache")

    assert ctx._is_ignored_path(keep_dir) is False
    assert ctx._is_ignored_path(cache_dir) is True

    # Re-scan should be stable and not drop visible sibling directories.
    before = set(ctx.get_file_list())
    ctx._scan_and_snapshot(str(workspace))
    after = set(ctx.get_file_list())
    assert before == after
    assert str(workspace / "keep" / "visible.txt") in after
    assert str(workspace / "cache" / "ignored.txt") not in after


def test_gitignore_dir_pruning_respects_scope(tmp_path):
    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"
    root_a.mkdir()
    root_b.mkdir()

    # root_a ignores logs/; root_b should remain unaffected.
    (root_a / ".gitignore").write_text("logs/\n")
    (root_a / "logs").mkdir()
    (root_a / "logs" / "a.log").write_text("a")
    (root_a / "src").mkdir()
    (root_a / "src" / "a.py").write_text("print('a')")

    (root_b / "logs").mkdir()
    (root_b / "logs" / "b.log").write_text("b")
    (root_b / "src").mkdir()
    (root_b / "src" / "b.py").write_text("print('b')")

    ctx = FolderContext()
    ctx.add_folder(str(root_a))
    ctx.add_folder(str(root_b))

    tracked = set(ctx.get_file_list())

    # root_a/logs is ignored by root_a .gitignore
    assert str(root_a / "logs" / "a.log") not in tracked
    # root_b/logs should still be included (no .gitignore rule there)
    assert str(root_b / "logs" / "b.log") in tracked
    assert str(root_a / "src" / "a.py") in tracked
    assert str(root_b / "src" / "b.py") in tracked
