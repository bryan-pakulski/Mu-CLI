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

from mu.session.session import Session, SessionManager
from mu.workspace.folder_context import FolderContext
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
    monkeypatch.setattr("mu.session.session.HISTORY_DIR", str(tmp_path / "history"))
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


# ---------------------------------------------------------- abort plumbing


def test_pre_tool_abort_short_circuits_with_envelope_and_sets_flag(session, tmp_path):
    """A `pre_tool` hook returning HookResult(action='abort') must:
      1. prevent the tool from running,
      2. return the synthetic `hook_aborted` envelope to the caller,
      3. set `session._hook_abort_requested` so the iteration loop exits.
    """
    from mu.agent.hooks import HookResult

    target = tmp_path / "should-not-be-read.txt"
    target.write_text("sentinel")
    default_registry.add(
        HookSpec(
            name="_test_pre_tool_abort",
            point="pre_tool",
            priority=999,
            handler=lambda ctx: HookResult(
                action="abort", payload="testing stop signal"
            ),
        )
    )
    try:
        result = session._execute_tool_with_memory(
            "read_file", {"filename": str(target)}
        )
        assert isinstance(result, dict)
        assert result["ok"] is False
        assert result["error_code"] == "hook_aborted"
        assert "testing stop signal" in result["message"]
        # The file was NEVER read — the sentinel is not in the payload.
        assert "sentinel" not in result["message"]
        assert session._hook_abort_requested is True
        assert "testing stop signal" in (session._hook_abort_reason or "")
    finally:
        _drain_registry_added_specs(["_test_pre_tool_abort"])
        session._hook_abort_requested = False
        session._hook_abort_reason = None


def test_post_tool_abort_keeps_result_and_sets_flag(session, tmp_path):
    """`post_tool` abort lets the tool's result flow back unchanged but
    sets the stop flag so the iteration loop exits next time around."""
    from mu.agent.hooks import HookResult

    target = tmp_path / "ok.txt"
    target.write_text("hello world")
    default_registry.add(
        HookSpec(
            name="_test_post_tool_abort",
            point="post_tool",
            priority=999,
            handler=lambda ctx: HookResult(action="abort", payload="enough"),
        )
    )
    try:
        result = session._execute_tool_with_memory(
            "read_file", {"filename": str(target)}
        )
        # The legacy JSON-string envelope still carries the real content.
        assert isinstance(result, str)
        assert "hello world" in result
        # Stop flag is set.
        assert session._hook_abort_requested is True
        assert session._hook_abort_reason == "enough"
    finally:
        _drain_registry_added_specs(["_test_post_tool_abort"])
        session._hook_abort_requested = False
        session._hook_abort_reason = None


def test_first_abort_wins_subsequent_aborts_do_not_clobber(session, tmp_path):
    """If two hooks fire abort in the same turn, the first reason is
    preserved (so the user sees what actually caused the stop)."""
    from mu.agent.hooks import HookResult

    target = tmp_path / "ok.txt"
    target.write_text("x")
    default_registry.add(
        HookSpec(
            name="_test_abort_first",
            point="post_tool",
            priority=10,
            handler=lambda ctx: HookResult(action="abort", payload="first cause"),
        )
    )
    default_registry.add(
        HookSpec(
            name="_test_abort_second",
            point="post_tool",
            priority=20,
            handler=lambda ctx: HookResult(action="abort", payload="second cause"),
        )
    )
    try:
        session._execute_tool_with_memory("read_file", {"filename": str(target)})
        assert session._hook_abort_requested is True
        assert session._hook_abort_reason == "first cause"
    finally:
        _drain_registry_added_specs(["_test_abort_first", "_test_abort_second"])
        session._hook_abort_requested = False
        session._hook_abort_reason = None


def test_pre_provider_call_abort_raises_hook_abort_and_sets_flag(session):
    """`_provider_generate_with_retry` must surface `_HookAbort` (not
    retry it as a transient error) when a `pre_provider_call` hook
    aborts. The flag is set; the provider is never invoked."""
    from mu.agent.hooks import HookResult
    from mu.session.session import _HookAbort

    provider_calls = {"count": 0}
    original_stream = session.provider.stream

    def _spy_stream(*a, **kw):
        provider_calls["count"] += 1
        return original_stream(*a, **kw)

    session.provider.stream = _spy_stream

    default_registry.add(
        HookSpec(
            name="_test_pre_provider_abort",
            point="pre_provider_call",
            priority=10,
            handler=lambda ctx: HookResult(action="abort", payload="reject turn"),
        )
    )
    try:
        with pytest.raises(_HookAbort) as exc_info:
            session._provider_generate_with_retry(
                messages=[], system_prompt="sys", thinking=False, tools=None
            )
        assert "reject turn" in str(exc_info.value)
        assert provider_calls["count"] == 0
        assert session._hook_abort_requested is True
        assert session._hook_abort_reason == "reject turn"
    finally:
        _drain_registry_added_specs(["_test_pre_provider_abort"])
        session._hook_abort_requested = False
        session._hook_abort_reason = None


def test_send_message_exits_cleanly_with_hook_aborted_status(tmp_path, monkeypatch):
    """End-to-end: a `post_provider_call` hook returns abort. The
    iteration loop sees the flag at the next iteration boundary and
    returns a turn-response with status='hook_aborted'."""
    from mu.agent.hooks import HookResult

    # Fresh session so the test doesn't inherit prior abort state.
    monkeypatch.setattr("mu.session.session.HISTORY_DIR", str(tmp_path / "history"))
    sm = SessionManager()
    sess = Session(_Provider("dummy"), False, "system", sm)
    fc = FolderContext()
    fc.add_folder(str(tmp_path))
    sess.folder_context = fc
    sess.agentic = True

    default_registry.add(
        HookSpec(
            name="_test_post_provider_abort_e2e",
            point="post_provider_call",
            priority=10,
            handler=lambda ctx: HookResult(action="abort", payload="end-to-end stop"),
        )
    )
    try:
        result = sess.send_message("hello")
        # The turn finishes the iteration that fired the abort, then exits.
        assert result.get("status") == "hook_aborted"
        assert "end-to-end stop" in (result.get("error") or "")
    finally:
        _drain_registry_added_specs(["_test_post_provider_abort_e2e"])


def test_send_message_resets_abort_flag_each_turn(session):
    """A stale abort flag from a prior turn must not stop the next
    `send_message` before it starts."""
    session._hook_abort_requested = True
    session._hook_abort_reason = "leftover from earlier"
    # Mirrors the early reset in `send_message` — calling it should clear
    # the flag before the loop even starts. We don't actually invoke
    # send_message here (it needs a fuller scaffold); instead pin the
    # reset by reading the source so a refactor that drops the reset
    # gets caught.
    import inspect
    from mu.session import session as session_mod

    # Body moved to `mu/agent/loop_body.py:run_turn` during Phase 4.
    from mu.agent import loop_body as loop_body_mod

    src = inspect.getsource(loop_body_mod.run_turn)
    assert "session._hook_abort_requested = False" in src
    assert "session._hook_abort_reason = None" in src
