"""Tests for the manual `/compact` slash command and the agent `compact`
tool — both backed by `mu.agent.compactor.manual_compact`.
"""

import json

import mu.tools as mt

from mu.commands import dispatch
from mu.session.session import Session, SessionManager
from providers.base import LLMProvider, ProviderResponse


class _Tiny8kProvider(LLMProvider):
    """Mimics an 8k-window Ollama model; generate() returns a compact summary."""

    def get_available_models(self):
        return ["tiny-8k"]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        return ProviderResponse(
            text="### Progress\nCompacted prior turns into a short summary.",
            parts=[],
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
        )

    def upload_file(self, file_path, mime_type):
        return None

    def effective_context_window(self, model_name=None):
        return 8192


def _make_session():
    sm = SessionManager()
    session = Session(_Tiny8kProvider("tiny-8k"), False, "sys", sm)
    # Isolate from L0 agentic accounting (see test_compaction_e2e.py).
    session.agentic = False
    return session


def _stuff(sm: SessionManager, n_turns: int = 30, size: int = 1500):
    """Append n_turns of (user, assistant) text bombs — ~size/4 tokens each."""
    sm.history.clear()
    sm.summary_anchor = 0
    sm.conversation_summary = ""
    for _ in range(n_turns):
        sm.history.append({"role": "user", "parts": [{"type": "text", "text": "u" * size}]})
        sm.history.append({"role": "assistant", "parts": [{"type": "text", "text": "a" * size}]})


def _ctx(session):
    return mt.build_tool_context(
        folder_context=None, ui=None, variables=getattr(session, "variables", {}),
        session=session,
    )


# --------------------------------------------------------------- manual_compact


def test_manual_compact_compacts_over_budget_history():
    from mu.agent.compactor import manual_compact

    session = _make_session()
    sm = session.session_manager
    _stuff(sm, n_turns=30, size=1500)
    before_tokens = sm.estimate_runtime_history_tokens()
    assert before_tokens > session._compaction_token_budget()

    result = manual_compact(session, focus="the auth refactor")

    assert result["ok"] is True
    assert result["compacted"] is True
    assert result["before"]["est_tokens"] == before_tokens
    assert result["after"]["est_tokens"] < before_tokens, "compaction didn't shrink history"
    assert result["after"]["summary_anchor"] > result["before"]["summary_anchor"]
    assert result["focus"] == "the auth refactor"
    # focus is bridged onto the session manager for the summarizer.
    assert sm._compact_focus == "the auth refactor"
    # compaction kind tagged for the run tracer.
    assert sm._pending_compaction_kind == "manual"
    # An explicit pass satisfies the turn so the auto-hook won't re-fire.
    assert session._compacted_this_turn is True


def test_manual_compact_forces_one_segment_when_under_budget():
    """An explicit /compact always makes progress when there's something to
    summarize (Claude Code's manual /compact always summarizes), even when
    history is under the budget gate."""
    from mu.agent.compactor import manual_compact

    session = _make_session()
    sm = session.session_manager
    # 20 turns (40 entries) — over the keep_recent=12 boundary (so there IS
    # something to summarize) but small enough to be UNDER the 8k budget...
    # actually 40 entries × 1500 chars is huge. Use tiny messages so it's
    # under budget but still has summarizable history.
    for i in range(20):
        sm.history.append({"role": "user", "parts": [{"type": "text", "text": f"msg {i}"}]})
        sm.history.append({"role": "assistant", "parts": [{"type": "text", "text": f"reply {i}"}]})

    assert sm.estimate_runtime_history_tokens() <= session._compaction_token_budget(), (
        "precondition: history should be under budget to test the force path"
    )
    assert len(sm.history) > 12, "precondition: must exceed keep_recent so a segment is summarizable"

    result = manual_compact(session)
    assert result["ok"] is True
    assert result["compacted"] is True, "manual compact should force a roll even under budget"
    assert result["after"]["summary_anchor"] > result["before"]["summary_anchor"]


def test_manual_compact_nothing_to_compact_when_caught_up():
    from mu.agent.compactor import manual_compact

    session = _make_session()
    sm = session.session_manager
    # Only 2 entries — under keep_recent=12, so nothing to summarize.
    sm.history = [
        {"role": "user", "parts": [{"type": "text", "text": "hi"}]},
        {"role": "assistant", "parts": [{"type": "text", "text": "hello"}]},
    ]
    result = manual_compact(session)
    assert result["ok"] is True
    assert result["compacted"] is False


def test_manual_compact_no_session_manager():
    from mu.agent.compactor import manual_compact

    class _Bare:
        session_manager = None

    result = manual_compact(_Bare())
    assert result["ok"] is False
    assert "no session manager" in result["error"]


# --------------------------------------------------------------- slash command


def test_slash_compact_command_dispatches_and_compacts():
    session = _make_session()
    sm = session.session_manager
    _stuff(sm, n_turns=30, size=1500)

    result = dispatch(session, "/compact the auth refactor", allow_prompt=False)

    assert result is not None
    assert result.ok is True
    assert result.data["compacted"] is True
    assert "Compacted L5 history" in result.message
    assert "the auth refactor" in result.message
    assert sm._compact_focus == "the auth refactor"


def test_slash_compact_nothing_to_compact_message():
    session = _make_session()
    sm = session.session_manager
    sm.history = [
        {"role": "user", "parts": [{"type": "text", "text": "hi"}]},
        {"role": "assistant", "parts": [{"type": "text", "text": "hello"}]},
    ]
    result = dispatch(session, "/compact", allow_prompt=False)
    assert result is not None
    assert result.ok is True
    assert result.data["compacted"] is False
    assert "Nothing to compact" in result.message


# --------------------------------------------------------------- agent tool


def test_compact_agent_tool_via_execute():
    session = _make_session()
    sm = session.session_manager
    _stuff(sm, n_turns=30, size=1500)
    before_tokens = sm.estimate_runtime_history_tokens()

    res = mt.execute("compact", {"focus": "the auth refactor"}, _ctx(session))

    # result_mode=raw → the handler's manual_compact dict (carrying ok/compacted)
    # is promoted to the envelope top level by execute().
    assert res["ok"] is True
    assert res["compacted"] is True
    assert res["before"]["est_tokens"] == before_tokens
    assert res["after"]["est_tokens"] < before_tokens
    assert res["focus"] == "the auth refactor"


def test_compact_agent_tool_no_session():
    ctx = mt.build_tool_context(
        folder_context=None, ui=None, variables={}, session=None
    )
    res = mt.execute("compact", {}, ctx)
    assert res["ok"] is False
    body = res.get("data") if isinstance(res.get("data"), str) else res.get("message", "")
    assert "No session" in (body if isinstance(body, str) else json.dumps(res))


def test_compact_agent_tool_no_focus_arg():
    session = _make_session()
    sm = session.session_manager
    _stuff(sm, n_turns=30, size=1500)
    res = mt.execute("compact", {}, _ctx(session))
    assert res["ok"] is True
    assert res["compacted"] is True
    assert res["focus"] == ""


# --------------------------------------------------------------- registration


def test_compact_command_and_tool_registered():
    from mu.commands import list_commands
    from mu.tools.descriptors import TOOL_DESCRIPTORS

    names = {n for s in list_commands() for n in s.names}
    assert "/compact" in names
    assert "compact" in TOOL_DESCRIPTORS
    d = TOOL_DESCRIPTORS["compact"]
    assert d.execution_kind == "mutate"
    assert d.definition.requires_approval is False