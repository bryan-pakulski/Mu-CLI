"""Tests for `mu.agent.hooks_config.load_hooks_from_config`."""

import json
import os
from pathlib import Path

import pytest

from mu.agent.hooks import HookContext, HookRegistry
from mu.agent.hooks_config import load_hooks_from_config


def _write_config(tmp_path: Path, hooks: list) -> str:
    cfg = tmp_path / "hooks.json"
    cfg.write_text(json.dumps({"hooks": hooks}))
    return str(cfg)


def test_missing_config_returns_zero(tmp_path):
    reg = HookRegistry()
    count = load_hooks_from_config(str(tmp_path / "absent.json"), registry=reg)
    assert count == 0
    assert reg.list() == []


def test_loads_exit_zero_hook_as_continue(tmp_path):
    reg = HookRegistry()
    cfg = _write_config(
        tmp_path,
        [
            {
                "name": "noop",
                "point": "post_tool",
                "command": "true",
            }
        ],
    )
    count = load_hooks_from_config(cfg, registry=reg)
    assert count == 1
    results = reg.fire(
        "post_tool", HookContext(point="post_tool", tool_name="read_file")
    )
    assert len(results) == 1
    assert results[0].action == "continue"


def test_exit_nonzero_with_short_circuit_blocks_pre_tool(tmp_path):
    reg = HookRegistry()
    cfg = _write_config(
        tmp_path,
        [
            {
                "name": "deny-all-writes",
                "point": "pre_tool",
                "command": "false",
                "on_failure": "short_circuit",
                "message": "denied by deny-all-writes",
            }
        ],
    )
    load_hooks_from_config(cfg, registry=reg)
    blocked = reg.first_short_circuit(
        "pre_tool", HookContext(point="pre_tool", tool_name="write_file")
    )
    assert blocked is not None
    assert blocked.payload["ok"] is False
    assert blocked.payload["error_code"] == "hook_denied"
    assert "denied by deny-all-writes" in blocked.payload["message"]


def test_exit_nonzero_without_short_circuit_continues(tmp_path):
    reg = HookRegistry()
    cfg = _write_config(
        tmp_path,
        [{"name": "log-fail", "point": "post_tool", "command": "false"}],
    )
    load_hooks_from_config(cfg, registry=reg)
    # Should not raise and should not produce a short_circuit
    short = reg.first_short_circuit(
        "post_tool", HookContext(point="post_tool", tool_name="x")
    )
    assert short is None


def test_unknown_point_is_skipped(tmp_path):
    reg = HookRegistry()
    cfg = _write_config(
        tmp_path,
        [
            {"name": "valid", "point": "post_tool", "command": "true"},
            {"name": "junk", "point": "not_a_real_point", "command": "true"},
        ],
    )
    count = load_hooks_from_config(cfg, registry=reg)
    assert count == 1


def test_clear_previous_removes_prior_cfg_hooks(tmp_path):
    reg = HookRegistry()
    cfg = _write_config(
        tmp_path, [{"name": "h1", "point": "post_tool", "command": "true"}]
    )
    load_hooks_from_config(cfg, registry=reg)
    cfg2 = _write_config(
        tmp_path, [{"name": "h2", "point": "post_tool", "command": "true"}]
    )
    load_hooks_from_config(cfg2, registry=reg)
    names = [s.name for s in reg.list("post_tool")]
    assert "cfg:h1" not in names
    assert "cfg:h2" in names


def test_env_vars_are_exposed_to_command(tmp_path):
    out = tmp_path / "captured.txt"
    reg = HookRegistry()
    cfg = _write_config(
        tmp_path,
        [
            {
                "name": "echo-env",
                "point": "post_tool",
                "command": f"echo \"$MU_TOOL_NAME:$MU_TOOL_ARGS_JSON\" > {out}",
            }
        ],
    )
    load_hooks_from_config(cfg, registry=reg)
    reg.fire(
        "post_tool",
        HookContext(
            point="post_tool",
            tool_name="read_file",
            tool_args={"filename": "x.txt"},
        ),
    )
    captured = out.read_text().strip()
    assert captured.startswith("read_file:")
    assert "filename" in captured
