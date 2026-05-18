"""Pin the /workspace slash command (replaces /folder, /file, /clearfiles)."""

import os

import pytest

import mu.commands as mc
from mu.session.session import Session, SessionManager
from providers.base import LLMProvider, ProviderResponse


class _DummyProvider(LLMProvider):
    def get_available_models(self):
        return ["dummy"]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        return ProviderResponse(text="", parts=[])

    def upload_file(self, file_path, mime_type):
        return None


@pytest.fixture
def session():
    sm = SessionManager()
    return Session(_DummyProvider("dummy"), False, "system instruction", sm)


def test_workspace_no_args_shows_state(session):
    result = mc.dispatch(session, "/workspace", allow_prompt=False)
    assert result is not None
    assert result.ok
    assert "folders" in result.data
    assert "staged_files" in result.data


def test_workspace_folder_add_and_remove(session, tmp_path):
    folder = tmp_path / "myproj"
    folder.mkdir()
    result = mc.dispatch(session, f"/workspace folder {folder}", allow_prompt=False)
    assert result.ok
    assert str(folder) in result.data["added"]
    assert str(folder) in session.folder_context.folders

    result = mc.dispatch(
        session, f"/workspace folder remove {folder}", allow_prompt=False
    )
    assert result.ok
    assert str(folder) not in session.folder_context.folders


def test_workspace_folder_clear(session, tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    session.folder_context.add_folder(str(folder))
    assert session.folder_context.folders
    result = mc.dispatch(session, "/workspace folder clear", allow_prompt=False)
    assert result.ok
    assert session.folder_context.folders == []


def test_workspace_file_stage_and_clear(session, tmp_path):
    target = tmp_path / "scratch.txt"
    target.write_text("hi")
    session.staged_files.append({"path": str(target), "name": "scratch.txt"})

    # No args → list
    result = mc.dispatch(session, "/workspace file", allow_prompt=False)
    assert result.ok
    assert len(result.data["staged_files"]) == 1

    # Clear
    result = mc.dispatch(session, "/workspace file clear", allow_prompt=False)
    assert result.ok
    assert session.staged_files == []


def test_workspace_clear_drops_folders_and_staged(session, tmp_path):
    folder = tmp_path / "p"
    folder.mkdir()
    session.folder_context.add_folder(str(folder))
    session.staged_files.append({"path": "x", "name": "x"})

    result = mc.dispatch(session, "/workspace clear", allow_prompt=False)
    assert result.ok
    assert session.folder_context.folders == []
    assert session.staged_files == []


def test_workspace_rejects_unknown_subcommand(session):
    result = mc.dispatch(session, "/workspace nope", allow_prompt=False)
    assert result.ok is False


def test_legacy_folder_removed(session):
    """`/folder` was consolidated into /workspace folder — must not be in registry."""
    assert mc.dispatch(session, "/folder /tmp", allow_prompt=False) is None


def test_legacy_file_removed(session):
    """`/file` was consolidated into /workspace file — must not be in registry."""
    assert mc.dispatch(session, "/file /tmp/x", allow_prompt=False) is None


def test_legacy_clearfiles_removed(session):
    """`/clearfiles` was consolidated into /workspace file clear."""
    assert mc.dispatch(session, "/clearfiles", allow_prompt=False) is None
