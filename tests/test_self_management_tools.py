"""Tests for the agent self-management tools.

Covers the four mechanism gaps that let the agent own its context instead
of the harness shoehorning it:

  - `todo_delete` / `todo_clear` — prune the persistent todo ledger
    (tests in test_mu_todo.py for the tool surface; here for the
    turn-start carve-out that makes the ledger persist).
  - `context_status` — per-layer token fill + L2-staleness signal.
  - `checkpoint_progress` — agent-callable L2 fold.
  - `retire_thread` — drop an abandoned thread (archive active memory +
    clear matching scratchpad notes).
"""

import json

import pytest

import mu.tools as mt
from mu.memory.stores import ScratchpadStore, TaskMemoryStore


# ----------------------------------------------------------- session stubs


def _make_session():
    """Real Session + SessionManager so collect_context_layers and
    force_progress_checkpoint work end-to-end. The provider returns a
    structured Progress summary so checkpoints merge cleanly."""
    from mu.session.session import Session, SessionManager
    from providers.base import LLMProvider, ProviderResponse

    class _DummyProvider(LLMProvider):
        def __init__(self):
            super().__init__()
            self.name = "dummy"
            self.model_name = "dummy"

        def get_available_models(self):
            return ["dummy"]

        def generate(self, messages, system_prompt=None, thinking=False, tools=None):
            return ProviderResponse(
                text="### Progress\nfolded recent work into L2.",
                parts=[],
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
            )

        def upload_file(self, file_path, mime_type):
            return None

    sm = SessionManager()
    return Session(_DummyProvider(), False, "you are a helpful assistant", sm)


def _ctx(session):
    return mt.build_tool_context(
        folder_context=None, ui=None, variables=getattr(session, "variables", {}),
        session=session,
    )


def _add_turn(session, role, text):
    session.session_manager.history.append(
        {"role": role, "parts": [{"type": "text", "text": text}]}
    )


# --------------------------------------------------- clear_excluding (carve-out)


def test_clear_excluding_preserves_tagged_entries():
    """The turn-start carve-out: clear_excluding({'todo'}) drops ephemeral
    notes but keeps the todo ledger across turns."""
    store = ScratchpadStore()
    store.save("task A", tags=["todo", "status:in_progress"])
    store.save("task B", tags=["todo", "status:pending"])
    store.save("ephemeral plan", tags=["plan"])
    store.save("loose note", tags=[])

    removed = store.clear_excluding({"todo"})

    assert removed == 2  # the two non-todo entries
    remaining = store.entries
    assert len(remaining) == 2
    assert all("todo" in e.tags for e in remaining)
    # _next_id stays ahead of retained entries so new saves don't collide.
    max_kept_id = max(e.id for e in remaining)
    store.save("task C", tags=["todo"])
    assert store.entries[-1].id > max_kept_id


def test_clear_excluding_empty_set_clears_all():
    store = ScratchpadStore()
    store.save("a", tags=["todo"])
    store.save("b", tags=["plan"])
    assert store.clear_excluding(set()) == 2
    assert store.entries == []


def test_clear_excluding_no_matches_clears_all():
    store = ScratchpadStore()
    store.save("a", tags=["plan"])
    # No entry carries the protected tag → behaves like full clear.
    assert store.clear_excluding({"todo"}) == 1
    assert store.entries == []


# ------------------------------------------------------------- context_status


def test_context_status_reports_layers_and_self_mgmt():
    session = _make_session()
    ctx = _ctx(session)
    res = mt.execute("context_status", {}, ctx)
    assert res["ok"] is True
    data = res["data"]
    assert "layers" in data
    assert "self_management" in data
    # Layer ids cover the canonical stack.
    ids = [l["layer"] for l in data["layers"]]
    for lid in ("L0", "L1", "L1B", "L2", "L3", "L4B", "L5"):
        assert lid in ids
    sm = data["self_management"]
    assert "uncheckpointed_entries" in sm
    assert "l2_stale_vs_l5" in sm
    assert "todo_count" in sm
    assert "memory_entries" in sm


def test_context_status_l2_stale_after_many_uncheckpointed_entries():
    session = _make_session()
    # Seed 14 history entries without checkpointing → l2_stale_vs_l5 True.
    for i in range(14):
        _add_turn(session, "user", f"turn {i} alpha beta gamma")
    ctx = _ctx(session)
    res = mt.execute("context_status", {}, ctx)
    sm = res["data"]["self_management"]
    assert sm["uncheckpointed_entries"] >= 12
    assert sm["l2_stale_vs_l5"] is True


