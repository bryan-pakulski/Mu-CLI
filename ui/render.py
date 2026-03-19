# Rich console output and markdown rendering
import re
import mimetypes
from rich.console import Console
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.panel import Panel

console = Console()


def render_response(text, console_override=None):
    """
    Renders text using Rich.
    """
    if not text.strip():
        return
    target_console = console_override or console
    pattern = r"(```(?:[\w\+\-\.]+)?\s*\n.*?```|<file_change\s+path='[^']+'>.*?</file_change>|<file_content\s+path='[^']+'>.*?</file_content>|<new_file\s+path='[^']+'>.*?</new_file>)"
    parts = re.split(pattern, text, flags=re.DOTALL)

    def print_code_panel(content, lang, title=None):
        if lang == "diff":
            content = re.sub(
                r"^(@@ \-(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@)(.*)$",
                r"\1 \4 # Line \2 -> \3",
                content,
                flags=re.MULTILINE,
            )

        if title:
            target_console.print(f"[bold cyan]### {title}[/bold cyan]")
        target_console.print(
            f" [bold cyan]┌── {lang} ─────────────────────────────────[/bold cyan]"
        )
        syntax = Syntax(
            content,
            lang,
            theme="monokai",
            background_color=None,
            word_wrap=False,
            padding=0,
        )
        target_console.print(syntax)
        target_console.print(
            " [bold cyan]└────────────────────────────────────────────[/bold cyan]"
        )

    for part in parts:
        if not part.strip():
            continue

        if part.startswith("``````"):
            lines = part.split("\n")
            lang = lines[0].strip("`").strip() or "text"
            content = "\n".join(lines[1:-1])
            print_code_panel(content, lang)

        elif part.startswith("<file_"):
            tag_match = re.match(
                r"<(file_change|file_content|new_file)\s+path='([^']+)'>([\s\S]*?)</\1>",
                part,
            )
            if tag_match:
                tag, path, content = tag_match.groups()
                lang = (
                    "diff"
                    if tag == "file_change"
                    else (mimetypes.guess_type(path)[0] or "text").split("/")[-1]
                )
                title = f"{tag.replace('_', ' ').upper()}: {path}"
                print_code_panel(content.strip(), lang, title)
        else:
            target_console.print(Markdown(part.strip()))
            target_console.print("")
