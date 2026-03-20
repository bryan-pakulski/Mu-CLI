from contextlib import contextmanager

from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from .input import InputHandler
from .render import render_response
from utils.config import AGENT_MODE_METADATA


class RichUI:
    def __init__(self):
        self.console = Console()
        self.input_handler = InputHandler()

    def render_message(self, role, content, model_name=None):
        if role == "user":
            self.console.print(
                Panel(
                    content,
                    title="User",
                    style="blue",
                    box=box.ROUNDED,
                    title_align="right",
                )
            )
        else:
            if model_name:
                self.console.print(f"\nAssistant ({model_name}):")
            render_response(content)

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

    def show_error(self, message):
        self.console.print(f"[red]{message}[/red]")

    def show_info(self, message):
        self.console.print(f"[blue]{message}[/blue]")

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

        current_mode = str(session.variables.get("agent_mode", "default"))
        mode_description = AGENT_MODE_METADATA.get(current_mode, {}).get("description", "")

        meta = Text()
        meta.append("tokens ", style="dim white")
        meta.append(str(token_total), style="bold cyan")
        meta.append("  |  queue ", style="dim white")
        meta.append(str(collation_items), style="bold yellow")
        meta.append(" item", style="dim white")
        if collation_items != 1:
            meta.append("s", style="dim white")
        meta.append("  |  mode ", style="dim white")
        meta.append(current_mode, style="bold magenta")

        mode_line = Text()
        mode_line.append("mode info ", style="dim white")
        mode_line.append(mode_description or "No description available.", style="white")

        legend = Text.from_markup(
            "[cyan]context[/cyan] [magenta]memory[/magenta] [green]scratchpad[/green] [yellow]collation[/yellow]"
        )

        return Align.right(
            Panel(
                Group(*meters, Text(""), meta, mode_line, legend),
                title="[bold white]Memory HUD[/bold white]",
                border_style="bright_black",
                box=box.ROUNDED,
                width=44,
            )
        )

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
        with self.console.status(message, spinner="aesthetic") as status:
            yield status

    def show_tool_result(self, result_str):
        """Displays the tool result preview with green for success and red for Error:."""
        res_preview = str(result_str).replace("\n", " ")[:60]
        char_count = len(str(result_str))
        color = (
            "red"
            if "Error" in str(res_preview) or "User denied" in str(res_preview)
            else "green"
        )
        self.console.print(
            f"[{color}]  ↳ Result: {res_preview}... ({char_count} chars)[/{color}]"
        )
