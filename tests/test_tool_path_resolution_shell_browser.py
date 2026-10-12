"""bash cwd and browser targets anchor relative paths to the workspace."""

from __future__ import annotations

from types import SimpleNamespace

from mu.tools.agent.browser import _resolve_target
from mu.tools.shell.handlers import bash_command


class _Folders:
    def __init__(self, root):
        self.folders = [str(root)]

    def is_ignored(self, path):
        return False


def _setup(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    (ws / "sub").mkdir(parents=True)
    (ws / "sub" / "page.html").write_text("<p>hi</p>")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    return ws, _Folders(ws)


def test_bash_relative_cwd_resolves_to_workspace(tmp_path, monkeypatch):
    ws, fc = _setup(tmp_path, monkeypatch)
    out = bash_command("pwd", fc, cwd="sub")
    assert str(ws / "sub") in out
    assert "Access denied" not in out


def test_bash_parent_escape_still_denied(tmp_path, monkeypatch):
    _, fc = _setup(tmp_path, monkeypatch)
    out = bash_command("pwd", fc, cwd="..")
    assert "Access denied" in out


def test_browser_relative_target_resolves_to_workspace(tmp_path, monkeypatch):
    ws, fc = _setup(tmp_path, monkeypatch)
    ctx = SimpleNamespace(folder_context=fc, session=None)
    path, err = _resolve_target("sub/page.html", ctx)
    assert err is None
    # Local targets come back as file:// URLs.
    assert path == "file://" + str((ws / "sub" / "page.html").resolve())


def test_browser_relative_escape_denied(tmp_path, monkeypatch):
    ws, fc = _setup(tmp_path, monkeypatch)
    (tmp_path / "outside.html").write_text("x")
    ctx = SimpleNamespace(folder_context=fc, session=None)
    path, err = _resolve_target("../outside.html", ctx)
    assert path is None and err
