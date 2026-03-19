from __future__ import annotations

from contextlib import contextmanager
from queue import Queue
from threading import Lock, Thread
from typing import Callable

from rich import box
from rich.align import Align
from rich.console import Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Footer, Header, Input, RichLog, Static

from .render import build_plain_text, build_response_segments


class RenderableBlock(Static):
    def __init__(self, renderable, *, classes: str = ""):
        super().__init__(renderable, classes=classes)


class CopyableCodeBlock(Vertical):
    def __init__(self, content: str, renderable, *, title: str | None = None):
        super().__init__(classes="code-block")
        self.content = content
        self.renderable = renderable
        self.title = title or "Code"

    def compose(self) -> ComposeResult:
        with Horizontal(classes="code-toolbar"):
            yield Static(self.title, classes="code-title")
            yield Button("Copy", variant="primary", classes="copy-button")
        yield Static(self.renderable, classes="code-render")

    @on(Button.Pressed)
    def copy_pressed(self) -> None:
        self.app.copy_to_clipboard(self.content)
        button = self.query_one(Button)
        button.label = "Copied!"
        self.app.update_status(f"Copied {self.title} to the clipboard.")


class MuTextualApp(App):
    CSS = """
    Screen {
        layout: vertical;
    }

    #body {
        height: 1fr;
    }

    #transcript-pane, #sidebar {
        height: 1fr;
        border: solid $primary;
    }

    #transcript-pane {
        width: 3fr;
    }

    #sidebar {
        width: 2fr;
    }

    .sidebar-hidden #transcript-pane {
        width: 1fr;
    }

    .sidebar-hidden #sidebar {
        display: none;
    }

    #status {
        height: auto;
        min-height: 3;
        padding: 0 1;
        border-bottom: solid $primary-background-darken-2;
    }

    #memory {
        height: 10;
        border-top: solid $primary-background-darken-2;
    }

    #input-row {
        height: auto;
        dock: bottom;
    }

    #command-input {
        width: 1fr;
    }

    .transcript-block {
        margin: 0 1 1 1;
    }

    .code-block {
        margin: 0 1 1 1;
        border: round $primary-background-darken-2;
    }

    .code-toolbar {
        height: auto;
        padding: 0 1;
        background: $surface;
    }

    .code-title {
        width: 1fr;
        padding-top: 1;
    }

    .copy-button {
        width: 12;
    }

    .code-render {
        height: auto;
    }
    """

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+l", "clear_input", "Clear input"),
        ("ctrl+b", "toggle_sidebar", "Toggle sidebar"),
    ]

    def __init__(self, ui: "TextualUI"):
        super().__init__()
        self.ui = ui
        self.sidebar_visible = True

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            yield VerticalScroll(id="transcript-pane")
            with Vertical(id="sidebar"):
                yield Static("Ready.", id="status")
                yield RichLog(id="activity", wrap=True, markup=True, highlight=True)
                yield Static("", id="memory")
        with Horizontal(id="input-row"):
            yield Input(placeholder="Type a message or /command and press Enter", id="command-input")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#command-input", Input).focus()
        self.ui.flush_pending()

    def action_clear_input(self) -> None:
        self.query_one("#command-input", Input).value = ""

    def action_toggle_sidebar(self) -> None:
        self.sidebar_visible = not self.sidebar_visible
        if self.sidebar_visible:
            self.remove_class("sidebar-hidden")
            self.update_status("Sidebar shown.")
        else:
            self.add_class("sidebar-hidden")
            self.update_status("Sidebar hidden. Press Ctrl+B to restore it.")

    @on(Input.Submitted, "#command-input")
    def input_submitted(self) -> None:
        self._submit_input()

    def _submit_input(self) -> None:
        widget = self.query_one("#command-input", Input)
        value = widget.value.strip()
        if not value:
            return
        widget.value = ""
        self.ui._handle_submission(value)

    def write_transcript_renderable(self, renderable) -> None:
        container = self.query_one("#transcript-pane", VerticalScroll)
        container.mount(RenderableBlock(renderable, classes="transcript-block"))
        container.scroll_end(animate=False)

    def write_transcript_code(self, content: str, renderable, title: str | None = None) -> None:
        container = self.query_one("#transcript-pane", VerticalScroll)
        container.mount(CopyableCodeBlock(content, renderable, title=title))
        container.scroll_end(animate=False)

    def write_activity(self, renderable) -> None:
        self.query_one("#activity", RichLog).write(renderable)

    def update_status(self, message: str) -> None:
        self.query_one("#status", Static).update(message)

    def update_memory(self, renderable) -> None:
        self.query_one("#memory", Static).update(renderable)


