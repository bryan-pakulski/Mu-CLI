import os
import pytest
from core.approval import build_approval_plan
from core.tools import (
    execute_tool,
    get_modifications,
    get_tool_descriptor,
    serialize_tool_descriptor,
)
from core.workspace import FolderContext


def test_batch_job_basic(tmp_path):
    ctx = FolderContext()
    ctx.add_folder(str(tmp_path))

    # Create a dummy file to read later
    dummy_file = tmp_path / "dummy.txt"
    dummy_file.write_text("hello batch")

    commands = [
        {"tool_name": "get_current_time", "tool_args": {}},
        {"tool_name": "read_file", "tool_args": {"filename": str(dummy_file)}},
    ]

    result = execute_tool("batch_job", {"commands": commands}, ctx)

    assert "--- Batch Job Results ---" in result
    assert "Tool: get_current_time" in result
    assert "Tool: read_file" in result
    assert "hello batch" in result


def test_batch_job_nested_prevention(tmp_path):
    ctx = FolderContext()
    ctx.add_folder(str(tmp_path))

    commands = [
        {
            "tool_name": "batch_job",
            "tool_args": {
                "commands": [{"tool_name": "get_current_time", "tool_args": {}}]
            },
        }
    ]

    result = execute_tool("batch_job", {"commands": commands}, ctx)
    assert "nested batch_job not allowed" in result


def test_batch_job_modifications(tmp_path):
    ctx = FolderContext()
    ctx.add_folder(str(tmp_path))

    file1 = tmp_path / "file1.txt"
    file2 = tmp_path / "file2.txt"

    commands = [
        {
            "tool_name": "write_file",
            "tool_args": {"filename": str(file1), "content": "content1"},
        },
        {
            "tool_name": "write_file",
            "tool_args": {"filename": str(file2), "content": "content2"},
        },
    ]

    # Test get_modifications for a batch_job
    mods = get_modifications("batch_job", {"commands": commands}, ctx)

    assert len(mods) == 2
    # mod is (original_content, new_content, filename)
    assert mods[0][2] == str(file1)
    assert mods[0][1] == "content1"
    assert mods[1][2] == str(file2)
    assert mods[1][1] == "content2"


def test_batch_job_execution_with_writes(tmp_path):
    ctx = FolderContext()
    ctx.add_folder(str(tmp_path))

    file1 = tmp_path / "batch_write.txt"

    commands = [
        {
            "tool_name": "write_file",
            "tool_args": {"filename": str(file1), "content": "initial"},
        },
        {"tool_name": "read_file", "tool_args": {"filename": str(file1)}},
    ]

    result = execute_tool("batch_job", {"commands": commands}, ctx)

    assert "Successfully wrote to" in result
    assert "initial" in result

    with open(file1, "r") as f:
        assert f.read() == "initial"


def test_tool_descriptors_expose_execution_metadata():
    read_descriptor = get_tool_descriptor("read_file")
    batch_descriptor = get_tool_descriptor("batch_job")
    payload = serialize_tool_descriptor("git_commit")

    assert read_descriptor is not None
    assert read_descriptor.execution_kind == "read"
    assert read_descriptor.result_mode == "structured+collated"
    assert batch_descriptor is not None
    assert batch_descriptor.execution_kind == "composite"
    assert batch_descriptor.handler_key == "batch_job"
    assert payload["server_policy"] == "allowed"
    assert payload["preview_policy"] == "optional"


def test_build_approval_plan_for_batch_job_collects_nested_modifications(tmp_path):
    ctx = FolderContext()
    ctx.add_folder(str(tmp_path))

    file1 = tmp_path / "file1.txt"
    file2 = tmp_path / "file2.txt"
    plan = build_approval_plan(
        "batch_job",
        {
            "commands": [
                {
                    "tool_name": "write_file",
                    "tool_args": {"filename": str(file1), "content": "alpha"},
                },
                {
                    "tool_name": "write_file",
                    "tool_args": {"filename": str(file2), "content": "beta"},
                },
            ]
        },
        ctx,
    )

    assert plan.requires_approval is True
    assert plan.can_approve is True
    assert [mod.filename for mod in plan.modifications] == [str(file1), str(file2)]


def test_build_approval_plan_marks_malformed_diff_as_preview_failure(tmp_path):
    ctx = FolderContext()
    ctx.add_folder(str(tmp_path))
    target = tmp_path / "note.txt"
    target.write_text("before\n", encoding="utf-8")

    plan = build_approval_plan(
        "apply_diff",
        {"filename": str(target), "diff": "this is not a unified diff"},
        ctx,
    )

    assert plan.requires_approval is True
    assert plan.can_approve is False
    assert plan.error_code == "preview_failed"
    assert plan.preview_error is not None
