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
from utils.runtime_metrics import build_live_status_line, collect_runtime_metrics


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

    def get_input(self, session_name, staged_files, agent_mode="default"):
        return self.input_handler.get_input(
            session_name, staged_files, agent_mode=agent_mode
        )

    def set_variables(self, variables_dict):
        self.input_handler.set_variables(variables_dict)

    def confirm(self, message, default=True):
        return Confirm.ask(message, default=default)

    def prompt_choices(self, message, choices, default=None):
        return Prompt.ask(message, choices=choices, default=default)

    def prompt(self, message, default=None):
        return Prompt.ask(message, default=default)

    def request_tool_approval(
        self,
        *,
        tool_name,
        tool_args,
        display_args,
        count_info,
        can_approve,
        modifications,
        preview_error,
        error_code,
        prompt_text,
        choices,
        default,
    ):
        self.console.print(prompt_text)
        self.console.print(
            "[dim]Tip: press Shift+Tab here to toggle YOLO for this and subsequent approvals in the current loop.[/dim]"
        )
        choice = self.input_handler.prompt_choice(
            "Approval choice",
            choices=choices,
            default=default,
        )
        reason = None
        if choice == "e":
            reason = self.prompt("Provide an explanation to the model")
        return choice, reason

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
        metrics = collect_runtime_metrics(session)

        meters = [
            self.build_meter(
                "CTX", metrics["ctx"]["current"], metrics["ctx"]["maximum"], color="cyan"
            ),
            self.build_meter(
                "MEM",
                metrics["mem"]["current"],
                metrics["mem"]["maximum"],
                color="magenta",
            ),
            self.build_meter(
                "SCRATCH",
                metrics["scratch"]["current"],
                metrics["scratch"]["maximum"],
                color="green",
            ),
            self.build_meter(
                "QUEUE",
                metrics["queue"]["current"],
                metrics["queue"]["maximum"],
                color="yellow",
            ),
        ]

        current_mode = metrics["mode"]["name"]
        mode_description = AGENT_MODE_METADATA.get(current_mode, {}).get("description", "")

        meta = Text()
        meta.append("tokens ", style="dim white")
        meta.append(str(metrics["tokens"]["total"]), style="bold cyan")
        meta.append("  |  queue ", style="dim white")
        meta.append(str(metrics["queue_items"]), style="bold yellow")
        meta.append(" item", style="dim white")
        if metrics["queue_items"] != 1:
            meta.append("s", style="dim white")
        meta.append("  |  mode ", style="dim white")
        meta.append(current_mode, style="bold magenta")

        token_line = Text()
        token_line.append("in ", style="dim white")
        token_line.append(str(metrics["tokens"]["input"]), style="bold cyan")
        token_line.append("  out ", style="dim white")
        token_line.append(str(metrics["tokens"]["output"]), style="bold green")
        token_line.append("  cost ", style="dim white")
        token_line.append(
            f"${metrics['tokens']['total_cost']:.5f}",
            style="bold yellow",
        )

        mode_line = Text()
        mode_line.append("mode info ", style="dim white")
        mode_line.append(mode_description or "No description available.", style="white")

        feature_group = []
        feature_metrics = metrics.get("feature")
        if feature_metrics:
            feature_state = feature_metrics.get("state") or {}
            feature_plan = feature_metrics.get("plan") or {}
            feature_name = feature_plan.get(
                "feature_name", feature_state.get("directory", "Unknown feature")
            )
            feature_status = feature_state.get("status", "unknown")
            feature_group.append(Text(""))

            feature_header = Text()
            feature_header.append("feature ", style="dim white")
            feature_header.append(str(feature_name), style="bold cyan")
            feature_group.append(feature_header)

            feature_meta = Text()
            feature_meta.append("loop ", style="dim white")
            feature_meta.append(str(feature_status), style="bold yellow")
            if feature_plan:
                feature_meta.append("  |  review ", style="dim white")
                feature_meta.append(
                    str(feature_plan.get("review_status", "unknown")),
                    style="bold magenta",
                )
            feature_group.append(feature_meta)

            phases = feature_plan.get("phases", [])
            completed_phases = sum(
                1 for phase in phases if phase.get("status") == "completed"
            )
            phase_count = max(1, int(feature_plan.get("phase_count", len(phases)) or 1))
            feature_group.append(
                self.build_meter(
                    "PHASES",
                    completed_phases,
                    phase_count,
                    color="blue",
                )
            )

            for phase in phases[:4]:
                counts = phase.get("task_counts", {})
                total_tasks = max(1, sum(int(value or 0) for value in counts.values()))
                completed_tasks = int(counts.get("completed", 0) or 0)
                phase_color = {
                    "completed": "green",
                    "in_progress": "yellow",
                    "not_started": "blue",
                }.get(phase.get("status"), "cyan")
                feature_group.append(
                    self.build_meter(
                        f"P{phase.get('number', '?')}",
                        completed_tasks,
                        total_tasks,
                        color=phase_color,
                        width=12,
                    )
                )

            next_phase = feature_plan.get("next_phase")
            if next_phase:
                next_phase_line = Text()
                next_phase_line.append("next ", style="dim white")
                next_phase_line.append(
                    f"P{next_phase.get('number')}: {next_phase.get('title', '')}",
                    style="white",
                )
                feature_group.append(next_phase_line)

        legend = Text.from_markup(
            "[cyan]context[/cyan] [magenta]memory[/magenta] [green]scratchpad[/green] [yellow]collation[/yellow]"
        )

        return Align.center(
            Panel(
                Group(*meters, Text(""), meta, token_line, mode_line, *feature_group, legend),
                title="[bold white]/stats[/bold white]",
                border_style="bright_black",
                box=box.ROUNDED,
                width=76,
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

    def build_live_status(self, session, model_name, iteration, max_iterations):
        metrics = collect_runtime_metrics(session)
        status = Text()
        status.append(f"Generating ({model_name}) it {iteration}/{max_iterations} | ")
        if metrics["yolo"]["enabled"]:
            status.append("✦", style="bold yellow blink")
            status.append(" YOLO | ", style="bold yellow")
        else:
            status.append("YOLO:off | ", style="dim")
        status.append(build_live_status_line(session), style="white")
        return status

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
