import os
import pytest
from core.workspace import FolderContext


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
