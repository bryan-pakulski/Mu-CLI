"""Terminal component gallery: ``python -m mu.ui.theme.gallery``.

Prints every Wash terminal component in the active colour mode, using the
real render code (RichUI, the splash, the jobs primitives, the pickers'
prompt_toolkit style). Review it under each mode::

    NO_COLOR=1 python -m mu.ui.theme.gallery
    TERM=xterm python -m mu.ui.theme.gallery              # 16 colours
    TERM=xterm-256color python -m mu.ui.theme.gallery
    COLORTERM=truecolor MUCLI_TERM_THEME=paper python -m mu.ui.theme.gallery
    COLORTERM=truecolor MUCLI_TERM_THEME=dusk python -m mu.ui.theme.gallery

``--svg PATH`` / ``--html PATH`` / ``--text PATH`` export a recording.
"""

from __future__ import annotations

import argparse
import io
import sys
from typing import Iterable, List, Optional, Tuple

from rich.console import Console, Group
from rich.syntax import Syntax
from rich.text import Text

from mu.design import load_tokens, load_vocabulary

from . import components, glyphs, mode, theme
from .pt_style import style_dict
from .pygments import syntax_theme

_RICH_SYSTEM = {"none": None, "16": "standard", "256": "256", "truecolor": "truecolor"}


# --------------------------------------------------------------- pt → rich

def _pt_to_rich(spec: str) -> str:
    """Convert a prompt_toolkit style string into a Rich style string."""
    out = []
    for part in str(spec or "").split():
        if part.startswith("fg:"):
            out.append(_pt_color(part[3:]))
        elif part.startswith("bg:"):
            out.append(f"on {_pt_color(part[3:])}")
        elif part in ("bold", "italic", "underline", "reverse"):
            out.append(part)
    return " ".join(p for p in out if p and p != "on ") or "none"


def _pt_color(value: str) -> str:
    if value.startswith("ansibright"):
        return "bright_" + value[len("ansibright"):]
    if value.startswith("ansi"):
        return value[4:]
    return value


def pt_fragments_text(fragments: Iterable[Tuple[str, str]]) -> Text:
    """Render prompt_toolkit ``(class:..., text)`` fragments with the pt style."""
    rules = style_dict()
    text = Text()
    for style, chunk in fragments:
        parts = []
        for token in str(style).split():
            if token.startswith("class:"):
                for name in token[6:].split(","):
                    parts.append(rules.get(name, ""))
            else:
                parts.append(token)
        text.append(chunk, style=_pt_to_rich(" ".join(parts)))
    return text


def _html_fragments(markup: str):
    from prompt_toolkit.formatted_text import HTML, to_formatted_text

    return [(style, chunk) for style, chunk, *_ in to_formatted_text(HTML(markup))]


# --------------------------------------------------------------- sections

def _heading(title: str) -> Text:
    text = Text()
    text.append(f"\n{title}", style="wash.title")
    return text


def palette_section() -> Group:
    rows: List[Text] = []
    neutrals = Text()
    for name, style in (("ink", "wash.ink"), ("ink-2", "wash.ink2"), ("ink-3", "wash.muted"), ("plum", "wash.plum")):
        neutrals.append("██ ", style=style)
        neutrals.append(f"{name:<8}", style="wash.ink2")
    rows.append(neutrals)
    semantics = Text()
    for name in ("accent", "running", "ok", "attention", "caution", "danger", "plan", "info"):
        semantics.append("██ ", style=f"wash.{name}")
        semantics.append(f"{name:<10}", style="wash.ink2")
    rows.append(semantics)
    modes = Text()
    for name in load_tokens()["mode"]:
        modes.append(f"{name} ", style=f"wash.mode.{name}")
    rows.append(Text.assemble(("modes   ", "wash.muted"), modes))
    series = Text()
    for index, name in enumerate(load_tokens()["series"]):
        series.append(f"agent-{index + 1} ", style=f"wash.series.{index}")
    rows.append(Text.assemble(("agents  ", "wash.muted"), series))
    return Group(*rows)


def splash_section():
    from mu.cli.display import splash_renderable

    return splash_renderable(
        session_name="refactor-auth",
        session_type="▱ workspace",
        agent_mode="feature",
        model_name="claude-sonnet",
        flags="thinking on · agentic on · yolo off · system set",
        context="ctx 18,240 / 200,000",
        workspace="~/dev/acme/api",
        plan=True,
    )


def prompt_section() -> Group:
    from mu.ui.input import InputHandler

    handler = InputHandler()
    handler.set_variables({"plan_mode": True, "yolo": True})
    lines = []
    for mode_name in load_tokens()["mode"]:
        if mode_name == "plan":
            continue
        handler.set_variables({"plan_mode": mode_name == "research", "yolo": mode_name == "debug"})
        markup = handler.build_prompt_markup("refactor-auth", [], agent_mode=mode_name)
        lines.append(pt_fragments_text(_html_fragments(markup)))
    handler.set_variables({"plan_mode": True, "yolo": False})
    toolbar = pt_fragments_text(handler.style_toolbar(handler.build_input_toolbar_text()))
    return Group(*lines, Text(""), Text("bottom toolbar", style="wash.muted"), toolbar)


