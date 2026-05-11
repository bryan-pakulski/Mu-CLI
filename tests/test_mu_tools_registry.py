"""Tests for the new tool registry in `mu.tools`.

The registry is a transitional surface: it mirrors the legacy registry in
`core/tools.py` and provides a `@tool` decorator for new registrations.
These tests pin that bridge behavior.
"""

import os
import sys

import pytest

import mu.tools as mut
from core import tools as legacy
from core.workspace import FolderContext


def test_list_tools_includes_legacy_set():
    names = {t.name for t in mut.list_tools()}
    # Sanity: should include core legacy tools that have always existed.
    assert "read_file" in names
    assert "bash" in names
    assert "get_workspace_details" in names


def test_get_returns_descriptor_with_definition():
    descriptor = mut.get("read_file")
    assert descriptor is not None
    assert descriptor.definition.name == "read_file"
    assert "filename" in descriptor.definition.parameters["properties"]


def test_executes_legacy_tool_through_new_registry(tmp_path):
    file_path = tmp_path / "hello.txt"
    file_path.write_text("hi from mu")

    fc = FolderContext()
    fc.add_folder(str(tmp_path))

    ctx = mut.build_tool_context(folder_context=fc, ui=None, variables={})
    result = mut.execute("read_file", {"filename": str(file_path)}, ctx)

    assert isinstance(result, dict)
    assert {"ok", "error_code", "message", "data", "artifacts", "telemetry"}.issubset(result.keys())
    assert result["ok"] is True
    assert "hi from mu" in result["message"]
    assert result["telemetry"]["tool_name"] == "read_file"


def test_register_new_tool_via_decorator(tmp_path):
    @mut.tool(
        name="_mu_test_echo",
        description="Test tool that echoes its message arg.",
        parameters={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
        requires_approval=False,
    )
    def echo_handler(args, context):
        return f"echo: {args.get('message', '')}"

    # The legacy registry should now know about this tool too.
    assert "_mu_test_echo" in {t.name for t in legacy.TOOLS}
    assert legacy.TOOL_DESCRIPTORS.get("_mu_test_echo") is not None

    fc = FolderContext()
    fc.add_folder(str(tmp_path))
    ctx = mut.build_tool_context(folder_context=fc, ui=None, variables={})

    result = mut.execute("_mu_test_echo", {"message": "hello"}, ctx)
    assert result["ok"] is True
    assert "echo: hello" in result["message"]


def test_registered_tool_appears_in_list_tools():
    @mut.tool(
        name="_mu_test_noop",
        description="Test tool that does nothing.",
        parameters={"type": "object", "properties": {}},
        requires_approval=False,
    )
    def noop(args, ctx):
        return "ok"

    names = {t.name for t in mut.list_tools()}
    assert "_mu_test_noop" in names


def test_list_tools_respects_disabled():
    disabled = {"read_file"}
    names = {t.name for t in mut.list_tools(disabled=disabled)}
    assert "read_file" not in names
    assert "bash" in names
