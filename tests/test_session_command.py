"""Pin the /session slash command (replaces /list, /load, /delete, /new)."""

import pytest

import mu.commands as mc
from mu.session.session import Session, SessionManager
from providers.base import LLMProvider, ProviderResponse


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
    return Session(_DummyProvider("dummy"), False, "system instruction", sm)


def test_session_no_args_lists(session):
    result = mc.dispatch(session, "/session", allow_prompt=False)
    assert result is not None
    assert result.ok
    assert "sessions" in result.data
    assert "active" in result.data


def test_session_list_subcommand(session):
    result = mc.dispatch(session, "/session list", allow_prompt=False)
    assert result is not None
    assert result.ok
    assert "sessions" in result.data


def test_session_load_without_name_errors(session):
    result = mc.dispatch(session, "/session load", allow_prompt=False)
    assert result is not None
    assert result.ok is False
    assert "Usage" in result.message


def test_session_delete_without_name_errors(session):
    result = mc.dispatch(session, "/session delete", allow_prompt=False)
    assert result is not None
    assert result.ok is False
    assert "Usage" in result.message


def test_session_rejects_unknown_subcommand(session):
    result = mc.dispatch(session, "/session nope", allow_prompt=False)
    assert result is not None
    assert result.ok is False


def test_legacy_list_removed(session):
    assert mc.dispatch(session, "/list", allow_prompt=False) is None


def test_legacy_load_removed(session):
    assert mc.dispatch(session, "/load foo", allow_prompt=False) is None


def test_legacy_delete_removed(session):
    assert mc.dispatch(session, "/delete foo", allow_prompt=False) is None


def test_legacy_new_removed(session):
    """`/new` was consolidated into /session new — registry must not catch it."""
    assert mc.dispatch(session, "/new", allow_prompt=False) is None