def picker_section() -> Group:
    rows = [
        ("class:title bold", "  Choose a session\n"),
        ("class:hint", "  ↑/↓ move · Enter open · d delete\n"),
        ("class:current bold", "  ❯ refactor-auth\n"),
        ("", "    docs-sweep\n"),
        ("class:selected", "    ✓ release-notes\n"),
        ("class:new", "    [New Session]\n"),
        ("class:warn bold", "  Delete 'docs-sweep'? y/N\n"),
    ]
    return Group(pt_fragments_text(rows))


def notices_section() -> Group:
    return Group(
        components.notice_text("info", "Verbose rendering: OFF"),
        components.notice_text("ok", "PLAN MODE DISABLED · Write-side tools are unblocked."),
        components.notice_text("attention", "Approval needed for write_file"),
        components.notice_text("caution", "Context is 172,000 / 200,000 tokens, past the 85% trim threshold."),
        components.notice_text("danger", "Couldn't reach Ollama at localhost:11434. Is it running?"),
        components.notice_text("plan", "PLAN MODE ENABLED · Write-side tools are blocked. /plan off to disable."),
    )


def _rich_ui(console: Console):
    from mu.ui.rich_ui import RichUI

    ui = RichUI()
    ui.console = console
    return ui


def approval_section(console: Console):
    ui = _rich_ui(console)
    return Group(
        ui.build_approval_panel(
            tool_name="write_file",
            display_args={"filename": "src/auth/[session].py", "content": "[bold]not markup[/bold]"},
            count_info="(1/2)",
            choices=["y", "n", "e"],
        ),
        ui.build_approval_panel(
            tool_name="apply_diff",
            display_args={"filename": "src/app.py"},
            can_approve=False,
            preview_error="hunk 2 did not apply",
            choices=["n", "e"],
            approval_policy="always_human",
        ),
    )


def tool_section() -> Group:
    return Group(
        components.tool_marker("read_file", "src/app/main.py"),
        components.tool_marker("bash", "pytest -q"),
        components.tool_marker("read_file", "src/app/main.py", state="done", elapsed=0.4),
        components.tool_marker("search_for_string", "def login", state="done", elapsed=1.2),
        components.tool_marker("write_file", "src/app/main.py", state="failed", elapsed=0.1),
        Text("I should check the session store before touching the middleware.", style="wash.thinking"),
    )


def diff_section(console: Console) -> Group:
    ui = _rich_ui(console)
    before = "def login(user):\n    token = issue(user)\n    return token\n"
    after = "def login(user, *, ttl=3600):\n    token = issue(user, ttl=ttl)\n    audit(user)\n    return token\n"
    return Group(
        ui.build_diff("src/auth.py", before, after, side_by_side=False),
        ui.build_diff("src/auth.py", before, after, side_by_side=True),
    )


def code_section():
    from rich.panel import Panel

    code = 'def greet(name: str) -> str:\n    # say hello\n    return f"hello {name}" * 2'
    return Panel(
        Syntax(code, "python", theme=syntax_theme(), background_color="default"),
        title=Text("python", style="wash.muted"),
        title_align="left",
        border_style="wash.border",
        box=components.PANEL_BOX,
        padding=(0, 1),
    )


def meters_section() -> Group:
    layers = [("L0", 3200), ("L1A", 2100), ("L1B", 900), ("L2", 1800), ("L3", 400),
              ("L4", 2500), ("L4B", 1200), ("L5", 9100)]
    return Group(
        components.meter("CTX", 30, 100),
        components.meter("MEM", 70, 100),
        components.meter("SCRATCH", 90, 100),
        Text(""),
        components.layer_bar(layers, limit=40000, width=48),
    )


def subagent_section():
    from mu.ui.progress import SubagentProgressTracker

    tracker = SubagentProgressTracker()
    a = tracker.open(depth=1, task="Review agent loop")
    tracker.update_tool(a, "read_file")
    b = tracker.open(depth=1, task="Review commands & UI")
    tracker.close(b, tool_count=4, summary="All 11 commands escape values")
    c = tracker.open(depth=1, task="Review tests & core")
    tracker.close(c, tool_count=2, summary="pytest failed", error="boom")
    return tracker.render_panel()


def status_section() -> Group:
    vocab = load_vocabulary()
    chips = Text()
    for status in vocab["status"]:
        chips.append_text(components.status_chip(status))
        chips.append("   ")
    severities = Text()
    for name, spec in vocab["severity"].items():
        severities.append(f"{name} ", style=f"wash.severity.{name}")
    spinner = Text(" ".join(glyphs.SPINNER_FRAMES), style="wash.running")
    return Group(
        chips,
        Text.assemble(("severity  ", "wash.muted"), severities),
        Text.assemble(("spinner   ", "wash.muted"), spinner, (f"  every {int(glyphs.SPINNER_INTERVAL_S * 1000)}ms", "wash.muted")),
        components.key_hints([("a", "approve"), ("A", "always"), ("d", "deny"), ("e", "edit")]),
    )


