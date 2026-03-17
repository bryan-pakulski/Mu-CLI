from rich.console import Console
from rich.panel import Panel
from rich.columns import Columns
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich import box
from .render import render_response
from .input import InputHandler
from contextlib import contextmanager

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

    def show_error(self, message):
        self.console.print(f"[red]{message}[/red]")

    def show_info(self, message):
        self.console.print(f"[blue]{message}[/blue]")

    def show_diff(self, filename, original_content, new_content):
        """Displays a side-by-side diff with context-aware hunks and Git-style highlighting."""
        import os
        import difflib

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
            collapse_padding=True
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
                Text(f"@@ hunk starting at L{group[0][1]+1} R{group[0][3]+1} @@", style="cyan dim"), 
                Text("...", style="cyan"), 
                Text("", style="cyan dim")
            )
            
            for tag, i1, i2, j1, j2 in group:
                if tag == 'equal':
                    for k in range(i2 - i1):
                        table.add_row(
                            str(i1 + k + 1),
                            Syntax(orig_lines[i1 + k], ext, theme="monokai", background_color="default"),
                            str(j1 + k + 1),
                            Syntax(new_lines[j1 + k], ext, theme="monokai", background_color="default")
                        )
                elif tag == 'delete':
                    for k in range(i2 - i1):
                        table.add_row(
                            Text(str(i1 + k + 1), style="red"),
                            Text("- " + orig_lines[i1 + k], style="red on #3a0000"),
                            "",
                            "",
                            style="on #2a0000"
                        )
                elif tag == 'insert':
                    for k in range(j2 - j1):
                        table.add_row(
                            "",
                            "",
                            Text(str(j1 + k + 1), style="green"),
                            Text("+ " + new_lines[j1 + k], style="green on #002b00"),
                            style="on #001b00"
                        )
                elif tag == 'replace':
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
                            Text("- " + l_text, style="red on #3a0000" if l_text else ""),
                            Text(r_num, style="green" if r_num else ""),
                            Text("+ " + r_text, style="green on #002b00" if r_text else ""),
                            style="on #1a1a1a" # Neutral dark background for mixed rows
                        )

        # Summary calculation
        diff_list = list(difflib.unified_diff(orig_lines, new_lines))
        additions = len([l for l in diff_list if l.startswith("+") and not l.startswith("+++")])
        deletions = len([l for l in diff_list if l.startswith("-") and not l.startswith("---")])
        summary = f"[bold green]+{additions} lines[/bold green]  [bold red]-{deletions} lines[/bold red]"

        self.console.print("\n")
        self.console.print(table)
        self.console.print(Panel(summary, title="Change Summary", expand=False, border_style="dim"))
        self.console.print("\n")

    @contextmanager
    def show_status(self, message):
        with self.console.status(message, spinner="aesthetic") as status:
            yield status

    def show_tool_result(self, result_str):
        """Displays the tool result preview with green for success and red for Error:."""
        res_preview = str(result_str).replace("\n", " ")[:60]
        char_count = len(str(result_str))
        color = "red" if str(result_str).startswith("Error:") or str(result_str).startswith("User denied") else "green"
        self.console.print(f"[{color}]  ↳ Result: {res_preview}... ({char_count} chars)[/{color}]")
