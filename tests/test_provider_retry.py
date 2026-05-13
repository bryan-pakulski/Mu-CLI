"""Pin the exponential-backoff retry around provider calls.

The bug this regression-tests: previously the loop hard-capped at
2 retries with a 3.0s max single delay. An unattended loop hitting a
flapping endpoint would give up after 5–10 seconds. The fix makes
total-elapsed-wait the primary bound (default 120s) so the agent
backs off properly through a real outage.
"""

from typing import List, Optional

import pytest

from core.session import Session, SessionManager
from providers.base import LLMProvider, ProviderResponse


class _StubProvider(LLMProvider):
    """Records call attempts; `fail_first_n` calls raise a transient
    error before succeeding."""

    def __init__(self, name: str, *, fail_first_n: int = 0, exception=None):
        super().__init__(name)
        self.fail_first_n = fail_first_n
        self.exception = exception or ConnectionError("transient")
        self.attempts = 0

    def get_available_models(self) -> List[str]:
        return ["stub"]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        return ProviderResponse(text="", parts=[])

    def stream(self, messages, system_prompt=None, thinking=False, tools=None):
        self.attempts += 1
        if self.attempts <= self.fail_first_n:
            raise self.exception
        yield {"type": "text", "text": "ok"}

    def upload_file(self, file_path, mime_type):
        return None


class _NeverTransient(LLMProvider):
    """Returns a non-transient error — retry should not fire."""

    def __init__(self, name: str):
        super().__init__(name)
        self.attempts = 0

    def get_available_models(self):
        return ["stub"]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        return ProviderResponse(text="", parts=[])

    def stream(self, messages, system_prompt=None, thinking=False, tools=None):
        self.attempts += 1
        raise ValueError("permanent error")

    def upload_file(self, file_path, mime_type):
        return None


def _session(provider):
    sm = SessionManager()
    s = Session(provider, False, "sys", sm)
    s.session_manager.history = []
    s.session_manager.summary_anchor = 0
    s.session_manager.conversation_summary = ""
    return s


@pytest.fixture
def transient_classifier(monkeypatch):
    """Treat ConnectionError as a transient/retryable error for these tests."""

    def _is_transient(self, exc):
        return isinstance(exc, ConnectionError)

    monkeypatch.setattr(
        "core.session.Session._is_transient_provider_error", _is_transient
    )


class _StubRenderer:
    """Bypasses the streaming UI — just exhausts the provider's stream
    generator (so its `attempts` counter advances) and returns a
    canned response."""

    def consume(self, provider, events):
        for _ in events:
            pass
        return ProviderResponse(text="", parts=[])


def _build_stub_renderer(_ui):
    return _StubRenderer()


@pytest.fixture(autouse=True)
def stub_renderer(monkeypatch):
    monkeypatch.setattr(
        "mu.ui.stream.build_default_renderer", _build_stub_renderer
    )


def _call(session):
    """Run _provider_generate_with_retry against a minimal call shape."""
    from providers.base import Message

    return session._provider_generate_with_retry(
        messages=[Message(role="user", parts=[])],
        system_prompt="sys",
        thinking=False,
        tools=[],
    )


def test_retry_succeeds_after_transient_failures(transient_classifier, monkeypatch):
    """3 transient failures then success → no error, 4 total attempts."""
    monkeypatch.setattr("time.sleep", lambda _s: None)
    provider = _StubProvider("stub", fail_first_n=3)
    session = _session(provider)
    session.variables["provider_retry_max_total_wait_seconds"] = 60.0
    session.variables["provider_retry_base_delay"] = 0.01
    session.variables["provider_retry_max_delay"] = 0.1
    _call(session)
    assert provider.attempts == 4  # 3 failures + 1 success


def test_retry_aborts_on_non_transient_error(transient_classifier, monkeypatch):
    """Non-transient errors raise immediately, no retries."""
    monkeypatch.setattr("time.sleep", lambda _s: None)
    provider = _NeverTransient("stub")
    session = _session(provider)
    with pytest.raises(ValueError):
        _call(session)
    assert provider.attempts == 1


def test_retry_respects_total_wait_budget(transient_classifier, monkeypatch):
    """When the cumulative-wait budget is exhausted, the next failure
    is re-raised even though there are retries left in the safety cap."""
    monkeypatch.setattr("time.sleep", lambda _s: None)
    provider = _StubProvider("stub", fail_first_n=999)  # always fails
    session = _session(provider)
    session.variables["provider_retry_max_total_wait_seconds"] = 1.0
    session.variables["provider_retry_base_delay"] = 0.5
    session.variables["provider_retry_max_delay"] = 0.5
    session.variables["provider_max_retries"] = 100
    with pytest.raises(ConnectionError):
        _call(session)
    # With base=0.5 and budget=1.0, expect 2-3 attempts before budget exhausted.
    assert 2 <= provider.attempts <= 4, f"unexpected attempt count {provider.attempts}"


def test_retry_respects_max_attempts_safety_belt(transient_classifier, monkeypatch):
    """Safety belt: even with a huge budget, max_retries caps attempts."""
    monkeypatch.setattr("time.sleep", lambda _s: None)
    provider = _StubProvider("stub", fail_first_n=999)
    session = _session(provider)
    session.variables["provider_retry_max_total_wait_seconds"] = 1_000_000
    session.variables["provider_retry_base_delay"] = 0.01
    session.variables["provider_retry_max_delay"] = 0.1
    session.variables["provider_max_retries"] = 5
    with pytest.raises(ConnectionError):
        _call(session)
    # Initial attempt + 5 retries = 6 total.
    assert provider.attempts == 6


def test_retry_delay_grows_exponentially(transient_classifier, monkeypatch):
    """Each retry's sleep should grow exponentially (with jitter), capped
    at max_delay. Track the actual sleep values."""
    sleeps: List[float] = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
    provider = _StubProvider("stub", fail_first_n=6)
    session = _session(provider)
    session.variables["provider_retry_max_total_wait_seconds"] = 10_000
    session.variables["provider_retry_base_delay"] = 1.0
    session.variables["provider_retry_max_delay"] = 16.0
    session.variables["provider_max_retries"] = 30
    _call(session)
    growth = sleeps[:5]
    assert len(growth) >= 4, f"expected at least 4 retry sleeps, got {growth}"
    for value in growth:
        assert 0 < value <= 20  # cap + 25% jitter slack
    # Last sleeps should approach or hit max_delay.
    assert max(growth) >= 8.0, f"backoff didn't grow enough: {growth}"


def test_retry_schema_variables_are_in_config():
    """The retry knobs must be settable via /set — pin them in the schema."""
    from utils.config import VARIABLE_SCHEMA

    for var in (
        "provider_retry_max_total_wait_seconds",
        "provider_retry_base_delay",
        "provider_retry_max_delay",
        "provider_max_retries",
    ):
        assert var in VARIABLE_SCHEMA, f"{var} missing from VARIABLE_SCHEMA"