def test_context_status_no_session_returns_error():
    ctx = mt.build_tool_context(
        folder_context=None, ui=None, variables={}, session=None
    )
    res = mt.execute("context_status", {}, ctx)
    # result_mode=raw → the handler returns a JSON string; execute wraps it.
    body = res.get("data") if isinstance(res.get("data"), str) else res.get("message", "")
    assert "No session" in (body if isinstance(body, str) else json.dumps(res))


# ---------------------------------------------------------- checkpoint_progress


def test_checkpoint_progress_noop_with_too_little_history():
    session = _make_session()
    _add_turn(session, "user", "just one turn")
    ctx = _ctx(session)
    res = mt.execute("checkpoint_progress", {}, ctx)
    assert res["ok"] is True
    assert res["data"]["updated"] is False


def test_checkpoint_progress_folds_when_enough_history():
    session = _make_session()
    for i in range(8):
        _add_turn(session, "user", f"turn {i} do something meaningful here")
    ctx = _ctx(session)
    res = mt.execute("checkpoint_progress", {}, ctx)
    assert res["ok"] is True
    assert res["data"]["updated"] is True
    # After a checkpoint, context_status should report fewer uncheckpointed.
    status = mt.execute("context_status", {}, _ctx(session))
    assert status["data"]["self_management"]["uncheckpointed_entries"] < 8


def test_checkpoint_progress_is_idempotent_second_call_noop():
    session = _make_session()
    for i in range(8):
        _add_turn(session, "user", f"turn {i} do something meaningful here")
    ctx = _ctx(session)
    first = mt.execute("checkpoint_progress", {}, ctx)
    assert first["data"]["updated"] is True
    # No new history since → second call is a no-op.
    second = mt.execute("checkpoint_progress", {}, ctx)
    assert second["data"]["updated"] is False


# --------------------------------------------------------------- retire_thread


def _mem_ctx(session=None):
    sess = session or _MemSession()
    return mt.build_tool_context(
        folder_context=None, ui=None, variables={}, session=sess
    )


class _MemSession:
    """Stub with task_memory + turn_scratchpad for the memory tools."""

    def __init__(self):
        self.task_memory = TaskMemoryStore()
        self.turn_scratchpad = ScratchpadStore()


def test_retire_thread_archives_matching_active_memory():
    ctx = _mem_ctx()
    mt.execute("save_memory", {"content": "auth refactor notes", "tags": ["auth"]}, ctx)
    mt.execute("save_memory", {"content": "unrelated billing finding", "tags": ["billing"]}, ctx)

    res = mt.execute("retire_thread", {"topic": "auth", "reason": "hypothesis disproved"}, ctx)
    assert res["ok"] is True
    assert res["data"]["archived_memory_ids"]  # at least the auth entry
    # The auth entry is now archived (not active); billing stays active.
    auth = next(e for e in ctx.session.task_memory.entries if "auth" in e.content)
    billing = next(e for e in ctx.session.task_memory.entries if "billing" in e.content)
    assert auth.status == "archived"
    assert billing.status == "active"
    # An audit entry was recorded (archived, tagged retired/abandoned).
    audit = [e for e in ctx.session.task_memory.entries if "retired" in e.tags]
    assert len(audit) == 1
    assert audit[0].status == "archived"


def test_retire_thread_clears_matching_scratchpad_notes():
    ctx = _mem_ctx()
    mt.execute("save_scratchpad", {"content": "auth hypothesis: maybe X", "tags": ["auth"]}, ctx)
    mt.execute("save_scratchpad", {"content": "unrelated plan", "tags": ["plan"]}, ctx)
    # A todo entry whose content happens to contain the topic must survive
    # (retire_thread never touches the todo ledger).
    ctx.session.turn_scratchpad.save("auth todo still relevant", tags=["todo"])

    res = mt.execute("retire_thread", {"topic": "auth"}, ctx)
    assert res["ok"] is True
    assert res["data"]["scratchpad_removed"] == 1

    contents = [e.content for e in ctx.session.turn_scratchpad.entries]
    assert "unrelated plan" in contents
    assert "auth todo still relevant" in contents
    assert "auth hypothesis: maybe X" not in contents


def test_retire_thread_can_skip_scratchpad_clear():
    ctx = _mem_ctx()
    mt.execute("save_scratchpad", {"content": "auth note", "tags": ["auth"]}, ctx)
    res = mt.execute("retire_thread", {"topic": "auth", "clear_scratchpad": False}, ctx)
    assert res["ok"] is True
    assert res["data"]["scratchpad_removed"] == 0
    assert any(e.content == "auth note" for e in ctx.session.turn_scratchpad.entries)


