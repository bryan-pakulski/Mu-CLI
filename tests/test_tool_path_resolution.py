"""Relative tool paths resolve against the session workspace, not process cwd.

Regression: the GUI ran with cwd=$HOME, so ``mu/jobs/service.py`` resolved to
``$HOME/mu/jobs/service.py`` -> outside the workspace -> access_denied, and a
passing check would then have opened the cwd-relative (wrong) file.
"""

from __future__ import annotations

import json
import os

from mu.tools._bounds import check_bounds, resolve_tool_path
from mu.tools.file.handlers import search_and_replace_file, write_file
from mu.tools.workspace.handlers import read_file


class _Folders:
    def __init__(self, root):
        self.folders = [str(root)]

    def is_ignored(self, path):
        return False


def _setup(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    (ws / "pkg").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    return ws, elsewhere, _Folders(ws)


def test_relative_path_resolves_to_workspace_not_cwd(tmp_path, monkeypatch):
    ws, _, fc = _setup(tmp_path, monkeypatch)
    resolved = resolve_tool_path("pkg/mod.py", fc)
    assert resolved == os.path.join(str(ws), "pkg", "mod.py")
    assert check_bounds(resolved, fc)


def test_absolute_path_unchanged(tmp_path, monkeypatch):
    ws, _, fc = _setup(tmp_path, monkeypatch)
    target = str(ws / "pkg" / "a.py")
    assert resolve_tool_path(target, fc) == target


def test_write_and_read_relative_land_in_workspace(tmp_path, monkeypatch):
    ws, elsewhere, fc = _setup(tmp_path, monkeypatch)
    out = write_file("pkg/new.py", "x = 1\n", fc)
    assert "Error" not in out
    assert (ws / "pkg" / "new.py").read_text() == "x = 1\n"
    assert not (elsewhere / "pkg" / "new.py").exists()
    assert "x = 1" in read_file("pkg/new.py", fc)


def test_search_and_replace_relative_edits_workspace_file(tmp_path, monkeypatch):
    ws, _, fc = _setup(tmp_path, monkeypatch)
    (ws / "pkg" / "m.py").write_text("old\n")
    result = json.loads(search_and_replace_file("pkg/m.py", "old", "new", folder_context=fc))
    assert result["success"] is True
    assert (ws / "pkg" / "m.py").read_text() == "new\n"


def test_parent_escape_still_denied(tmp_path, monkeypatch):
    _, elsewhere, fc = _setup(tmp_path, monkeypatch)
    out = write_file("../elsewhere/escape.txt", "nope", fc)
    assert "Access denied" in out
    assert not (elsewhere / "escape.txt").exists()


def test_secret_path_still_denied(tmp_path, monkeypatch):
    _, _, fc = _setup(tmp_path, monkeypatch)
    secret = os.path.expanduser("~/.ssh/id_rsa")
    assert not check_bounds(resolve_tool_path("~/.ssh/id_rsa", fc), fc)
    assert resolve_tool_path("~/.ssh/id_rsa", fc) == secret
