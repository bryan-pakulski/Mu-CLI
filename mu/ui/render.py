# Rich console output and markdown rendering
import re
import mimetypes
from rich.console import Console
from rich.errors import MarkupError
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.panel import Panel

from utils.helpers import safe_markup

console = Console()

# --- LaTeX → Unicode (TUI) -------------------------------------------------
# pylatexenc converts LaTeX math fragments to readable Unicode/plain text so
# Rich's Markdown renderer (which has no math support) shows something sane
# instead of raw `$\frac{...}$`.  Code spans are protected — `$` inside code
# is never touched.
_L2T = None


def _get_l2t():
    """Lazily build the LatexNodes2Text converter; False if pylatexenc missing."""
    global _L2T
    if _L2T is None:
        try:
            from pylatexenc.latex2text import LatexNodes2Text
            _L2T = LatexNodes2Text()
        except ImportError:
            _L2T = False
    return _L2T


# Order matters: $$...$$ before $...$; \(...\) and \[...\] after.
_MATH_RE = re.compile(
    r"(\$\$.+?\$\$"      # $$...$$  display
    r"|\$[^\$\n]+?\$"    # $...$    inline (no newline, no nested $)
    r"|\\\(.+?\\\)"      # \(...\)  inline
    r"|\\\[.+?\\\])",    # \[...\]  display
    re.DOTALL,
)


def _latex_repl(match):
    l2t = _get_l2t()
    if not l2t:
        return match.group(0)
    expr = match.group(0)
    is_display = expr.startswith("$$") or expr.startswith("\\[")
    try:
        out = l2t.latex_to_text(expr)
    except Exception:
        return expr
    out = out.strip()
    if is_display:
        return "\n\n" + out + "\n\n"
    return out


def latex_to_unicode(text):
    """Convert LaTeX math in prose to Unicode; leave inline ``code`` spans intact."""
    # Split out inline code spans (`...`) so $ inside code is never converted.
    pieces = re.split(r"(`[^`\n]+`)", text)
    rendered = []
    for piece in pieces:
        if piece.startswith("`") and piece.endswith("`") and len(piece) > 1:
            rendered.append(piece)
        else:
            rendered.append(_MATH_RE.sub(_latex_repl, piece))
    return "".join(rendered)


def render_response(text):
    """
    Renders text using Rich.
    """
    if not text.strip():
        return
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
            console.print(f"[bold cyan]### {safe_markup(title)}[/bold cyan]")
        console.print(
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
        console.print(syntax)
        console.print(
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
            body = part.strip()
            body = latex_to_unicode(body)
            try:
                console.print(Markdown(body))
            except MarkupError:
                console.print(body, markup=False)
            console.print("")
