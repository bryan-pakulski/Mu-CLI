import mimetypes
import re
from dataclasses import dataclass

from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text


CODE_BLOCK_PATTERN = re.compile(
    r"(```(?:[\w\+\-\.]+)?\s*\n.*?```|<file_change\s+path='[^']+'>.*?</file_change>|<file_content\s+path='[^']+'>.*?</file_content>|<new_file\s+path='[^']+'>.*?</new_file>)",
    flags=re.DOTALL,
)


@dataclass(frozen=True)
class ResponseSegment:
    kind: str
    renderable: object
    content: str | None = None
    lang: str | None = None
    title: str | None = None


def _build_code_renderable(content, lang, title=None):
    if lang == "diff":
        content = re.sub(
            r"^(@@ \-(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@)(.*)$",
            r"\1 \4 # Line \2 -> \3",
            content,
            flags=re.MULTILINE,
        )

    syntax = Syntax(
        content,
        lang,
        theme="monokai",
        background_color=None,
        word_wrap=False,
        padding=0,
    )
    panel_title = title or f"{lang}"
    return Panel(syntax, title=panel_title, border_style="cyan")


def build_response_segments(text):
    """Convert a response into structured Rich/Textual-ready segments."""
    if not text or not text.strip():
        return []

    segments = []
    parts = re.split(CODE_BLOCK_PATTERN, text)

    for part in parts:
        if not part or not part.strip():
            continue

        if part.startswith("```"):
            lines = part.split("\n")
            lang = lines[0].strip("`").strip() or "text"
            content = "\n".join(lines[1:-1])
            title = lang
            segments.append(
                ResponseSegment(
                    kind="code",
                    renderable=_build_code_renderable(content, lang, title),
                    content=content,
                    lang=lang,
                    title=title,
                )
            )
            continue

        if part.startswith("<file_") or part.startswith("<new_file"):
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
                content = content.strip()
                segments.append(
                    ResponseSegment(
                        kind="code",
                        renderable=_build_code_renderable(content, lang, title),
                        content=content,
                        lang=lang,
                        title=title,
                    )
                )
                continue

        renderable = Markdown(part.strip())
        segments.append(ResponseSegment(kind="markdown", renderable=renderable, content=part.strip()))

    return segments


def build_response_renderables(text):
    return [segment.renderable for segment in build_response_segments(text)]


def build_plain_text(text):
    if not text:
        return Text("")
    return Text.from_markup(text)


def render_response(text, console=None):
    from rich.console import Console

    console = console or Console()
    for renderable in build_response_renderables(text):
        console.print(renderable)
