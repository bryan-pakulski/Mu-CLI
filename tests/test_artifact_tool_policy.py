from types import SimpleNamespace

import pytest

from mu.artifact.registry import ArtifactError
from mu.tools.artifact.handlers import _validated_source_path


def context(session_type: str, folders: list[str]):
    folder_context = SimpleNamespace(folders=folders)
    session = SimpleNamespace(
        variables={"session_type": session_type},
        folder_context=folder_context,
    )
    return SimpleNamespace(
        session=session,
        variables=session.variables,
        folder_context=folder_context,
    )


def test_chat_file_artifacts_are_refused(tmp_path):
    source = tmp_path / "report.txt"
    source.write_text("hello")
    with pytest.raises(ArtifactError, match="inline content only"):
        _validated_source_path({"file_path": str(source)}, context("chat", []))


def test_workspace_file_artifact_must_be_inside_attached_folder(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    inside = workspace / "inside.txt"
    inside.write_text("ok")
    outside = tmp_path / "outside.txt"
    outside.write_text("no")
    ctx = context("workspace", [str(workspace)])
    assert _validated_source_path({"file_path": str(inside)}, ctx) == str(inside.resolve())
    with pytest.raises(ArtifactError, match="inside an attached workspace"):
        _validated_source_path({"file_path": str(outside)}, ctx)


def test_container_file_artifact_accepts_runtime_file(tmp_path):
    source = tmp_path / "runtime.txt"
    source.write_text("ok")
    assert _validated_source_path({"file_path": str(source)}, context("container", [])) == str(source.resolve())
