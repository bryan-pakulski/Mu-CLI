from types import SimpleNamespace

from rich.console import Console

from ui.rich_ui import RichUI


def _build_session(**overrides):
    token_counts = {"input": 120, "output": 80, "total": 200, "total_cost": 0.01}
    session = SimpleNamespace(
        session_manager=SimpleNamespace(
            history=[{"role": "user", "parts": []}] * 12,
            summary_anchor=3,
            token_counts=token_counts,
        ),
        active_context_window=20,
        task_memory=SimpleNamespace(entries=[1, 2, 3], max_entries=64),
        turn_scratchpad=SimpleNamespace(entries=[1], max_entries=24),
        collation_buffer=SimpleNamespace(
            entries=[("read_file", {}, "abc"), ("search", {}, "12345")],
            max_bytes=100,
        ),
        variables={
            "memory_max_entries": 64,
            "scratchpad_max_entries": 24,
            "agent_mode": "feature",
        },
    )
    for key, value in overrides.items():
        setattr(session, key, value)
    return session


def test_build_meter_shows_capacity_and_clamps_fill():
    ui = RichUI()

    meter = ui.build_meter("MEM", current=12, maximum=10, color="magenta", width=10)

    assert "MEM" in meter.plain
    assert "12/10" in meter.plain
    assert meter.plain.count("█") == 10


def test_memory_monitor_renders_context_memory_and_queue_labels():
    ui = RichUI()
    session = _build_session()
    console = Console(record=True, width=100)

    console.print(ui.build_memory_monitor(session))
    output = console.export_text()

    assert "Memory HUD" in output
    assert "CTX" in output
    assert "MEM" in output
    assert "SCRATCH" in output
    assert "QUEUE" in output
    assert "tokens 200" in output
    assert "queue 2 items" in output
    assert "mode" in output
    assert "feature" in output
