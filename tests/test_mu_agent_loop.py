"""Tests for `mu.agent.loop.AgentLoop`.

The façade currently delegates to `Session.send_message`. Once the
legacy loop body relocates into `AgentLoop.run_turn`, these tests
continue to pin the contract callers see.
"""

import pytest

from mu.agent import AgentLoop, TurnResult, default_registry
from mu.agent.hooks import HookContext, HookSpec
from mu.session.session import Session, SessionManager
from providers.base import LLMProvider, MessagePart, ProviderResponse


class _Provider(LLMProvider):
    def get_available_models(self):
        return ["dummy"]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        return ProviderResponse(
            text="agent loop result",
            parts=[MessagePart(type="text", text="agent loop result")],
            input_tokens=1,
            output_tokens=1,
            total_tokens=2,
        )

    def upload_file(self, *a, **kw):
        return None


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr("mu.session.session.HISTORY_DIR", str(tmp_path / "history"))
    return Session(_Provider("dummy"), False, "system", SessionManager())


def test_run_turn_returns_wrapped_result(session):
    loop = AgentLoop(session)
    result = loop.run_turn("hello agent")
    assert isinstance(result, TurnResult)
    assert result.ok is True
    assert result.status == "completed"
    assert isinstance(result.raw, dict)


def test_stop_fires_on_stop_hook(session):
    seen_reasons = []
    default_registry.add(
        HookSpec(
            name="_test_on_stop",
            point="on_stop",
            priority=100,
            handler=lambda ctx: seen_reasons.append(ctx.stop_reason) or None,
        )
    )
    try:
        loop = AgentLoop(session)
        loop.stop(reason="manual_test")
        assert seen_reasons == ["manual_test"]
    finally:
        default_registry.remove("_test_on_stop")


def test_run_turn_preserves_history(session):
    loop = AgentLoop(session)
    loop.run_turn("first")
    assert any(
        msg.get("role") == "user"
        and any(p.get("text") == "first" for p in msg.get("parts", []))
        for msg in session.session_manager.history
    )


def test_agent_loop_re_exported_from_package():
    import mu.agent as ma

    assert ma.AgentLoop is AgentLoop
    assert ma.TurnResult is TurnResult
