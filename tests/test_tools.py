import os
import pytest
import json
from core.tools import (
    _check_bounds,
    read_file,
    _handle_create_feature_task,
    ToolExecutionContext,
    execute_tool,
    TOOL_HANDLERS,
)
from core.workspace import FolderContext


class _FeatureSessionManagerStub:
    def __init__(self, metadata_path: str):
        self._metadata_path = metadata_path
        self.feature = None
        self.active_feature_id = None
        self.saved = False
        self.record = None

    def get_feature(self, feature_id):
        return self.feature

    def get_feature_metadata_path(self, feature_id):
        return self._metadata_path

    def upsert_feature(self, feature_record):
        self.record = feature_record

    def activate_feature(self, feature_id):
        self.active_feature_id = feature_id

    def save_history(self):
        self.saved = True


class _SessionStub:
    def __init__(self, metadata_path: str):
        self.session_manager = _FeatureSessionManagerStub(metadata_path)


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


def test_create_feature_task_accepts_json_string_tasks(tmp_path):
    ctx = FolderContext()
    ctx.add_folder(str(tmp_path))
    session = _SessionStub(str(tmp_path / "feature_plan.json"))
    tool_ctx = ToolExecutionContext(folder_context=ctx, session=session)
    tasks = [
        {
            "title": "Initialize",
            "objectives": ["Set up"],
            "action_points": ["Create files"],
            "exit_criteria": ["Files exist"],
        }
    ]

    result = _handle_create_feature_task(
        {
            "feature_name": "Visibility",
            "feature_request": "Add failure visibility",
            "tasks": json.dumps(tasks),
        },
        tool_ctx,
    )
    payload = json.loads(result)

    assert payload["ok"] is True
    assert payload["task_count"] == 1
    assert session.session_manager.record is not None


def test_create_feature_task_rejects_non_object_task_items(tmp_path):
    ctx = FolderContext()
    ctx.add_folder(str(tmp_path))
    session = _SessionStub(str(tmp_path / "feature_plan.json"))
    tool_ctx = ToolExecutionContext(folder_context=ctx, session=session)

    result = _handle_create_feature_task(
        {
            "feature_name": "Visibility",
            "feature_request": "Add failure visibility",
            "tasks": [1, 2],
        },
        tool_ctx,
    )

    assert "tasks must be an array of objects" in result


def test_execute_tool_rejects_non_dict_args(tmp_path):
    ctx = FolderContext()
    ctx.add_folder(str(tmp_path))

    result = execute_tool("read_file", "not-a-dict", ctx)
    assert "arguments must be an object/dict" in result


def test_execute_tool_converts_handler_exception_to_error(tmp_path, monkeypatch):
    ctx = FolderContext()
    ctx.add_folder(str(tmp_path))

    def _boom(args, context):
        raise RuntimeError("boom")

    monkeypatch.setitem(TOOL_HANDLERS, "read_file", _boom)
    result = execute_tool("read_file", {"filename": "x.txt"}, ctx)
    assert "Tool 'read_file' failed with RuntimeError: boom" in result
