"""Rich markup is only interpreted where our own code wrote it.

Regression coverage for the Wash spec's Phase 0 bug fixes:

* B1: model-supplied tool args are escaped in the approval prompt.
* B2: ``RichUI`` renders intentional markup via ``show_info_markup``,
  prints ``show_info`` text literally, and draws renderables instead of
  their ``repr``.
* B3: the web UI sends plain text to browser and mobile clients.
* B4: container output is escaped before it is printed with markup.
* B7: every prompt mode class has a style.
"""

from __future__ import annotations

from types import SimpleNamespace

from rich.console import Console
from rich.table import Table
from rich.text import Text

from mu.agent.approval import build_approval_prompt
from mu.gui.prompts import PromptStore
from mu.gui.web_ui import WebUI
from mu.ui.input import MODE_PROMPT_STYLES, InputHandler
from mu.ui.markup import esc, render_plain, show_markup, show_renderable, to_plain
from mu.ui.rich_ui import RichUI

HOSTILE = "[bold red]x[/bold red] [/] [link=http://evil]y[/link] path[0]"


def _recording_rich_ui(width: int = 120) -> tuple[RichUI, Console]:
    ui = RichUI()
    console = Console(record=True, width=width, color_system=None)
    ui.console = console
    return ui, console


class _RecordingBus:
    def __init__(self):
        self.events: list[dict] = []

    def publish_threadsafe(self, event):
        self.events.append(event)


class _PlainUI:
    """A UI that only knows ``show_info`` (like most test doubles)."""

    def __init__(self):
        self.info: list[str] = []

    def show_info(self, message):
        self.info.append(message)


def _plan(tool_name: str, can_approve: bool = True):
    return SimpleNamespace(tool_name=tool_name, can_approve=can_approve)


# --- B1 ------------------------------------------------------------------


def test_approval_prompt_escapes_tool_args_and_name():
    for can_approve in (True, False):
        prompt_text, _, _ = build_approval_prompt(
            _plan("evil[/cyan]tool", can_approve),
            display_args={"pattern": HOSTILE},
            count_info=" (1/2)",
        )
        plain = Text.from_markup(prompt_text).plain  # must not raise MarkupError
        assert HOSTILE in plain
        assert "evil[/cyan]tool" in plain
        assert "(1/2)" in plain


def test_approval_prompt_prints_hostile_args_literally():
    ui, console = _recording_rich_ui()
    prompt_text, _, _ = build_approval_prompt(
        _plan("write_file"), display_args={"content": HOSTILE}, count_info=""
    )
    ui.console.print(prompt_text)
    output = console.export_text()
    assert "Permission Required" in output
    assert HOSTILE in output


# --- B2 ------------------------------------------------------------------


def test_show_info_prints_brackets_literally():
    ui, console = _recording_rich_ui()
    ui.show_info("[bold]literal[/bold]")
    assert "[bold]literal[/bold]" in console.export_text()


def test_show_info_markup_renders_tags():
    ui, console = _recording_rich_ui()
    show_markup(ui, "[bold green]Stats tracker cleared.[/bold green] done")
    output = console.export_text()
    assert "Stats tracker cleared. done" in output
    assert "[bold green]" not in output


def test_show_info_markup_tolerates_malformed_markup():
    ui, console = _recording_rich_ui()
    ui.show_info_markup("unbalanced [/bold] tag")
    assert "unbalanced [/bold] tag" in console.export_text()


def test_tables_render_instead_of_their_repr():
    table = Table("agent", "status")
    table.add_row("child-1", "running")
    for send in (lambda ui: ui.show_info(table), lambda ui: show_renderable(ui, table)):
        ui, console = _recording_rich_ui()
        send(ui)
        output = console.export_text()
        assert "child-1" in output and "running" in output
        assert "rich.table.Table object" not in output


def test_helpers_degrade_to_plain_text_on_simple_uis():
    ui = _PlainUI()
    show_markup(ui, "[bold black on cyan] PLAN MODE ENABLED [/bold black on cyan] blocked")
    table = Table("agent")
    table.add_row("child-1")
    show_renderable(ui, table)
    assert ui.info[0] == " PLAN MODE ENABLED  blocked"
    assert "child-1" in ui.info[1] and "Table object" not in ui.info[1]


def test_markup_helpers():
    assert to_plain(f"[bold]{esc(HOSTILE)}[/bold]") == HOSTILE
    assert to_plain("broken [/bold]") == "broken [/bold]"
    table = Table("a")
    table.add_row("b")
    assert "b" in render_plain(table)


# --- B3 ------------------------------------------------------------------


def test_web_ui_publishes_plain_text_for_markup_and_renderables():
    bus = _RecordingBus()
    ui = WebUI(bus, PromptStore(), session_name="s1")
    show_markup(ui, "[bold green]ON[/bold green]")
    show_markup(ui, "[red]boom[/red]", error=True)
    table = Table("agent")
    table.add_row("child-1")
    ui.show_info(table)
    ui.show_info("[keep] literal brackets")
    info = [e["text"] for e in bus.events if e.get("kind") == "info"]
    errors = [e["text"] for e in bus.events if e.get("kind") == "error"]
    assert info[0] == "ON"
    assert errors == ["boom"]
    assert "child-1" in info[1] and "Table object" not in info[1]
    assert info[2] == "[keep] literal brackets"


# --- B4 ------------------------------------------------------------------


def test_container_output_is_escaped(monkeypatch):
    import mu.commands.container as container_cmd

    console = Console(record=True, width=200, color_system=None)
    monkeypatch.setattr(container_cmd, "console", console)
    container_cmd._output("stderr", "error: [/red] missing [bold]")
    container_cmd._output("command", "$ echo [dim]x[/dim]")
    container_cmd._progress("build_image", "step [1/3]")
    output = console.export_text()
    assert "error: [/red] missing [bold]" in output
    assert "$ echo [dim]x[/dim]" in output
    assert "step [1/3]" in output


# --- B7 ------------------------------------------------------------------


def test_every_prompt_mode_class_has_a_style():
    handler = InputHandler()
    defined = {name for name, _ in handler.style.style_rules}
    missing = set(MODE_PROMPT_STYLES.values()) - defined
    assert not missing, f"undefined prompt styles: {sorted(missing)}"