def test_retire_thread_requires_topic():
    ctx = _mem_ctx()
    res = mt.execute("retire_thread", {"topic": "   "}, ctx)
    assert res["ok"] is False
    assert res["error_code"] == "invalid_args"


# ----------------------------------------------- prompt block + tool registration


def test_self_management_tools_registered():
    for name in ("todo_delete", "todo_clear", "context_status", "checkpoint_progress", "retire_thread"):
        assert mt.get(name) is not None, f"{name} not registered"


def test_agnostic_system_base_has_self_management_block():
    from utils.config import AGENTIC_SYSTEM_BASE

    assert "SELF-MANAGEMENT" in AGENTIC_SYSTEM_BASE
    assert "YOU OWN YOUR CONTEXT" in AGENTIC_SYSTEM_BASE
    # The new tools are named in the tool surface.
    for name in ("todo_delete", "todo_clear", "context_status", "checkpoint_progress", "retire_thread"):
        assert name in AGENTIC_SYSTEM_BASE


# --------------------------------------------------------- staleness decay


from mu.memory.stores import TaskMemoryStore, STALE, ACTIVE, DONE, ARCHIVED


def test_advance_turn_increments_counter():
    store = TaskMemoryStore()
    assert store.turn_count == 0
    store.advance_turn()
    store.advance_turn()
    assert store.turn_count == 2


def test_decay_demotes_active_after_n_turns():
    """An active entry not hit in `stale_after_turns` turns → STALE."""
    store = TaskMemoryStore()
    store.save("auth finding", tags=["auth"], kind="finding")
    # last_hit_turn = 0 (turn_count was 0 at save time).
    for _ in range(13):
        store.advance_turn()
    demoted = store.apply_staleness_decay(12)
    assert demoted == 1
    assert store.entries[0].status == STALE


def test_decay_spared_recently_hit_entry():
    """A search hit refreshes last_hit_turn, sparing the entry from decay."""
    store = TaskMemoryStore()
    store.save("auth finding", tags=["auth"], kind="finding")
    for _ in range(8):
        store.advance_turn()
    # Hit it at turn 8 → last_hit_turn = 8.
    store.search("auth", limit=1)
    assert store.entries[0].last_hit_turn == 8
    for _ in range(4):  # advance to turn 12
        store.advance_turn()
    # 12 - 8 = 4 < 12 → still active.
    demoted = store.apply_staleness_decay(12)
    assert demoted == 0
    assert store.entries[0].status == ACTIVE


def test_search_reactivates_stale_entry():
    """Decay is reversible through use: a search hit promotes STALE → ACTIVE."""
    store = TaskMemoryStore()
    store.save("auth finding", tags=["auth"], kind="finding")
    for _ in range(13):
        store.advance_turn()
    store.apply_staleness_decay(12)
    assert store.entries[0].status == STALE
    # An explicit search hit reactivates it.
    results = store.search("auth", limit=1)
    assert results and results[0].status == ACTIVE
    assert store.entries[0].status == ACTIVE
    assert store.entries[0].last_hit_turn == store.turn_count


def test_save_dedup_reactivates_stale_entry():
    """Re-saving identical content is active reliance → STALE promoted back."""
    store = TaskMemoryStore()
    store.save("auth finding", tags=["auth"], kind="finding")
    for _ in range(13):
        store.advance_turn()
    store.apply_staleness_decay(12)
    assert store.entries[0].status == STALE
    store.save("auth finding", tags=["auth"], kind="finding")
    assert store.entries[0].status == ACTIVE


def test_decay_disabled_when_zero():
    store = TaskMemoryStore()
    store.save("x")
    for _ in range(50):
        store.advance_turn()
    assert store.apply_staleness_decay(0) == 0
    assert store.entries[0].status == ACTIVE


def test_decay_skips_non_active_statuses():
    """Only ACTIVE entries decay; done/archived/superseded are left alone."""
    store = TaskMemoryStore()
    store.save("done one", status=DONE)
    store.save("archived one", status=ARCHIVED)
    for _ in range(20):
        store.advance_turn()
    demoted = store.apply_staleness_decay(12)
    assert demoted == 0
    statuses = {e.content: e.status for e in store.entries}
    assert statuses["done one"] == DONE
    assert statuses["archived one"] == ARCHIVED


