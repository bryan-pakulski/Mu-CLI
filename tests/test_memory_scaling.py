"""Tests for memory scaling: kind-aware eviction, capacity, persistence, scratchpad flag."""

import json
import os
import time
from types import SimpleNamespace

import pytest

from mu.memory.stores import BaseNoteStore, MemoryEntry, ScratchpadStore, TaskMemoryStore


# ============================================================ Kind-aware eviction


def test_eviction_score_decision_gets_3x_weight():
    store = TaskMemoryStore(max_entries=10)
    decision = MemoryEntry(id=1, content="dec", kind="decision", hits=1)
    observation = MemoryEntry(id=2, content="obs", kind="observation", hits=1)
    # (hits + 1) * kind_weight: (1+1)*3.0 = 6.0, (1+1)*1.0 = 2.0
    assert store._eviction_score(decision) == 6.0
    assert store._eviction_score(observation) == 2.0


def test_eviction_score_finding_gets_2x_weight():
    store = TaskMemoryStore(max_entries=10)
    finding = MemoryEntry(id=1, content="find", kind="finding", hits=1)
    # (1+1)*2.0 = 4.0
    assert store._eviction_score(finding) == 4.0


def test_eviction_score_unknown_kind_defaults_to_1x():
    store = TaskMemoryStore(max_entries=10)
    entry = MemoryEntry(id=1, content="x", kind="custom_kind", hits=1)
    # (1+1)*1.0 = 2.0
    assert store._eviction_score(entry) == 2.0


def test_decision_outlives_observation_in_eviction():
    """When eviction is needed, observation entries should be evicted
    before decision entries, even if both have the same hits."""
    store = TaskMemoryStore(max_entries=3)
    store.save("decision content", kind="decision")
    store.save("observation content 1", kind="observation")
    store.save("observation content 2", kind="observation")
    # Now add one more to trigger eviction (max_entries=3).
    store.save("new observation", kind="observation")

    contents = [e.content for e in store.entries]
    # The decision should still be there — it was not evicted.
    assert "decision content" in contents
    # At least one observation was evicted.
    assert len(store.entries) == 3


def test_eviction_kind_weights_configurable():
    """Custom weights should override defaults."""
    store = TaskMemoryStore(max_entries=10)
    store.eviction_kind_weights = {"critical": 10.0, "normal": 1.0}
    critical = MemoryEntry(id=1, content="crit", kind="critical", hits=1)
    normal = MemoryEntry(id=2, content="norm", kind="normal", hits=1)
    # (hits + 1) * kind_weight: (1+1)*10.0 = 20.0, (1+1)*1.0 = 2.0
    assert store._eviction_score(critical) == 20.0
    assert store._eviction_score(normal) == 2.0


# ============================================================ Capacity


def test_task_memory_store_max_entries_is_1024():
    store = TaskMemoryStore()
    assert store.max_entries == 1024


def test_task_memory_store_summary_char_limit_is_16000():
    store = TaskMemoryStore()
    assert store.summary_char_limit == 16000


def test_scratchpad_store_max_entries_is_256():
    store = ScratchpadStore()
    assert store.max_entries == 256


def test_scratchpad_store_summary_char_limit_is_8000():
    store = ScratchpadStore()
    assert store.summary_char_limit == 8000


# ============================================================ Scratchpad persistence flag


def test_scratchpad_persist_variable_in_schema():
    from utils.config import VARIABLE_SCHEMA

    assert "scratchpad_persist_across_turns" in VARIABLE_SCHEMA
    assert VARIABLE_SCHEMA["scratchpad_persist_across_turns"]["type"] is bool
    assert VARIABLE_SCHEMA["scratchpad_persist_across_turns"]["default"] is False


def test_scratchpad_persist_flag_gates_clear_in_loop_body():
    """Verify loop_body.py gates turn_scratchpad.clear() on the flag."""
    import inspect
    from mu.agent import loop_body

    src = inspect.getsource(loop_body.run_turn)
    assert "scratchpad_persist_across_turns" in src
    assert "turn_scratchpad.clear()" in src
    # The clear should be inside an if block that checks the flag.
    # Verify it's gated (not unconditional).
    clear_pos = src.index("turn_scratchpad.clear()")
    flag_pos = src.index("scratchpad_persist_across_turns")
    assert flag_pos < clear_pos


# ============================================================ /memory save/load round-trip