def table_section():
    table = components.table("Tool", "Calls", "Fail", "Last used", title="Tools used")
    table.add_row("read_file", "12", "-", "2m ago")
    table.add_row("bash", "4", "1", "30s ago")
    return table


def container_section() -> Group:
    from mu.container.progress_line import progress_text

    return Group(
        progress_text("build_image", "step [2/5] RUN pip install -r requirements.txt"),
        progress_text("start", "container acme-dev is up"),
    )


def jobs_section() -> Group:
    from mu.ui.jobs import primitives, theme as jobs_theme

    steps = [{"id": "plan", "label": "Plan", "status": "done", "elapsed": 360},
             {"id": "approve", "label": "Approve", "status": "waiting"},
             {"id": "implement", "label": "Implement", "status": "active", "elapsed": 1080},
             {"id": "checks", "label": "Checks", "status": "pending"},
             {"id": "push", "label": "Push", "status": "failed"}]
    depth = mode()
    lines = [primitives.stepper(steps, 100)]
    for state in ("provisioning", "plan_review", "implementing", "pushed", "failed", "cancelled"):
        row = jobs_theme.state_style(state)
        lines.append([(row["token"], f"{row['icon']} {row['label']}")])
    return Group(*[Text.from_ansi(primitives.to_ansi([line], depth)) for line in lines])


def build(console: Console) -> List:
    header = Text()
    header.append("Wash terminal gallery", style="wash.title")
    header.append(f"  mode {mode()} · background {theme()}", style="wash.muted")
    return [
        header,
        _heading("Palette"), palette_section(),
        _heading("Splash"), splash_section(),
        _heading("Prompt and toolbar"), prompt_section(),
        _heading("Picker"), picker_section(),
        _heading("Notices"), notices_section(),
        _heading("Approval"), approval_section(console),
        _heading("Tool markers and thinking"), tool_section(),
        _heading("Diff"), diff_section(console),
        _heading("Code"), code_section(),
        _heading("Meters"), meters_section(),
        _heading("Sub-agents"), subagent_section(),
        _heading("Status, severity, spinner, key hints"), status_section(),
        _heading("Table"), table_section(),
        _heading("Container progress"), container_section(),
        _heading("Jobs TUI"), jobs_section(),
    ]


def terminal_theme():
    """A Wash-remapped terminal palette (G§9.21) for SVG/HTML exports.

    Background and foreground follow the detected Paper/Dusk theme; the 16
    ANSI slots are the pigment ink steps, so 16-colour output previews the
    way it looks on a terminal themed with Wash.
    """
    from rich.terminal_theme import TerminalTheme

    from mu.design import hex_to_rgb, pigment, theme_colors

    name = theme()
    colors = theme_colors(name)
    background = colors["canvas"] if name == "paper" else colors.get("terminal", colors["canvas"])

    def ink(pig):
        return hex_to_rgb(pigment(name, pig, "ink"))

    normal = [hex_to_rgb(colors["ink"] if name == "paper" else colors["sunken"]), ink("rose"),
              ink("meadow"), ink("apricot"), ink("sky"), ink("lilac"), ink("lagoon"),
              hex_to_rgb(colors["ink-2"])]
    bright = [hex_to_rgb(colors["ink-3"]), ink("rose"), ink("meadow"), ink("sun"), ink("sky"),
              ink("lilac"), ink("lagoon"), hex_to_rgb(colors["ink"])]
    return TerminalTheme(hex_to_rgb(background), hex_to_rgb(colors["ink"]), normal, bright)


def render(*, width: int = 110, record: bool = False, file=None) -> Console:
    console = Console(
        record=record,
        width=width,
        color_system=_RICH_SYSTEM[mode()],
        force_terminal=mode() != "none",
        no_color=mode() == "none",
        file=file,
    )
    for part in build(console):
        console.print(part)
    return console


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--width", type=int, default=110)
    parser.add_argument("--svg")
    parser.add_argument("--html")
    parser.add_argument("--text")
    args = parser.parse_args(argv)
    exporting = bool(args.svg or args.html or args.text)
    console = render(width=args.width, record=exporting, file=io.StringIO() if exporting else None)
    if args.svg:
        svg = console.export_svg(
            title=f"Wash · {mode()} · {theme()}", theme=terminal_theme(), clear=False
        )
        with open(args.svg, "w", encoding="utf-8") as handle:
            handle.write(svg)
    if args.html:
        with open(args.html, "w", encoding="utf-8") as handle:
            handle.write(console.export_html(theme=terminal_theme(), clear=False))
    if args.text:
        with open(args.text, "w", encoding="utf-8") as handle:
            handle.write(console.export_text(clear=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
