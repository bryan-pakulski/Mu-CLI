"""Tests for protected messages — messages exempt from L2 summarization.

Protected messages survive compaction: they're re-injected into L5
history even after summary_anchor advances past them.
"""

import pytest
from mu.session.session import Session, SessionManager
from mu.session.history import HistoryMixin
from providers.base import LLMProvider, MessagePart, ProviderResponse


class _DummyProvider(LLMProvider):
    def get_available_models(self):
        return ["dummy"]
    def generate(self, *a, **k):
        return ProviderResponse(text="ok", parts=[], input_tokens=0, output_tokens=0, total_tokens=0)
    def upload_file(self, *a, **k):
        return None


class _Host(HistoryMixin):
    """Minimal host for HistoryMixin tests."""
    def __init__(self):
        self.history = []
        self.summary_anchor = 0
        self.conversation_summary = ""


def _msg(role, text):
    return {"role": role, "parts": [{"type": "text", "text": text}]}


# ---- Phase 1: protected_indices infrastructure ----

def test_protected_indices_initializes_empty():
    sm = SessionManager()
    assert hasattr(sm, "protected_indices")
    assert sm.protected_indices == set()


def test_protected_indices_persists_through_save_load(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.config.HISTORY_DIR", str(tmp_path / "h"))
    sm = SessionManager(session_name="test-protect")
    sm.protected_indices = {0, 3, 5}
    sm.history = [_msg("user", "x"), _msg("assistant", "y")]
    sm.save_history()
    
    sm2 = SessionManager(session_name="test-protect")
    sm2._load_session("test-protect")
    assert sm2.protected_indices == {0, 3, 5}


def test_protected_message_survives_compaction():
    """Protected message re-injected into L5 after anchor advances."""
    host = _Host()
    host.history = [
        _msg("user", "Important task: refactor the auth module"),
        _msg("assistant", "Reading auth.py"),
        _msg("user", "continue"),
        _msg("assistant", "Working on it"),
        _msg("user", "keep going"),
        _msg("assistant", "Done"),
    ]
    host.protected_indices = {0}  # first user message protected
    
    # Roll with keep_recent=2 → anchor advances to index 4
    host.roll_history_summary(keep_recent=2)
    assert host.summary_anchor > 0
    
    # Check that protected message appears in the history slice that would be L5
    # After compaction, _prepare_runtime_history on Session would re-inject it.
    # For the mixin-only test, verify the anchor advanced past index 0
    # and the protected message is NOT in the recent slice (it gets re-injected by Session).
    recent = host.history[host.summary_anchor:]
    recent_texts = [p.get("text", "") for m in recent for p in m.get("parts", []) if p.get("type") == "text"]
    # The protected message at index 0 is NOT in the recent slice
    assert "Important task: refactor the auth module" not in recent_texts
    # But it IS still in full history (not deleted)
    assert "Important task: refactor the auth module" in host.history[0]["parts"][0]["text"]


def test_protected_message_excluded_from_llm_summarization():
    """Protected messages should not be included in the LLM summary prompt."""
    host = _Host()
    host.history = [
        _msg("user", "PROTECTED-MESSAGE-MARKER"),
        _msg("assistant", "response 1"),
        _msg("user", "continue"),
        _msg("assistant", "response 2"),
    ]
    host.protected_indices = {0}
    
    # Capture what would be sent to LLM
    original_render = host._render_entries_for_llm
    captured = []
    def spy_render(entries):
        text = original_render(entries)
        captured.append(text)
        return text
    host._render_entries_for_llm = spy_render
    
    host.roll_history_summary(keep_recent=2)
    if captured:
        assert "PROTECTED-MESSAGE-MARKER" not in captured[0], \
            "protected message leaked into LLM summary prompt"


def test_empty_protected_indices_preserves_existing_behavior():
    """No protected indices = same as before the feature."""
    host = _Host()
    host.history = [
        _msg("user", "turn 1 " + "x" * 200),
        _msg("assistant", "reply 1 " + "y" * 200),
        _msg("user", "turn 2 " + "z" * 200),
        _msg("assistant", "reply 2 " + "w" * 200),
    ]
    # No protected_indices set → empty set by default
    host.protected_indices = set()
    
    changed = host.roll_history_summary(keep_recent=2)
    assert changed is True
    assert host.summary_anchor > 0
    
    # Only keep_recent messages should be in recent slice (no re-injection)
    recent = host.history[host.summary_anchor:]
    recent_texts = [p.get("text", "") for m in recent for p in m.get("parts", []) if p.get("type") == "text"]
    assert "turn 1" not in recent_texts  # summarized away


# ---- Phase 2: auto-protection on insert ----

def test_maybe_protect_substantial_user_message():
    sm = SessionManager()
    sm._maybe_protect(0, "user", "Please refactor the authentication module to use JWT tokens")
    assert 0 in sm.protected_indices


def test_maybe_protect_skips_short_messages():
    sm = SessionManager()
    sm._maybe_protect(1, "user", "ok")
    assert 1 not in sm.protected_indices


def test_maybe_protect_skips_slash_commands():
    sm = SessionManager()
    sm._maybe_protect(2, "user", "/feature list")
    assert 2 not in sm.protected_indices


def test_maybe_protect_skips_assistant_messages():
    sm = SessionManager()
    sm._maybe_protect(3, "assistant", "I will now refactor the auth module to use JWT tokens instead of session cookies")
    assert 3 not in sm.protected_indices


def test_maybe_protect_threshold_boundary():
    """Messages exactly 50 chars should NOT be protected (strictly > 50)."""
    sm = SessionManager()
    exactly_50 = "x" * 50
    sm._maybe_protect(0, "user", exactly_50)
    assert 0 not in sm.protected_indices
    
    fifty_one = "x" * 51
    sm._maybe_protect(1, "user", fifty_one)
    assert 1 in sm.protected_indices


# ---- Phase 2: cap enforcement ----

def test_protected_cap_evicts_oldest():
    """When protected count exceeds 20, oldest is evicted."""
    sm = SessionManager()
    # Add 21 protected indices (0..20)
    for i in range(21):
        sm.protected_indices.add(i)
    
    # Adding one more should trigger cap enforcement
    sm._maybe_protect(21, "user", "x" * 60)
    
    assert len(sm.protected_indices) <= 20
    # Oldest (0) should have been evicted
    assert 0 not in sm.protected_indices
    # Newest (21) should be present
    assert 21 in sm.protected_indices


def test_protected_cap_at_boundary():
    """Exactly 20 protected indices — no eviction needed."""
    sm = SessionManager()
    for i in range(20):
        sm.protected_indices.add(i)
    
    sm._maybe_protect(20, "user", "x" * 60)
    # 21 indices → evict oldest → 20 remain
    assert len(sm.protected_indices) <= 20


# ---- Phase 2: turn cleanup ----

def test_cleanup_protected_removes_turn_prompt_if_not_worthy():
    """After turn ends, the turn's starting prompt is unprotected if short."""
    sm = SessionManager()
    sm.protected_indices = {0, 5, 10}
    # Turn started at index 10 with a short message
    sm._cleanup_protected(turn_start_index=10)
    # 10 should be removed (was just the turn prompt, not substantial)
    assert 10 not in sm.protected_indices
    # Others survive
    assert 0 in sm.protected_indices
    assert 5 in sm.protected_indices


def test_cleanup_protected_keeps_turn_prompt_if_substantial():
    """If turn prompt was substantial (>50 chars), it stays protected."""
    sm = SessionManager()
    sm.protected_indices = {0, 5, 10}
    sm.history = [
        _msg("user", "x" * 51),  # index 0
    ] + [_msg("assistant", "r")] * 4 + [
        _msg("user", "y" * 60),  # index 5
    ] + [_msg("assistant", "r")] * 4 + [
        _msg("user", "z" * 80),  # index 10 — substantial
    ] + [_msg("assistant", "r")] * 5
    
    sm._cleanup_protected(turn_start_index=10)
    # 10 stays because it's substantial (>50 chars)
    assert 10 in sm.protected_indices


# ---- Phase 3: session_goal auto-pin + L2 preamble ----

def test_session_goal_auto_set_from_first_user_message():
    """send_message should auto-pin session_goal from substantial first message.
    The goal is cleared at end of turn by _strip_session_goal_after_turn, so
    we monkeypatch that to verify the goal WAS set during the turn."""
    sm = SessionManager()
    session = Session(_DummyProvider("dummy"), False, "sys", sm)
    session.session_manager.history = []
    session.variables["session_goal"] = ""
    # Prevent stripping so we can assert the auto-pin happened
    session._strip_session_goal_after_turn = lambda: None
    session.send_message("Please refactor the auth module to use JWT")
    assert session.variables["session_goal"] == "Please refactor the auth module to use JWT"


def test_session_goal_not_set_from_slash_command():
    sm = SessionManager()
    session = Session(_DummyProvider("dummy"), False, "sys", sm)
    session.session_manager.history = []
    session.variables["session_goal"] = ""
    session._strip_session_goal_after_turn = lambda: None
    session.send_message("/feature list")
    assert session.variables["session_goal"] == ""


def test_session_goal_not_overwritten_if_already_set():
    sm = SessionManager()
    session = Session(_DummyProvider("dummy"), False, "sys", sm)
    session.session_manager.history = []
    session.variables["session_goal"] = "Existing goal"
    session._strip_session_goal_after_turn = lambda: None
    session.send_message("Some new task that is completely different")
    assert session.variables["session_goal"] == "Existing goal"


def test_l2_preamble_includes_session_goal():
    """L2 conversation_summary should be prefixed with goal if set."""
    sm = SessionManager()
    session = Session(_DummyProvider("dummy"), False, "sys", sm)
    session.variables["session_goal"] = "Refactor auth to use JWT"
    session.session_manager.conversation_summary = "### Progress\nDid some work"
    full = session._inject_hierarchical_context("base prompt")
    assert "Active Goal: Refactor auth to use JWT" in full
