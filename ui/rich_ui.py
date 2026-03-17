from rich.console import Console
from rich.panel import Panel
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

    def show_error(self, message):
        self.console.print(f"[red]{message}[/red]")

    def show_info(self, message):
        self.console.print(f"[blue]{message}[/blue]")

    @contextmanager
    def show_status(self, message):
        with self.console.status(message, spinner="aesthetic") as status:
            yield status
