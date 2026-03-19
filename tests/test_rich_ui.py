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




def test_render_message_titles_include_timestamps():
    ui = RichUI()
    ui.console = Console(record=True, width=100)
    ui._timestamp = lambda: "12:34:56"

    ui.render_message("user", "hello")
    ui.render_message("assistant", "response", model_name="gpt-test")
    output = ui.console.export_text()

    assert "User • 12:34:56" in output
    assert "Assistant (gpt-test) • 12:34:56" in output

def test_refresh_memory_monitor_prints_when_live_is_inactive():
    ui = RichUI()
    session = _build_session()
    printed = []

    def fake_print(renderable):
        printed.append(renderable)

    ui.console.print = fake_print

    ui.refresh_memory_monitor(session)

    assert len(printed) == 1
    assert printed[0] is not None


def test_live_memory_monitor_updates_in_place(monkeypatch):
    events = []

    class FakeLive:
        def __init__(self, renderable, console, refresh_per_second, auto_refresh, vertical_overflow, transient):
            self.renderable = renderable
            self.console = console
            self.refresh_per_second = refresh_per_second
            self.auto_refresh = auto_refresh
            self.vertical_overflow = vertical_overflow
            self.transient = transient

        def start(self):
            events.append("start")

        def refresh(self):
            events.append("refresh")

        def update(self, renderable, refresh):
            self.renderable = renderable
            events.append(("update", refresh))

        def stop(self):
            events.append("stop")

    monkeypatch.setattr("ui.rich_ui.Live", FakeLive)

    ui = RichUI()
    ui._timestamp = lambda: "12:34:56"
    session = _build_session()

    with ui.live_memory_monitor(session):
        assert ui._memory_hud_live is not None
        ui.refresh_memory_monitor(session)
        ui.show_info("tooling started")
        ui.show_tool_result("ok")
        assert len(ui._live_event_buffer) == 2
        assert "[12:34:56]" in ui._live_event_buffer[0]
        assert "[12:34:56]" in ui._live_event_buffer[1]
        with ui.show_status("Working..."):
            events.append("status")
            assert "[12:34:56]" in ui._live_status_message
            assert "Working..." in ui._live_status_message

    assert events[0:2] == ["start", "refresh"]
    assert ("update", True) in events
    assert events.count("stop") == 1
    assert events.count("start") == 1
    assert events[-1] == "stop"
    assert ui._memory_hud_live is None