def test_decay_threshold_boundary():
    """last_hit_turn == threshold (exactly stale_after_turns ago) → demoted."""
    store = TaskMemoryStore()
    store.save("boundary")  # last_hit_turn = 0
    for _ in range(12):
        store.advance_turn()  # turn_count = 12, threshold = 0
    # last_hit_turn(0) <= threshold(0) → demoted.
    assert store.apply_staleness_decay(12) == 1
    assert store.entries[0].status == STALE


def test_last_hit_turn_and_turn_count_round_trip():
    """Persistence: last_hit_turn (entry) and turn_count (store) survive save/load."""
    store = TaskMemoryStore()
    store.save("fact")
    store.advance_turn()
    store.advance_turn()
    store.search("fact", limit=1)
    assert store.turn_count == 2
    assert store.entries[0].last_hit_turn == 2
    data = store.to_dict()
    restored = TaskMemoryStore.from_dict(data)
    assert restored.turn_count == 2
    assert restored.entries[0].last_hit_turn == 2


def test_stale_entry_findable_and_reactivated_by_default_search():
    """Default search surfaces ACTIVE + STALE and reactivates stale on hit.
    Done/superseded/archived stay excluded from the default view."""
    store = TaskMemoryStore()
    store.save("auth finding", tags=["auth"])
    for _ in range(13):
        store.advance_turn()
    store.apply_staleness_decay(12)
    assert store.entries[0].status == STALE
    # Default search finds the stale entry AND reactivates it.
    results = store.search("auth", limit=5)
    assert len(results) == 1
    assert results[0].status == ACTIVE
    assert store.entries[0].status == ACTIVE

    # Done entries stay excluded from the default view.
    store.save("done finding", tags=["auth"], status=DONE)
    store.entries[-1].status = DONE
    done_results = store.search("done", limit=5)
    assert all(r.status != DONE for r in done_results)
    # ...but surface via a status filter.
    done_only = store.search("done", limit=5, status_filter="done")
    assert any(r.status == DONE for r in done_only)


# ------------------------------------------- context_status staleness signals


def test_context_status_reports_staleness_signals():
    session = _make_session()
    # Seed a memory entry, then age it past the decay threshold so it goes stale.
    session.task_memory.save("old auth finding", tags=["auth"], kind="finding")
    for _ in range(13):
        session.task_memory.advance_turn()
    session.task_memory.apply_staleness_decay(12)
    # Seed a fresh active memory + a completed todo (stale todo target).
    session.task_memory.save("fresh billing note", tags=["billing"], kind="observation")
    ctx = _ctx(session)
    # Add a completed todo and an in_progress todo directly to the scratchpad.
    session.turn_scratchpad.save("done task", tags=["todo", "status:completed"])
    session.turn_scratchpad.save("wip task", tags=["todo", "status:in_progress"])

    res = mt.execute("context_status", {}, ctx)
    sm = res["data"]["self_management"]
    assert sm["stale_memory_count"] == 1
    assert sm["active_memory"] == 1
    assert sm["stale_todos"] == 1
    assert sm["in_progress_todos"] == 1
    assert sm["memory_pressure_pct"] >= 0.0


def test_context_status_clean_when_nothing_stale():
    session = _make_session()
    ctx = _ctx(session)
    res = mt.execute("context_status", {}, ctx)
    sm = res["data"]["self_management"]
    assert sm["stale_memory_count"] == 0
    assert sm["stale_todos"] == 0
    assert sm["active_memory"] == 0


# --------------------------------------------------- prompt block: decay + supersede


def test_self_management_block_has_decay_and_supersede_rules():
    from utils.config import AGENTIC_SYSTEM_BASE

    assert "Supersede, don't sibling" in AGENTIC_SYSTEM_BASE
    assert "Decay keeps the active set honest" in AGENTIC_SYSTEM_BASE
    assert "stale_memory_count" in AGENTIC_SYSTEM_BASE
    assert "memory_pressure_pct" in AGENTIC_SYSTEM_BASE
    assert "stale_todos" in AGENTIC_SYSTEM_BASE


def test_memory_stale_after_turns_in_schema():
    from utils.config import VARIABLE_SCHEMA

    assert "memory_stale_after_turns" in VARIABLE_SCHEMA
    assert VARIABLE_SCHEMA["memory_stale_after_turns"]["type"] is int
    assert VARIABLE_SCHEMA["memory_stale_after_turns"]["default"] == 12


def test_loop_body_applies_decay_at_turn_start():
    """loop_body advances the turn counter + applies decay at turn start."""
    import inspect
    from mu.agent import loop_body

    src = inspect.getsource(loop_body.run_turn)
    assert "advance_turn" in src
    assert "apply_staleness_decay" in src
    assert "memory_stale_after_turns" in src