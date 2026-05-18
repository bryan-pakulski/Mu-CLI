"""Pin the time-awareness prelude in the system prompt.

The model has no built-in wall-clock. Without an injected date string
it has to guess at "is this commit recent?" / "schedule X for next
Tuesday" — which fails predictably. The prelude is a single line at
the very top of the prompt so it's hard to miss.
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


def test_compose_base_system_prompt_starts_with_time_prelude(session):
    """L0 / the actual sent prompt MUST start with the time prelude
    so the model can rely on it being there."""
    body = compose_base_system_prompt(session)
    first_line = body.split("\n", 1)[0]
    assert "Current date/time" in first_line


def test_inject_hierarchical_context_prepends_time(session):
    """`_inject_hierarchical_context` is what builds the actual prompt
    sent to the provider — pin that the time prelude lands there too,
    not only in the /memory L0 display."""
    out = session._inject_hierarchical_context("the base prompt")
    assert "Current date/time" in out
    # And before "the base prompt" — i.e., truly prepended.
    assert out.index("Current date/time") < out.index("the base prompt")
