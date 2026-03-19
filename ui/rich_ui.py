from collections import deque
from contextlib import contextmanager
from datetime import datetime

from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from .input import InputHandler
from .render import render_response


class RichUI:
    def __init__(self):
        self.console = Console()
        self.input_handler = InputHandler()
        self._memory_hud_live = None
        self._memory_hud_session = None
        self._live_event_buffer = deque(maxlen=18)
        self._live_status_message = None
        self._last_event_timestamp = None

    def render_message(self, role, content, model_name=None):
        timestamp = self._timestamp()
        if role == "user":
            self.console.print(
                Panel(
                    content,
                    title=f"User • {timestamp}",
                    style="blue",
                    box=box.ROUNDED,
                    title_align="right",
                )
            )
        else:
            if model_name:
                self.console.print(f"\nAssistant ({model_name}) • {timestamp}:")
            else:
                self.console.print(f"\nAssistant • {timestamp}:")
            render_response(content, console_override=self.console)

    def get_input(self, session_name, staged_files):
        return self.input_handler.get_input(session_name, staged_files)

    def set_variables(self, variables_dict):
        self.input_handler.set_variables(variables_dict)

    def confirm(self, message, default=True):
        return Confirm.ask(message, default=default)

    def prompt_choices(self, message, choices, default=None):
        return Prompt.ask(message, choices=choices, default=default)

    def prompt(self, message, default=None):
        return Prompt.ask(message, default=default)

    def _timestamp(self):
        return datetime.now().strftime("%H:%M:%S")

    def _format_timestamped_markup(self, markup_message):
        timestamp = self._timestamp()
        self._last_event_timestamp = timestamp
        return f"[dim][{timestamp}][/dim] {markup_message}"

    def _append_live_event(self, markup_message):
        if self._memory_hud_live is None:
            return False

        self._live_event_buffer.append(self._format_timestamped_markup(markup_message))
        self.refresh_memory_monitor()
        return True

    def show_error(self, message):
        if self._append_live_event(f"[red]{message}[/red]"):
            return
        self.console.print(self._format_timestamped_markup(f"[red]{message}[/red]"))

    def show_info(self, message):
        if self._append_live_event(str(message)):
            return
        self.console.print(self._format_timestamped_markup(f"[blue]{message}[/blue]"))

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

    def build_memory_monitor_panel(self, session):
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
        collation_limit = max(
            1, int(getattr(session.collation_buffer, "max_bytes", 1) or 1)
        )
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
        meta.append(
            str(session.variables.get("agent_mode", "default")), style="bold magenta"
        )

        iteration = int(getattr(session, "_runtime_iteration", 0) or 0)
        max_iterations = int(
            getattr(session, "_runtime_max_iterations", session.variables.get("max_iterations", 0))
            or 0
        )
        total_cost = float(session.session_manager.token_counts.get("total_cost", 0.0) or 0.0)

        extra = Text()
        extra.append("iter ", style="dim white")
        extra.append(f"{iteration}/{max_iterations}", style="bold green")
        extra.append("  |  cost ", style="dim white")
        extra.append(f"${total_cost:.5f}", style="bold yellow")
        if self._last_event_timestamp:
            extra.append("  |  last ", style="dim white")
            extra.append(self._last_event_timestamp, style="bold cyan")

        legend = Text.from_markup(
            "[cyan]context[/cyan] [magenta]memory[/magenta] [green]scratchpad[/green] [yellow]collation[/yellow]"
        )

        return Panel(
            Group(*meters, Text(""), meta, extra, legend),
            title="[bold white]Memory HUD[/bold white]",
            border_style="bright_black",
            box=box.ROUNDED,
            width=44,
        )

    def build_memory_monitor(self, session, align_right=True):
        panel = self.build_memory_monitor_panel(session)
        if not align_right:
            return panel
        return Align.right(panel)

    def build_live_dashboard(self, session):
        lines = []
        if self._live_status_message:
            lines.append(Text.from_markup(f"[bold cyan]status:[/bold cyan] {self._live_status_message}"))
            lines.append(Text(""))

        if self._live_event_buffer:
            lines.extend(Text.from_markup(entry) for entry in self._live_event_buffer)
        else:
            lines.append(Text("Waiting for agent events...", style="dim"))

        runtime_panel = Panel(
            Group(*lines),
            title="[bold white]Runtime Feed[/bold white]",
            border_style="cyan",
            box=box.ROUNDED,
            expand=True,
        )

        grid = Table.grid(expand=True)
        grid.add_column(ratio=1, min_width=60)
        grid.add_column(width=46)
        grid.add_row(runtime_panel, self.build_memory_monitor(session, align_right=False))
        return grid

    def is_memory_monitor_live(self):
        return self._memory_hud_live is not None

    @contextmanager
    def live_memory_monitor(self, session):
        if self._memory_hud_live is not None:
            yield self._memory_hud_live
            return

        self._memory_hud_session = session
        self._live_event_buffer.clear()
        self._live_status_message = None
        self._last_event_timestamp = None
        live = Live(
            self.build_live_dashboard(session),
            console=self.console,
            refresh_per_second=8,
            auto_refresh=False,
            vertical_overflow="visible",
            transient=False,
        )
        self._memory_hud_live = live
        live.start()
        live.refresh()
        try:
            yield live
        finally:
            live.stop()
            self._memory_hud_live = None
            self._memory_hud_session = None
            self._live_status_message = None
            self._last_event_timestamp = None
            self._live_event_buffer.clear()

    def refresh_memory_monitor(self, session=None):
        target_session = session or self._memory_hud_session
        if target_session is None or self._memory_hud_live is None:
            return False

        self._memory_hud_live.update(self.build_live_dashboard(target_session), refresh=True)
        return True

    def show_memory_monitor(self, session):
        self.console.print(self.build_memory_monitor(session))

    def show_diff(self, filename, original_content, new_content):
        """Displays a side-by-side diff with context-aware hunks and Git-style highlighting."""
        import difflib
        import os

        ext = os.path.splitext(filename)[1][1:] or "txt"

        orig_lines = original_content.splitlines()
        new_lines = new_content.splitlines()

        sm = difflib.SequenceMatcher(None, orig_lines, new_lines)
        grouped_opcodes = sm.get_grouped_opcodes(n=3)

        table = Table(
            title=f"PROPOSED CHANGES: {filename}",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold magenta",
            expand=True,
            pad_edge=False,
            collapse_padding=True,
        )

        # We'll use 4 columns: Line L, Content L, Line R, Content R
        table.add_column("L#", justify="right", style="dim", width=5)
        table.add_column("CURRENT STATE", ratio=1)
        table.add_column("R#", justify="right", style="dim", width=5)
        table.add_column("PROPOSED STATE", ratio=1)

        for group in grouped_opcodes:
            # Hunk separator
            table.add_row(
                Text("...", style="cyan"),
                Text(
                    f"@@ hunk starting at L{group[0][1]+1} R{group[0][3]+1} @@",
                    style="cyan dim",
                ),
                Text("...", style="cyan"),
                Text("", style="cyan dim"),
            )

            for tag, i1, i2, j1, j2 in group:
                if tag == "equal":
                    for k in range(i2 - i1):
                        table.add_row(
                            str(i1 + k + 1),
                            Syntax(
                                orig_lines[i1 + k],
                                ext,
                                theme="monokai",
                                background_color="default",
                            ),
                            str(j1 + k + 1),
                            Syntax(
                                new_lines[j1 + k],
                                ext,
                                theme="monokai",
                                background_color="default",
                            ),
                        )
                elif tag == "delete":
                    for k in range(i2 - i1):
                        table.add_row(
                            Text(str(i1 + k + 1), style="red"),
                            Text("- " + orig_lines[i1 + k], style="red on #3a0000"),
                            "",
                            "",
                            style="on #2a0000",
                        )
                elif tag == "insert":
                    for k in range(j2 - j1):
                        table.add_row(
                            "",
                            "",
                            Text(str(j1 + k + 1), style="green"),
                            Text("+ " + new_lines[j1 + k], style="green on #002b00"),
                            style="on #001b00",
                        )
                elif tag == "replace":
                    # For replace, we show deletions then insertions to keep L/R aligned if possible,
                    # but side-by-side replace is tricky in 4 columns if we want to align corresponding lines.
                    # Simplest is to show L on left and R on right in the same row.
                    max_range = max(i2 - i1, j2 - j1)
                    for k in range(max_range):
                        l_idx = i1 + k
                        r_idx = j1 + k

                        l_num = str(l_idx + 1) if l_idx < i2 else ""
                        l_text = orig_lines[l_idx] if l_idx < i2 else ""

                        r_num = str(r_idx + 1) if r_idx < j2 else ""
                        r_text = new_lines[r_idx] if r_idx < j2 else ""

                        table.add_row(
                            Text(l_num, style="red" if l_num else ""),
                            Text(
                                "- " + l_text, style="red on #3a0000" if l_text else ""
                            ),
                            Text(r_num, style="green" if r_num else ""),
                            Text(
                                "+ " + r_text,
                                style="green on #002b00" if r_text else "",
                            ),
                            style="on #1a1a1a",  # Neutral dark background for mixed rows
                        )

        # Summary calculation
        diff_list = list(difflib.unified_diff(orig_lines, new_lines))
        additions = len(
            [l for l in diff_list if l.startswith("+") and not l.startswith("+++")]
        )
        deletions = len(
            [l for l in diff_list if l.startswith("-") and not l.startswith("---")]
        )
        summary = f"[bold green]+{additions} lines[/bold green]  [bold red]-{deletions} lines[/bold red]"

        self.console.print("\n")
        self.console.print(table)
        self.console.print(
            Panel(summary, title="Change Summary", expand=False, border_style="dim")
        )
        self.console.print("\n")

    @contextmanager
    def show_status(self, message):
        if self._memory_hud_live is None:
            with self.console.status(message, spinner="aesthetic") as status:
                yield status
            return

        previous_status = self._live_status_message
        self._live_status_message = self._format_timestamped_markup(str(message))
        self.refresh_memory_monitor()
        try:
            yield str(message)
        finally:
            self._live_status_message = previous_status
            self.refresh_memory_monitor()

    def show_tool_result(self, result_str):
        """Displays the tool result preview with green for success and red for Error:."""
        res_preview = str(result_str).replace("\n", " ")[:60]
        char_count = len(str(result_str))
        color = (
            "red"
            if "Error" in str(res_preview) or "User denied" in str(res_preview)
            else "green"
        )
        message = f"[{color}]  ↳ Result: {res_preview}... ({char_count} chars)[/{color}]"
        if self._append_live_event(message):
            return
        self.console.print(self._format_timestamped_markup(message))
