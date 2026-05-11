"""Integration tests: hooks + plan mode fire from the existing Session loop.

These exercise the real `Session._execute_tool_with_memory` and
`Session._provider_generate_with_retry` against fake providers, asserting
that:
  * pre_tool / post_tool hooks fire around tool execution
  * plan mode blocks write-side tools at the loop boundary
  * pre_provider_call / post_provider_call hooks fire around the model call
"""

import json
import os

import pytest

from core.session import Session, SessionManager
from core.workspace import FolderContext
from mu.agent.hooks import HookContext, HookSpec, default_registry
from providers.base import LLMProvider, MessagePart, ProviderResponse


class _Provider(LLMProvider):
    def get_available_models(self):
        return ["dummy"]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        return ProviderResponse(text="ok", parts=[MessagePart(type="text", text="ok")])

    def upload_file(self, *a, **kw):
        return None


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr("core.session.HISTORY_DIR", str(tmp_path / "history"))
    sm = SessionManager()
    sess = Session(_Provider("dummy"), False, "system", sm)
    fc = FolderContext()
    fc.add_folder(str(tmp_path))
    sess.folder_context = fc
    return sess


def _drain_registry_added_specs(extra_names):
    for name in extra_names:
        default_registry.remove(name)


def test_pre_post_tool_hooks_fire(session, tmp_path):
    pre_seen = []
    post_seen = []

    default_registry.add(
        HookSpec(
            name="_test_pre",
            point="pre_tool",
            priority=999,
            handler=lambda ctx: pre_seen.append(ctx.tool_name) or None,
        )
    )
    default_registry.add(
        HookSpec(
            name="_test_post",
            point="post_tool",
            priority=999,
            handler=lambda ctx: post_seen.append(ctx.tool_name) or None,
        )
    )
    try:
        target = tmp_path / "hi.txt"
        target.write_text("hello")
        # Execute a read_file directly via the tool dispatcher.
        session._execute_tool_with_memory("read_file", {"filename": str(target)})
        assert pre_seen == ["read_file"]
        assert post_seen == ["read_file"]
    finally:
        _drain_registry_added_specs(["_test_pre", "_test_post"])


def test_plan_mode_blocks_write_tool(session, tmp_path):
    # plan_mode hook is auto-installed at import time
    import mu.agent.plan_mode  # noqa: F401

    session.variables["plan_mode"] = True
    result = session._execute_tool_with_memory(
        "write_file", {"filename": str(tmp_path / "nope.txt"), "content": "x"}
    )
    # The result must be the refusal envelope (dict from plan_mode hook).
    assert isinstance(result, dict)
    assert result["ok"] is False
    assert result["error_code"] == "plan_mode_blocked"
    # The file must not have been written.
    assert not (tmp_path / "nope.txt").exists()


def test_plan_mode_allows_read_tool(session, tmp_path):
    import mu.agent.plan_mode  # noqa: F401

    target = tmp_path / "yes.txt"
    target.write_text("readable")
    session.variables["plan_mode"] = True
    result = session._execute_tool_with_memory("read_file", {"filename": str(target)})
    # Reads go through; result is the legacy JSON-string envelope.
    assert isinstance(result, str)
    assert "readable" in result


def test_pre_post_provider_hooks_fire(session):
    pre_seen = []
    post_seen = []

    default_registry.add(
        HookSpec(
            name="_test_pre_provider",
            point="pre_provider_call",
            priority=999,
            handler=lambda ctx: pre_seen.append(ctx.system_prompt) or None,
        )
    )
    default_registry.add(
        HookSpec(
            name="_test_post_provider",
            point="post_provider_call",
            priority=999,
            handler=lambda ctx: post_seen.append(ctx.response.text) or None,
        )
    )
    try:
        response = session._provider_generate_with_retry(
            messages=[],
            system_prompt="sys",
            thinking=False,
            tools=None,
        )
        assert response.text == "ok"
        assert pre_seen == ["sys"]
        assert post_seen == ["ok"]
    finally:
        _drain_registry_added_specs(
            ["_test_pre_provider", "_test_post_provider"]
        )
