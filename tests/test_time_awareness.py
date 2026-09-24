"""Pin the time-awareness prelude in the system prompt.

The model has no built-in wall-clock. Without an injected date string
it has to guess at "is this commit recent?" / "schedule X for next
Tuesday" — which fails predictably. The prelude is a single line inside
the per-iteration LAYER 5 block: present every request, but off the
prompt head so the static base + tool schema stay a cacheable prefix.
"""

import re

import pytest

from mu.session.session import Session, SessionManager
from providers.base import LLMProvider, ProviderResponse
from utils.runtime_metrics import _current_time_prelude, compose_base_system_prompt


class _DummyProvider(LLMProvider):
    def get_available_models(self):
        return ["dummy"]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        return ProviderResponse(text="", parts=[])

    def upload_file(self, file_path, mime_type):
        return None


@pytest.fixture
def session():
    sm = SessionManager()
    s = Session(_DummyProvider("dummy"), False, "sys", sm)
    s._mcp_clients = []
    s.session_manager.history = []
    s.session_manager.summary_anchor = 0
    s.session_manager.conversation_summary = ""
    s.session_manager.provider_config = {"provider": "openai", "model": "gpt-4o"}
    return s


def test_time_prelude_is_iso_date():
    body = _current_time_prelude()
    # Looks like a real timestamp, not a placeholder.
    assert re.search(r"\d{4}-\d{2}-\d{2}", body), body


def test_time_prelude_includes_weekday():
    body = _current_time_prelude()
    weekdays = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}
    assert any(day in body for day in weekdays), body


def test_compose_base_system_prompt_accounts_time_prelude(session):
    """L0 accounting must include the time prelude (it is sent every
    request) but keep it OFF the prompt head so the static base stays a
    stable, cacheable prefix."""
    body = compose_base_system_prompt(session)
    assert "Current date/time" in body
    first_line = body.split("\n", 1)[0]
    assert "Current date/time" not in first_line


def test_inject_hierarchical_context_places_time_in_layer5_when_prefix_unstable(session):
    """Legacy placement (`prompt_prefix_stable=False`): the time prelude
    lands in the system prompt AFTER the static base and inside the
    per-iteration LAYER 5 block."""
    session.variables["prompt_prefix_stable"] = False
    out = session._inject_hierarchical_context("the base prompt")
    assert "Current date/time" in out
    assert out.index("the base prompt") < out.index("Current date/time")
    assert out.index("LAYER 5") < out.index("Current date/time")
    assert not out.startswith("Current date/time")


def test_inject_hierarchical_context_keeps_clock_out_of_stable_prompt(session):
    """Default (`prompt_prefix_stable=True`, context_packaging_v2 P4): the
    wall-clock changes every minute, so it must NOT be in the system prompt
    at all — it ships in the trailing runtime-state message instead, and
    the system prompt is byte-identical across iterations."""
    session.variables["prompt_prefix_stable"] = True
    out = session._inject_hierarchical_context("the base prompt", volatile_handoff=True)
    assert "Current date/time" not in out
    assert "LAYER 5" in out
    # Direct callers without the handoff (memory map, subagent bootstrap,
    # tests) still get the complete legacy prompt.
    legacy = session._inject_hierarchical_context("the base prompt")
    assert "Current date/time" in legacy
