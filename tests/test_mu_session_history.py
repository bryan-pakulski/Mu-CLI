"""Tests for `mu.session.history.HistoryMixin` in isolation.

These verify the mixin works against any host class that supplies the
three required attributes (`history`, `summary_anchor`,
`conversation_summary`), independent of `SessionManager`.
"""

from typing import Any, Dict, List

from mu.session.history import HistoryMixin
from mu.session import HistoryMixin as PackageHistoryMixin


class _Host(HistoryMixin):
    def __init__(
        self,
        history: List[Dict[str, Any]] = None,
        summary_anchor: int = 0,
        conversation_summary: str = "",
    ):
        self.history = history or []
        self.summary_anchor = summary_anchor
        self.conversation_summary = conversation_summary


def test_mixin_re_exported_via_package():
    assert HistoryMixin is PackageHistoryMixin


def test_estimate_tokens_from_text_zero_for_empty():
    assert HistoryMixin._estimate_tokens_from_text("") == 0
    assert HistoryMixin._estimate_tokens_from_text(None) == 0


def test_estimate_tokens_from_text_chars_over_four():
    # 12 chars / 4 = 3 tokens
    assert HistoryMixin._estimate_tokens_from_text("hello world!") == 3


def test_estimate_message_tokens_counts_role_type_and_payload():
    host = _Host()
    msg = {"role": "user", "parts": [{"type": "text", "text": "abcdefghij"}]}
    # 3 baseline + role(4 chars/4=1) + type(4 chars/4=1) + text(10 chars/4=2 = max(1, 2))
    assert host._estimate_message_tokens(msg) >= 5


def test_estimate_runtime_history_tokens_skips_summarized():
    host = _Host(
        history=[
            {"role": "user", "parts": [{"type": "text", "text": "old " * 100}]},
            {"role": "assistant", "parts": [{"type": "text", "text": "new"}]},
        ],
        summary_anchor=1,
    )
    # Only the post-anchor message counts.
    total = host.estimate_runtime_history_tokens()
    assert total < 50  # rough — far less than the "old" message contributes


def test_roll_history_summary_advances_anchor_to_user_boundary():
    history = [
        {"role": "user", "parts": [{"type": "text", "text": "u1"}]},
        {"role": "assistant", "parts": [{"type": "text", "text": "a1"}]},
        {"role": "user", "parts": [{"type": "text", "text": "u2"}]},
        {"role": "assistant", "parts": [{"type": "text", "text": "a2"}]},
        {"role": "user", "parts": [{"type": "text", "text": "u3"}]},
        {"role": "assistant", "parts": [{"type": "text", "text": "a3"}]},
    ]
    host = _Host(history=list(history))
    changed = host.roll_history_summary(keep_recent=2)
    assert changed is True
    assert host.summary_anchor == 4  # snaps to the user boundary at idx 4
    assert "u1" in host.conversation_summary
    assert "a2" in host.conversation_summary


def test_roll_history_summary_returns_false_when_nothing_to_roll():
    host = _Host(
        history=[
            {"role": "user", "parts": [{"type": "text", "text": "only one"}]},
        ]
    )
    assert host.roll_history_summary(keep_recent=4) is False


def test_roll_to_token_budget_compacts_until_under_budget():
    # 6 messages, each ~200 chars → 50 tokens each → 300 tokens total
    history = [
        {
            "role": "user" if i % 2 == 0 else "assistant",
            "parts": [{"type": "text", "text": ("x" * 200) + f" turn {i}"}],
        }
        for i in range(6)
    ]
    host = _Host(history=history)
    pre = host.estimate_runtime_history_tokens()
    assert pre > 100

    changed = host.roll_history_summary_to_token_budget(
        token_budget=60, keep_recent=2
    )
    assert changed is True
    post = host.estimate_runtime_history_tokens()
    assert post < pre


def test_degrade_oldest_truncates_large_text():
    host = _Host(
        history=[
            {
                "role": "user",
                "parts": [{"type": "text", "text": "X" * 8000}],
            },
            {
                "role": "assistant",
                "parts": [{"type": "text", "text": "short"}],
            },
        ]
    )
    changed = host._degrade_oldest_runtime_payload(max_chars=1000)
    assert changed is True
    truncated = host.history[0]["parts"][0]["text"]
    assert len(truncated) <= 1100  # 1000 + truncation marker
    assert "truncated_to_1000_chars_for_context_budget" in truncated


def test_degrade_oldest_truncates_tool_result_dict():
    big_result = {"data": "Y" * 10_000}
    host = _Host(
        history=[
            {
                "role": "tool",
                "parts": [
                    {
                        "type": "tool_result",
                        "tool_name": "read_file",
                        "tool_result": big_result,
                    }
                ],
            }
        ]
    )
    changed = host._degrade_oldest_runtime_payload(max_chars=500)
    assert changed is True
    clipped = host.history[0]["parts"][0]["tool_result"]
    assert isinstance(clipped, str)  # serialized to string after clipping
    assert "truncated_to_500_chars_for_context_budget" in clipped


def test_degrade_returns_false_when_nothing_oversized():
    host = _Host(
        history=[
            {"role": "user", "parts": [{"type": "text", "text": "tiny"}]},
        ]
    )
    assert host._degrade_oldest_runtime_payload(max_chars=1000) is False


def test_clip_conversation_summary():
    host = _Host()
    host.conversation_summary = "a" * 10000
    host._clip_conversation_summary(limit=2000)
    assert len(host.conversation_summary) <= 2100
    assert "conversation_summary_truncated" in host.conversation_summary


def test_session_manager_lazy_export():
    import mu.session

    # Triggers the module-level __getattr__ path.
    sm_class = mu.session.SessionManager
    instance = sm_class(session_name="lazy-export-test")
    assert hasattr(instance, "history")
    assert hasattr(instance, "summary_anchor")
    # Inherits HistoryMixin
    assert isinstance(instance, HistoryMixin)
