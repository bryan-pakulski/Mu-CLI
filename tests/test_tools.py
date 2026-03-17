import os
import pytest
from core.tools import _check_bounds, read_file
from core.workspace import FolderContext


def test_workspace_boundaries(tmp_path):
    ctx = FolderContext()
    safe_dir = tmp_path / "safe"
    safe_dir.mkdir()

    # Add 'safe' directory to tracked folders
    ctx.add_folder(str(safe_dir))

    # Safe file inside
    assert _check_bounds(str(safe_dir / "test.txt"), ctx) is True

    # File outside should be blocked
    outside_file = tmp_path / "outside.txt"
    assert _check_bounds(str(outside_file), ctx) is False

    # Relative path navigation outside workspace
    hacked_path = str(safe_dir / ".." / "outside.txt")
    assert _check_bounds(hacked_path, ctx) is False


def test_read_file_boundary_enforcement(tmp_path):
    ctx = FolderContext()
    safe_dir = tmp_path / "workspace"
    safe_dir.mkdir()
    ctx.add_folder(str(safe_dir))

    out_file = tmp_path / "secret.txt"
    out_file.write_text("top secret")

    result = read_file(str(out_file), ctx)
    assert "Error: Access denied" in result
    assert "top secret" not in result


def test_read_file_not_found(tmp_path):
    ctx = FolderContext()
    ctx.add_folder(str(tmp_path))

    result = read_file(str(tmp_path / "missing.py"), ctx)
    assert "Error: File" in result
    assert "not found" in result
