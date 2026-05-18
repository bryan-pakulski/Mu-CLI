"""Pin the /docs slash command."""

import pytest

import mu.commands as mc
from mu.session.session import Session, SessionManager
from mu.commands.docs import (
    _docs_root,
    _resolve_doc,
    docs_cmd,
    list_doc_names,
)
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


def test_docs_root_resolves_to_bundled_documentation_dir():
    root = _docs_root()
    assert root.endswith("documentation")
    import os

    assert os.path.isdir(root)


def test_list_doc_names_includes_known_files():
    names = set(list_doc_names())
    # These ship with the repo as of the time of writing.
    for expected in ("skills", "commands", "configuration", "loop_mode"):
        assert expected in names, f"missing expected doc: {expected}"


def test_resolve_doc_accepts_stem_and_filename():
    by_stem = _resolve_doc("skills")
    by_filename = _resolve_doc("skills.md")
    by_case = _resolve_doc("Skills")
    assert by_stem is not None
    assert by_filename == by_stem
    assert by_case == by_stem


def test_resolve_doc_returns_none_for_unknown():
    assert _resolve_doc("does-not-exist") is None


def test_docs_cmd_lists_when_called_without_args(session):
    result = docs_cmd(session, "", allow_prompt=False)
    assert result.ok
    names = {entry["name"] for entry in result.data["docs"]}
    assert "skills" in names
    # Each entry has a summary (the first heading line of the doc).
    for entry in result.data["docs"]:
        assert "name" in entry
        assert "path" in entry
        assert "summary" in entry


def test_docs_cmd_shows_one_doc_by_name(session):
    result = docs_cmd(session, "skills", allow_prompt=False)
    assert result.ok
    assert result.data["name"] == "skills"
    assert result.data["path"].endswith("skills.md")
    # The message field carries the full markdown.
    assert "# Skills" in result.message or "Skills" in result.message


def test_docs_cmd_errors_on_unknown_name(session):
    result = docs_cmd(session, "no-such-doc", allow_prompt=False)
    assert not result.ok
    assert "no-such-doc" in result.message


def test_docs_dispatches_through_registry(session):
    result = mc.dispatch(session, "/docs", allow_prompt=False)
    assert result is not None
    assert result.ok
    assert "docs" in result.data


def test_docs_dispatch_with_name(session):
    result = mc.dispatch(session, "/docs skills", allow_prompt=False)
    assert result is not None
    assert result.ok
    assert result.data["name"] == "skills"
