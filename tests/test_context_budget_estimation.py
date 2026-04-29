from core.session import SessionManager


def test_estimate_runtime_history_tokens_includes_summary_and_collation_buffer():
    sm = SessionManager(session_name="context-budget-estimate")
    sm.history = [{"role": "user", "parts": [{"type": "text", "text": "hello"}]}]
    sm.summary_anchor = 0

    base = sm.estimate_runtime_history_tokens()
    assert base > 0

    sm.conversation_summary = "This is a long summary block for token accounting. " * 20
    with_summary = sm.estimate_runtime_history_tokens()
    assert with_summary > base

    sm.collation_buffer.add("read_file", {"filename": "demo.txt"}, "x" * 5000)
    with_collation = sm.estimate_runtime_history_tokens()
    assert with_collation > with_summary

from core.session import Session
from providers.base import LLMProvider, ProviderResponse


class _DummyProvider(LLMProvider):
    def get_available_models(self):
        return ["dummy-model"]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        return ProviderResponse(text="ok", parts=[], input_tokens=1, output_tokens=1, total_tokens=2)

    def upload_file(self, file_path, mime_type):
        return None


def test_prepare_runtime_history_budgets_non_history_layers():
    sm = SessionManager(session_name="context-window-layer-budget")
    session = Session(_DummyProvider("dummy"), False, "system", sm)
    session.variables["context_token_limit"] = 1024
    session.variables["context_trim_threshold"] = 0.5

    sm.history = [
        {"role": "user", "parts": [{"type": "text", "text": "x" * 1200}]},
        {"role": "assistant", "parts": [{"type": "text", "text": "y" * 1200}]},
    ]
    sm.summary_anchor = 0
    sm.conversation_summary = "s" * 2000
    session._pending_retrieved_context = "r" * 2000

    recent = session._prepare_runtime_history()
    assert len(recent) < len(sm.history)
