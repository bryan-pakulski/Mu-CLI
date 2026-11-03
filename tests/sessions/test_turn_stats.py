"""Each turn reports its time, tokens and cost; the session keeps running totals."""
import time

import pytest

from mu.session.session import Session, SessionManager
from mu.workspace.folder_context import FolderContext
from providers.base import LLMProvider, MessagePart, ProviderResponse


class _SlowProvider(LLMProvider):
    calls = []

    def get_available_models(self):
        return ["dummy"]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        self.calls.append(messages)
        time.sleep(0.05)
        return ProviderResponse(text="done", parts=[MessagePart(type="text", text="done")],
                                input_tokens=120, output_tokens=30, total_tokens=150)

    def upload_file(self, *a, **kw):
        return None


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.config.HISTORY_DIR", str(tmp_path / "history"))
    sess = Session(_SlowProvider("dummy"), False, "system", SessionManager())
    fc = FolderContext()
    fc.add_folder(str(tmp_path))
    sess.folder_context = fc
    return sess


def test_turn_stats_reported_persisted_and_totalled(session):
    first = session.send_message("hello")
    stats = first["turn_stats"]
    assert stats["input"] == 120 and stats["output"] == 30
    assert stats["api_seconds"] >= 0.05
    assert stats["wall_seconds"] >= stats["api_seconds"]

    user = next(m for m in session.session_manager.history if m["role"] == "user")
    assert user["turn_stats"] == stats

    session.send_message("again")
    totals = session.session_manager.token_counts
    assert totals["api_seconds"] >= 0.1
    assert totals["wall_seconds"] >= totals["api_seconds"]
