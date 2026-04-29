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