def _make_session(tmp_path, monkeypatch):
    """Build a minimal session stub with task_memory for /memory command tests."""
    monkeypatch.setattr("utils.config.HISTORY_DIR", str(tmp_path / "mucli"))
    from mu.session.session import Session, SessionManager
    from providers.base import LLMProvider, ProviderResponse

    class _DummyProvider(LLMProvider):
        def get_available_models(self):
            return ["dummy"]
        def generate(self, *a, **k):
            return ProviderResponse(text="", parts=[])
        def upload_file(self, *a, **k):
            return None

    sm = SessionManager()
    s = Session(_DummyProvider("dummy"), False, "sys", sm)
    s._mcp_clients = []
    s.session_manager.history = []
    s.session_manager.summary_anchor = 0
    s.session_manager.conversation_summary = ""
    return s


def test_memory_save_creates_json_file(tmp_path, monkeypatch):
    session = _make_session(tmp_path, monkeypatch)
    session.task_memory.save("important fact", tags=["arch"], kind="decision")
    session.task_memory.save("file location: src/main.py", tags=["path"])

    from mu.commands.memory import memory_cmd

    result = memory_cmd(session, "save test-snapshot", allow_prompt=False)
    assert result.ok
    assert result.data["saved_count"] == 2

    filepath = result.data["filepath"]
    assert os.path.isfile(filepath)
    with open(filepath, "r") as fh:
        data = json.load(fh)
    assert "entries" in data
    assert "saved_at" in data
    assert "session" in data
    assert len(data["entries"]) == 2


def test_memory_load_merges_into_task_memory(tmp_path, monkeypatch):
    session = _make_session(tmp_path, monkeypatch)
    session.task_memory.save("original fact", tags=["tag1"])

    from mu.commands.memory import memory_cmd

    # Save
    memory_cmd(session, "save test-load", allow_prompt=False)
    # Clear task memory
    session.task_memory.clear()
    assert len(session.task_memory.entries) == 0

    # Load
    result = memory_cmd(session, "load test-load", allow_prompt=False)
    assert result.ok
    assert result.data["loaded_count"] == 1
    assert len(session.task_memory.entries) == 1
    assert session.task_memory.entries[0].content == "original fact"


def test_memory_list_saved_shows_files(tmp_path, monkeypatch):
    session = _make_session(tmp_path, monkeypatch)
    session.task_memory.save("fact 1")
    session.task_memory.save("fact 2")

    from mu.commands.memory import memory_cmd

    memory_cmd(session, "save snapshot-a", allow_prompt=False)
    memory_cmd(session, "save snapshot-b", allow_prompt=False)

    result = memory_cmd(session, "list saved", allow_prompt=False)
    assert result.ok
    names = {item["name"] for item in result.data["saved_files"]}
    assert "snapshot-a" in names
    assert "snapshot-b" in names


def test_memory_clear_saved_removes_files(tmp_path, monkeypatch):
    session = _make_session(tmp_path, monkeypatch)
    session.task_memory.save("fact 1")

    from mu.commands.memory import memory_cmd

    memory_cmd(session, "save to-clear", allow_prompt=False)
    result = memory_cmd(session, "clear saved", allow_prompt=False)
    assert result.ok
    assert result.data["cleared_count"] == 1

    # Verify files are gone
    list_result = memory_cmd(session, "list saved", allow_prompt=False)
    assert list_result.data["saved_files"] == []


def test_memory_save_without_name_errors(tmp_path, monkeypatch):
    session = _make_session(tmp_path, monkeypatch)
    from mu.commands.memory import memory_cmd

    result = memory_cmd(session, "save", allow_prompt=False)
    assert not result.ok
    assert "Usage" in result.message


def test_memory_load_nonexistent_errors(tmp_path, monkeypatch):
    session = _make_session(tmp_path, monkeypatch)
    from mu.commands.memory import memory_cmd

    result = memory_cmd(session, "load nonexistent", allow_prompt=False)
    assert not result.ok


def test_memory_load_dedup_does_not_duplicate(tmp_path, monkeypatch):
    """Loading a saved memory when entries already exist should not
    create duplicates — BaseNoteStore.save() dedups by content+tags."""
    session = _make_session(tmp_path, monkeypatch)
    session.task_memory.save("shared fact", tags=["common"])

    from mu.commands.memory import memory_cmd

    memory_cmd(session, "save dedup-test", allow_prompt=False)
    # Load again — should not duplicate the entry.
    memory_cmd(session, "load dedup-test", allow_prompt=False)
    assert len(session.task_memory.entries) == 1