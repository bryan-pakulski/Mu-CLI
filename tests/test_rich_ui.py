from types import SimpleNamespace

from rich.console import Console

from core.feature_mode import create_feature_plan, update_feature_plan_metadata
from core.workspace import FolderContext
from ui.rich_ui import RichUI
from utils.runtime_metrics import build_live_status_line


def _build_session(**overrides):
    token_counts = {"input": 120, "output": 80, "total": 200, "total_cost": 0.01}
    session = SimpleNamespace(
        session_manager=SimpleNamespace(
            history=[{"role": "user", "parts": []}] * 12,
            summary_anchor=3,
            token_counts=token_counts,
            get_feature_state=lambda: None,
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

    assert "/stats" in output
    assert "CTX" in output
    assert "MEM" in output
    assert "SCRATCH" in output
    assert "QUEUE" in output
    assert "tokens 200" in output
    assert "in 120" in output
    assert "out 80" in output
    assert "$0.01000" in output
    assert "queue 2 items" in output
    assert "mode" in output
    assert "feature" in output
    assert "Phased Feature Plan Engine" in output


def test_live_status_line_renders_inline_bars():
    session = _build_session()

    status_line = build_live_status_line(session)

    assert "yolo:off" in status_line
    assert "ctx:" in status_line
    assert "mem:" in status_line
    assert "scratch:" in status_line
    assert "queue:" in status_line
    assert "[" in status_line


def test_build_live_status_shows_yolo_indicator_when_enabled():
    ui = RichUI()
    session = _build_session(variables={
        "memory_max_entries": 64,
        "scratchpad_max_entries": 24,
        "agent_mode": "feature",
        "yolo": True,
    })

    status = ui.build_live_status(session, "dummy-model", 2, 5)

    assert status.plain.startswith("Generating (dummy-model) it 2/5 | ✦ YOLO | ")
    assert "yolo:on" in status.plain


def test_memory_monitor_renders_feature_progress(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ctx = FolderContext()
    ctx.add_folder(str(workspace))
    plan = create_feature_plan(
        feature_name="Stats Feature",
        feature_request="Show feature progress in stats",
        phases=[
            {
                "title": "Phase A",
                "objectives": ["Understand scope"],
                "action_points": ["Implement change"],
                "exit_criteria": ["Verify output"],
            }
        ],
        folder_context=ctx,
        feature_id="stats_feature",
    )
    update_feature_plan_metadata(plan.directory, approved=True)

    ui = RichUI()
    session = _build_session(
        session_manager=SimpleNamespace(
            history=[{"role": "user", "parts": []}] * 12,
            summary_anchor=3,
            token_counts={"input": 120, "output": 80, "total": 200, "total_cost": 0.01},
            get_feature_state=lambda: {
                "type": "feature",
                "status": "awaiting_input",
                "directory": plan.directory,
            },
        )
    )
    console = Console(record=True, width=120)

    console.print(ui.build_memory_monitor(session))
    output = console.export_text()

    assert "Stats Feature" in output
    assert "awaiting_input" in output
    assert "PHASES" in output
    assert "P1" in output


def test_request_tool_approval_uses_input_handler_prompt_choice():
    ui = RichUI()
    ui.set_variables({"yolo": False})
    calls = {}

    def fake_prompt_choice(prompt_text, *, choices, default=None):
        calls["prompt_text"] = prompt_text
        calls["choices"] = choices
        calls["default"] = default
        return "y"

    ui.input_handler.prompt_choice = fake_prompt_choice

    choice, reason = ui.request_tool_approval(
        tool_name="write_file",
        tool_args={"filename": "demo.txt"},
        display_args={"filename": "demo.txt"},
        count_info="",
        can_approve=True,
        modifications=[],
        preview_error=None,
        error_code=None,
        prompt_text="[bold yellow]Permission Required[/bold yellow]",
        choices=["y", "n", "e"],
        default="y",
    )

    assert choice == "y"
    assert reason is None
    assert calls == {
        "prompt_text": "Approval choice",
        "choices": ["y", "n", "e"],
        "default": "y",
    }
