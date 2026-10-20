"""Tests for `mu.agent.plan_mode` — read-only enforcement.

Pins: write-side tools are blocked when plan_mode is on; read-side tools
are not blocked; the block envelope shape; and that toggling off restores
normal dispatch.
"""

import pytest

import mu.agent.plan_mode as plan_mode
from mu.agent.hooks import HookContext, HookRegistry


def _fire_pre_tool(registry: HookRegistry, *, tool_name: str, plan_on: bool):
    ctx = HookContext(
        point="pre_tool",
        variables={"plan_mode": plan_on},
        tool_name=tool_name,
        tool_args={},
    )
    return registry.first_short_circuit("pre_tool", ctx)


def test_plan_mode_off_does_not_block():
    reg = HookRegistry()
    plan_mode.install(reg)
    blocked = _fire_pre_tool(reg, tool_name="write_file", plan_on=False)
    assert blocked is None


def test_plan_mode_on_blocks_write_file():
    reg = HookRegistry()
    plan_mode.install(reg)
    blocked = _fire_pre_tool(reg, tool_name="write_file", plan_on=True)
    assert blocked is not None
    payload = blocked.payload
    assert payload["ok"] is False
    assert payload["error_code"] == "plan_mode_blocked"
    assert "plan mode is active" in payload["message"].lower()


def test_plan_mode_on_blocks_writes_and_bash():
    reg = HookRegistry()
    plan_mode.install(reg)
    # Git ops now go through `bash` rather than dedicated tools, so blocking
    # `bash` covers `git commit`, `git push`, etc.
    for tool in ("bash", "write_file", "apply_diff", "search_and_replace_file"):
        blocked = _fire_pre_tool(reg, tool_name=tool, plan_on=True)
        assert blocked is not None, f"expected {tool!r} to be blocked"


def test_plan_mode_does_not_block_reads():
    reg = HookRegistry()
    plan_mode.install(reg)
    for tool in (
        "read_file",
        "search_for_string",
        "search_references",
        "get_workspace_details",
        "list_dir",
        "get_chunk",
        "retrieve_relevant_context",
    ):
        blocked = _fire_pre_tool(reg, tool_name=tool, plan_on=True)
        assert blocked is None, f"expected {tool!r} to be allowed"


def test_install_is_idempotent():
    reg = HookRegistry()
    plan_mode.install(reg)
    plan_mode.install(reg)
    matching = [s for s in reg.list("pre_tool") if s.name == "plan_mode_block_writes"]
    assert len(matching) == 1