class TextualUI:
    def __init__(self):
        self.app = MuTextualApp(self)
        self.variables_dict = None
        self._submission_callback: Callable[[str], bool | None] | None = None
        self._busy = False
        self._pending_transcript: list[tuple[str, tuple]] = []
        self._pending_activity = []
        self._pending_memory = None
        self._prompt_queue: Queue[str] | None = None
        self._prompt_validator: Callable[[str], bool] | None = None
        self._prompt_lock = Lock()

    def set_submission_callback(self, callback: Callable[[str], bool | None]):
        self._submission_callback = callback

    def run(self):
        self.app.run()

    def exit(self):
        if self.app.is_running:
            self.app.call_from_thread(self.app.exit)

    def _handle_submission(self, value: str):
        with self._prompt_lock:
            if self._prompt_queue is not None:
                if self._prompt_validator and not self._prompt_validator(value):
                    self.show_error("Invalid response. Please try again.")
                    return
                queue = self._prompt_queue
                self._prompt_queue = None
                self._prompt_validator = None
                self.app.update_status("Ready.")
                queue.put(value)
                return

        if self._busy:
            self.show_info("Still processing the previous request. Please wait.")
            return

        self._busy = True
        self.app.update_status("Working...")

        def runner():
            should_continue = True
            try:
                if self._submission_callback:
                    result = self._submission_callback(value)
                    should_continue = True if result is None else bool(result)
            finally:
                self._busy = False
                self.app.call_from_thread(self.app.update_status, "Ready.")
                if not should_continue:
                    self.exit()

        Thread(target=runner, daemon=True).start()

    def _enqueue_activity(self, renderable):
        if self.app.is_running:
            self.app.call_from_thread(self.app.write_activity, renderable)
        else:
            self._pending_activity.append(renderable)

    def _enqueue_transcript_renderable(self, renderable):
        if self.app.is_running:
            self.app.call_from_thread(self.app.write_transcript_renderable, renderable)
        else:
            self._pending_transcript.append(("renderable", (renderable,)))

    def _enqueue_transcript_code(self, content: str, renderable, title: str | None = None):
        if self.app.is_running:
            self.app.call_from_thread(self.app.write_transcript_code, content, renderable, title)
        else:
            self._pending_transcript.append(("code", (content, renderable, title)))

    def flush_pending(self):
        for kind, args in self._pending_transcript:
            if kind == "code":
                self.app.write_transcript_code(*args)
            else:
                self.app.write_transcript_renderable(*args)
        for renderable in self._pending_activity:
            self.app.write_activity(renderable)
        if self._pending_memory is not None:
            self.app.update_memory(self._pending_memory)
        self._pending_transcript.clear()
        self._pending_activity.clear()
        self._pending_memory = None

    def set_variables(self, variables_dict):
        self.variables_dict = variables_dict

    def display_renderable(self, renderable, *, area="activity"):
        if area == "transcript":
            self._enqueue_transcript_renderable(renderable)
        else:
            self._enqueue_activity(renderable)

    def render_message(self, role, content, model_name=None):
        if role == "user":
            self._enqueue_transcript_renderable(
                Panel(content, title="User", border_style="blue")
            )
            return

        title = f"Assistant ({model_name})" if model_name else "Assistant"
        self._enqueue_transcript_renderable(
            Panel(Text(title), title="Assistant", border_style="green")
        )
        for segment in build_response_segments(content):
            if segment.kind == "code":
                self._enqueue_transcript_code(
                    segment.content or "",
                    segment.renderable,
                    segment.title,
                )
            else:
                self._enqueue_transcript_renderable(segment.renderable)

    def show_error(self, message):
        self.display_renderable(
            Panel(build_plain_text(f"[red]{message}[/red]"), title="Error", border_style="red")
        )

    def show_info(self, message):
        self.display_renderable(build_plain_text(message))

    def print(self, obj):
        self.display_renderable(obj)

    def build_meter(
        self,
        label,
        current,
        maximum,
        *,
        color="cyan",
        width=16,
        warning_threshold=0.75,
        danger_threshold=0.9,
    ):
        maximum = max(1, int(maximum or 1))
        current = max(0, int(current or 0))
        ratio = min(current / maximum, 1.0)
        filled = min(width, int(round(width * ratio)))

        bar = Text()
        active_color = color
        if ratio >= danger_threshold:
            active_color = "red"
        elif ratio >= warning_threshold:
            active_color = "yellow"

        bar.append("█" * filled, style=f"bold {active_color}")
        bar.append("░" * (width - filled), style="grey30")

        line = Text()
        line.append(f"{label:<8}", style="bold white")
        line.append(" ")
        line.append(bar)
        line.append(f" {current}/{maximum}", style="dim white")
        return line

    def build_memory_monitor(self, session):
        hist_len = len(session.session_manager.history)
        anchor = session.session_manager.summary_anchor
        active_turns = max(0, hist_len - anchor)
        context_limit = max(1, int(getattr(session, "active_context_window", 1) or 1))

        memory_limit = max(
            1,
            int(
                session.variables.get(
                    "memory_max_entries", getattr(session.task_memory, "max_entries", 1)
                )
            ),
        )
        scratch_limit = max(
            1,
            int(
                session.variables.get(
                    "scratchpad_max_entries",
                    getattr(session.turn_scratchpad, "max_entries", 1),
                )
            ),
        )
        collation_limit = max(1, int(getattr(session.collation_buffer, "max_bytes", 1) or 1))
        collation_bytes = sum(
            len(result or "") for _, _, result in session.collation_buffer.entries
        )
        collation_items = len(session.collation_buffer.entries)
        token_total = int(session.session_manager.token_counts.get("total", 0) or 0)

        meters = [
            self.build_meter("CTX", active_turns, context_limit, color="cyan"),
            self.build_meter(
                "MEM", len(session.task_memory.entries), memory_limit, color="magenta"
            ),
            self.build_meter(
                "SCRATCH",
                len(session.turn_scratchpad.entries),
                scratch_limit,
                color="green",
            ),
            self.build_meter("QUEUE", collation_bytes, collation_limit, color="yellow"),
        ]

        meta = Text()
        meta.append("tokens ", style="dim white")
        meta.append(str(token_total), style="bold cyan")
        meta.append("  |  queue ", style="dim white")
        meta.append(str(collation_items), style="bold yellow")
        meta.append(" item", style="dim white")
        if collation_items != 1:
            meta.append("s", style="dim white")
        meta.append("  |  mode ", style="dim white")
        meta.append(str(session.variables.get("agent_mode", "default")), style="bold magenta")

        legend = Text.from_markup(
            "[cyan]context[/cyan] [magenta]memory[/magenta] [green]scratchpad[/green] [yellow]collation[/yellow]"
        )

        return Align.left(
            Panel(
                Group(*meters, Text(""), meta, legend),
                title="[bold white]Memory HUD[/bold white]",
                border_style="bright_black",
                box=box.ROUNDED,
            )
        )

    def show_memory_monitor(self, session):
        renderable = self.build_memory_monitor(session)
        if self.app.is_running:
            self.app.call_from_thread(self.app.update_memory, renderable)
        else:
            self._pending_memory = renderable

    def show_diff(self, filename, original_content, new_content):
        import difflib

        orig_lines = original_content.splitlines()
        new_lines = new_content.splitlines()

        table = Table(title=f"PROPOSED CHANGES: {filename}", box=box.ROUNDED, expand=True)
        table.add_column("L#", justify="right", style="dim", width=5)
        table.add_column("CURRENT STATE", ratio=1)
        table.add_column("R#", justify="right", style="dim", width=5)
        table.add_column("PROPOSED STATE", ratio=1)

        sm = difflib.SequenceMatcher(None, orig_lines, new_lines)
        for group in sm.get_grouped_opcodes(n=3):
            table.add_row(
                Text("...", style="cyan"),
                Text(f"@@ hunk starting at L{group[0][1]+1} R{group[0][3]+1} @@", style="cyan dim"),
                Text("...", style="cyan"),
                Text("", style="cyan dim"),
            )
            for tag, i1, i2, j1, j2 in group:
                if tag == "equal":
                    for k in range(i2 - i1):
                        table.add_row(str(i1 + k + 1), orig_lines[i1 + k], str(j1 + k + 1), new_lines[j1 + k])
                elif tag == "delete":
                    for k in range(i2 - i1):
                        table.add_row(Text(str(i1 + k + 1), style="red"), Text("- " + orig_lines[i1 + k], style="red"), "", "")
                elif tag == "insert":
                    for k in range(j2 - j1):
                        table.add_row("", "", Text(str(j1 + k + 1), style="green"), Text("+ " + new_lines[j1 + k], style="green"))
                else:
                    max_range = max(i2 - i1, j2 - j1)
                    for k in range(max_range):
                        l_idx = i1 + k
                        r_idx = j1 + k
                        l_num = str(l_idx + 1) if l_idx < i2 else ""
                        r_num = str(r_idx + 1) if r_idx < j2 else ""
                        l_text = orig_lines[l_idx] if l_idx < i2 else ""
                        r_text = new_lines[r_idx] if r_idx < j2 else ""
                        table.add_row(Text(l_num, style="red" if l_num else ""), Text("- " + l_text if l_text else "", style="red"), Text(r_num, style="green" if r_num else ""), Text("+ " + r_text if r_text else "", style="green"))

        self.display_renderable(table)

    @contextmanager
    def show_status(self, message):
        if self.app.is_running:
            self.app.call_from_thread(self.app.update_status, message)
        try:
            yield
        finally:
            if self.app.is_running:
                self.app.call_from_thread(self.app.update_status, "Ready.")

    def show_tool_result(self, result_str):
        res_preview = str(result_str).replace("\n", " ")[:120]
        char_count = len(str(result_str))
        color = "red" if "Error" in res_preview or "User denied" in res_preview else "green"
        self.display_renderable(build_plain_text(f"[{color}]↳ Result: {res_preview}... ({char_count} chars)[/{color}]"))

    def _request_prompt(self, message, validator=None, default=None):
        queue: Queue[str] = Queue()
        with self._prompt_lock:
            self._prompt_queue = queue
            self._prompt_validator = validator
        note = f"{message}"
        if default is not None:
            note += f" [default: {default}]"
        self.display_renderable(Panel(Text(note), title="Input Required", border_style="yellow"))
        if self.app.is_running:
            self.app.call_from_thread(self.app.update_status, "Waiting for input...")
        value = queue.get()
        if not value.strip() and default is not None:
            return default
        return value

    def confirm(self, message, default=True):
        default_choice = "y" if default else "n"
        value = self._request_prompt(
            f"{message} [y/n]",
            validator=lambda v: v.lower() in {"y", "n", "yes", "no", ""},
            default=default_choice,
        )
        return value.lower() in {"y", "yes"}

    def prompt_choices(self, message, choices, default=None):
        valid = set(choices)
        return self._request_prompt(
            f"{message} Choices: {', '.join(choices)}",
            validator=lambda v: (not v.strip() and default is not None) or v in valid,
            default=default,
        )

    def prompt(self, message, default=None):
        return self._request_prompt(message, default=default)
