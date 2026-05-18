from mu.memory.stores import ScratchpadStore, TaskMemoryStore


def test_task_memory_save_search_and_summary():
    store = TaskMemoryStore(max_entries=3, summary_char_limit=500)

    first = store.save(
        "Parser lives in core/session.py and owns the agent loop.",
        tags=["parser", "agent"],
        source="read_file",
    )
    store.save("Collation buffer stores read-only tool outputs.", tags=["collation"])
    store.save("Use flush after gathering context.", tags=["workflow"])

    results = store.search("parser agent", limit=2)
    assert results
    assert results[0].id == first.id

    summary = store.render_summary(limit=5)
    assert "In-Task Memory" in summary
    assert "core/session.py" in summary


def test_task_memory_evicts_low_value_entries():
    store = TaskMemoryStore(max_entries=2)

    first = store.save("first memory")
    store.save("second memory")
    store.save("third memory")

    ids = [entry.id for entry in store.entries]
    assert first.id not in ids
    assert len(store.entries) == 2


def test_scratchpad_store_can_clear_turn_local_notes():
    store = ScratchpadStore(max_entries=4)
    store.save("temporary plan", tags=["plan"])
    store.save("temporary finding", tags=["finding"])

    assert len(store.list_entries()) == 2
    assert "Turn Scratchpad" in store.render_summary(limit=5)

    store.clear()
    assert store.list_entries() == []
