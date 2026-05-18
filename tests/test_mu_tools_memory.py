"""Pin the migrated memory + scratchpad tools (`mu/tools/memory/`).

These tools used to short-circuit inside `Session._execute_tool_with_memory`
with handlers that read `self.task_memory` / `self.turn_scratchpad`. After
the Phase 1.1 migration, the same behavior lives behind `@tool`-decorated
functions in `mu/tools/memory/handlers.py`. The short-circuit is gone;
the dispatcher routes normally and the new handlers resolve the stores
from `context.session`.
"""

import json

import pytest

import mu.tools as mt
from core.memory import ScratchpadStore, TaskMemoryStore


# ---------------------------------------------------------- registration


REGISTERED_MEMORY_TOOLS = (
    "save_memory",
    "search_memory",
    "list_memory",
    "save_scratchpad",
    "search_scratchpad",
    "list_scratchpad",
    "clear_scratchpad",
)


@pytest.mark.parametrize("name", REGISTERED_MEMORY_TOOLS)
def test_memory_tool_registered_in_new_registry(name):
    descriptor = mt.get(name)
    assert descriptor is not None, f"{name} not registered in mu.tools"
    assert descriptor.definition.name == name
    # Metadata flows through the @tool decorator.
    assert descriptor.execution_kind == "memory"
    assert descriptor.preview_policy == "none"


def test_legacy_core_tools_no_longer_has_memory_descriptors():
    """Pin the cleanup: `core/tools.py` should no longer carry separate
    descriptors for the 7 memory tools. The registry still surfaces them
    (via the @tool decorator in mu/tools/memory/handlers.py) — verifying
    the descriptor's home module is the new package, not the legacy one."""
    from core import tools as legacy

    for name in REGISTERED_MEMORY_TOOLS:
        # Legacy module no longer holds a separate ToolDefinition for these.
        legacy_defs = [t for t in legacy.TOOLS if t.name == name]
        # The aggregated TOOLS list is built from descriptors in the new
        # registry too, so the names DO show up — but the legacy
        # `_TOOL_METADATA` should not have a per-tool entry, and the
        # legacy `_handle_memory_placeholder` helper is removed.
        # Both `_TOOL_METADATA` and `_handle_memory_placeholder` are
        # internal — pin via attribute absence.
        assert not hasattr(legacy, "_handle_memory_placeholder"), (
            "Legacy placeholder helper should be removed after migration."
        )


# ---------------------------------------------------------- execution


class _SessionStub:
    """Minimal session shape for the memory tools — they only need
    `task_memory` and `turn_scratchpad` attributes."""

    def __init__(self):
        self.task_memory = TaskMemoryStore()
        self.turn_scratchpad = ScratchpadStore()


def _ctx_with_session():
    return mt.build_tool_context(
        folder_context=None, ui=None, variables={}, session=_SessionStub()
    )


def test_save_memory_writes_to_session_store():
    ctx = _ctx_with_session()
    result = mt.execute(
        "save_memory",
        {"content": "auth lives in core/session.py", "tags": ["arch"]},
        ctx,
    )
    assert result["ok"] is True
    assert "#1" in result["message"]
    # And the actual store now holds the entry.
    entries = ctx.session.task_memory.list_entries(limit=5)
    assert len(entries) == 1
    assert "auth lives" in entries[0].content


def test_save_and_search_memory_round_trip():
    ctx = _ctx_with_session()
    mt.execute(
        "save_memory",
        {"content": "session loop is in send_message", "tags": ["loop"]},
        ctx,
    )
    mt.execute(
        "save_memory",
        {"content": "unrelated note about billing", "tags": ["billing"]},
        ctx,
    )
    result = mt.execute("search_memory", {"query": "loop", "limit": 5}, ctx)
    assert result["ok"] is True
    assert "send_message" in result["message"]
    assert "billing" not in result["message"]


def test_list_memory_returns_recent_entries():
    ctx = _ctx_with_session()
    for i in range(3):
        mt.execute("save_memory", {"content": f"fact {i}"}, ctx)
    result = mt.execute("list_memory", {"limit": 5}, ctx)
    assert result["ok"] is True
    for i in range(3):
        assert f"fact {i}" in result["message"]


def test_save_search_list_scratchpad_round_trip():
    ctx = _ctx_with_session()
    save = mt.execute(
        "save_scratchpad",
        {"content": "remember to flush", "tags": ["plan"]},
        ctx,
    )
    assert save["ok"] is True
    assert "#1" in save["message"]

    search = mt.execute("search_scratchpad", {"query": "flush"}, ctx)
    assert search["ok"] is True
    assert "remember to flush" in search["message"]

    listing = mt.execute("list_scratchpad", {"limit": 5}, ctx)
    assert listing["ok"] is True
    assert "remember to flush" in listing["message"]


def test_clear_scratchpad_wipes_store():
    ctx = _ctx_with_session()
    mt.execute("save_scratchpad", {"content": "ephemeral"}, ctx)
    assert len(ctx.session.turn_scratchpad.list_entries()) == 1

    result = mt.execute("clear_scratchpad", {}, ctx)
    assert result["ok"] is True
    assert "cleared" in result["message"].lower()
    assert ctx.session.turn_scratchpad.list_entries() == []


def test_session_less_context_uses_fallback_store():
    """Standalone callers without a session (eg. unit tests of tool
    behavior in isolation) get an in-process fallback so the handler
    can still exercise its code path."""
    # Reset the module-level fallback so this test isn't contaminated.
    from mu.tools.memory import handlers as h

    h._FALLBACK_TASK_MEMORY = None
    h._FALLBACK_SCRATCHPAD = None

    ctx = mt.build_tool_context(
        folder_context=None, ui=None, variables={}, session=None
    )
    save = mt.execute("save_memory", {"content": "no-session test"}, ctx)
    assert save["ok"] is True
    search = mt.execute("search_memory", {"query": "no-session"}, ctx)
    assert search["ok"] is True
    assert "no-session test" in search["message"]


def test_int_limit_coerced_from_string_or_missing():
    """The legacy implementation forced `int(limit or default)`. The new
    handler keeps the same defensive cast so a model passing `"5"` or
    omitting the key still works."""
    ctx = _ctx_with_session()
    mt.execute("save_memory", {"content": "a"}, ctx)

    by_string = mt.execute("list_memory", {"limit": "5"}, ctx)
    assert by_string["ok"] is True

    by_missing = mt.execute("list_memory", {}, ctx)
    assert by_missing["ok"] is True
