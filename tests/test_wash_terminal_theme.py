"""Wash terminal theme contracts (spec §6, §8.1).

* every ``wash.*`` style resolves in none / 16 / 256 / truecolor, Paper and Dusk
* dynamic values are escaped (approval card, splash, show_info, show_renderable)
* ``NO_COLOR`` output keeps its glyphs and labels
* background detection precedence (override → COLORFGBG → OSC 11 → dusk)
* one process console; no inline colour markup outside ``mu/ui/theme``
* one prompt_toolkit style, no blink, every mode class defined
"""

from __future__ import annotations

import ast
import io
import re
from pathlib import Path

import pytest
from rich.console import Console
from rich.style import Style
from rich.table import Table
from rich.text import Text

import mu.ui.theme as wash
from mu.design import load_tokens, load_vocabulary, nearest_xterm256, semantic
from mu.ui.markup import notice_plain, show_notice
from mu.ui.theme import components, detect, glyphs
from mu.ui.theme.palette import role_ansi16, role_color
from mu.ui.theme.pt_style import style_dict
from mu.ui.theme.rich_theme import STYLE_NAMES, STYLE_SPECS, resolve

ROOT = Path(__file__).resolve().parents[1]
MODES = ("none", "16", "256", "truecolor")
THEMES = ("paper", "dusk")
WASH_NAMES = [name for name in STYLE_NAMES if name.startswith("wash.")]

HOSTILE = "[bold red]x[/] [/cyan] [link=file:///etc] ]["


def _record(width=120, color_system=None):
    return Console(record=True, width=width, color_system=color_system, file=io.StringIO())


# --------------------------------------------------------------- styles


def test_required_style_names_exist():
    required = {
        "wash.ink", "wash.ink2", "wash.muted", "wash.plum", "wash.accent", "wash.running",
        "wash.ok", "wash.attention", "wash.caution", "wash.danger", "wash.plan", "wash.key",
        "wash.hint", "wash.border", "wash.code", "wash.path", "wash.diff.add", "wash.diff.del",
        "wash.diff.hunk", "wash.thinking",
    }
    required |= {f"wash.mode.{m}" for m in load_tokens()["mode"]}
    required |= {f"wash.layer.{layer}" for layer in load_tokens()["layer"]}
    assert required <= set(STYLE_NAMES)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("theme", THEMES)
def test_every_wash_style_resolves_in_every_mode(mode, theme):
    for name in WASH_NAMES:
        spec = resolve(name, mode, theme)
        style = Style.parse(spec)  # must parse
        color = style.color.name if style.color else ""
        if mode == "none":
            assert style.color is None and style.bgcolor is None, name
        elif mode == "16":
            assert "#" not in spec and "color(" not in spec, (name, spec)
            assert style.bgcolor is None, (name, spec)  # tints only in truecolor
        elif mode == "256":
            assert "#" not in spec, (name, spec)
            assert style.bgcolor is None, (name, spec)
        if mode in ("256", "truecolor") and STYLE_SPECS[name].role in ("accent", "ok", "danger"):
            assert color, name


def test_semantic_styles_use_the_theme_ink_step():
    for theme in THEMES:
        for role in ("accent", "running", "ok", "attention", "caution", "danger", "plan", "info"):
            assert Style.parse(resolve(f"wash.{role}", "truecolor", theme)).color.name == semantic(theme, role).lower()
            index = nearest_xterm256(semantic(theme, role))
            assert resolve(f"wash.{role}", "256", theme) == f"color({index})"
    ansi = load_tokens()["terminal"]["ansi16"]
    for role in ("accent", "running", "ok", "attention", "danger", "plan"):
        assert resolve(f"wash.{role}", "16", "dusk") == ansi[role]
    assert resolve("wash.muted", "16", "dusk") == ansi["muted"]
    assert resolve("wash.mode.loop", "16", "dusk") == ansi["mode.loop"]


def test_thinking_is_italic_and_muted():
    for mode in MODES:
        style = Style.parse(resolve("wash.thinking", mode, "dusk"))
        assert style.italic
        if mode == "none":
            assert style.dim
        else:
            assert style.color.name == Style.parse(resolve("wash.muted", mode, "dusk")).color.name


def test_diff_background_tint_only_in_truecolor_from_theme_washes():
    for theme in THEMES:
        add = Style.parse(resolve("wash.diff.add.line", "truecolor", theme))
        assert add.bgcolor.name == semantic(theme, "ok", "wash").lower()
        assert Style.parse(resolve("wash.diff.del.line", "truecolor", theme)).bgcolor.name == semantic(theme, "danger", "wash").lower()
    for mode in ("none", "16", "256"):
        assert Style.parse(resolve("wash.diff.add.line", mode, "dusk")).bgcolor is None
    source = (ROOT / "mu/ui/rich_ui.py").read_text(encoding="utf-8")
    assert "on #" not in source and "monokai" not in source


def test_named_styles_are_installed_on_every_console():
    console = _record(color_system="truecolor")
    for name in WASH_NAMES:
        console.get_style(name)  # MissingStyle if not installed
    assert wash.get_console() is wash.get_console()


def test_status_and_severity_styles_follow_the_vocabulary():
    vocab = load_vocabulary()
    for status, spec in vocab["status"].items():
        assert STYLE_SPECS[f"wash.status.{status}"].role == spec["semantic"]
    assert STYLE_SPECS["wash.severity.major"].role == "attention"


# --------------------------------------------------------------- escaping


def _approval_ui():
    from mu.ui.rich_ui import RichUI

    ui = RichUI()
    console = _record()
    ui.console = console
    return ui, console


def test_approval_card_prints_hostile_args_literally():
    ui, console = _approval_ui()
    console.print(ui.build_approval_panel(
        tool_name="evil[/cyan]tool", display_args={"pattern": HOSTILE}, count_info="(1/2)",
        choices=["y", "n", "e"],
    ))
    out = console.export_text()
    assert HOSTILE in out and "evil[/cyan]tool" in out
    assert "◆ Needs you · Permission to run evil[/cyan]tool" in out
    assert "[y] approve  [n] deny  [e] explain" in out


def test_approval_prompt_choice_label_is_unchanged():
    ui, _ = _approval_ui()
    seen = {}

    def fake_prompt_choice(prompt_text, *, choices, default=None):
        seen.update(prompt_text=prompt_text, choices=choices, default=default)
        return "n"

    ui.input_handler.prompt_choice = fake_prompt_choice
    choice, reason = ui.request_tool_approval(
        tool_name="write_file", tool_args={"filename": "[x].py"}, display_args={"filename": "[x].py"},
        count_info="", can_approve=True, modifications=[], preview_error=None, error_code=None,
        prompt_text="[wash.attention.bold]Permission Required[/]", choices=["y", "n", "e"], default="y",
    )
    assert (choice, reason) == ("n", None)
    assert seen == {"prompt_text": "Approval choice", "choices": ["y", "n", "e"], "default": "y"}


def test_show_info_is_literal_and_show_renderable_renders_tables():
    ui, console = _approval_ui()
    ui.show_info("[bold]not markup[/bold]")
    table = Table("agent", "status")
    table.add_row("child-1", "running")
    ui.show_renderable(table)
    out = console.export_text()
    assert "[bold]not markup[/bold]" in out
    assert "child-1" in out and "Table object" not in out


def test_splash_values_are_escaped():
    from mu.cli.display import splash_renderable

    console = _record()
    console.print(splash_renderable(
        session_name="s[/red]1", session_type="▱ workspace", agent_mode="feature",
        model_name="gpt-[bold]x", flags="thinking on", context="ctx 1 / 2",
        workspace="/tmp/[proj]",
    ))
    out = console.export_text()
    assert "s[/red]1" in out and "gpt-[bold]x" in out and "/tmp/[proj]" in out
    assert "μcli" in out and "━" in out
    assert "session" in out and "model" in out and "workspace" in out


def test_prompt_escapes_session_name():
    from mu.ui.input import InputHandler

    handler = InputHandler()
    handler.set_variables({})
    markup = handler.build_prompt_markup("a<b>&c", [], agent_mode="default")
    assert "a&lt;b&gt;&amp;c" in markup


# --------------------------------------------------------------- NO_COLOR


def test_no_color_output_keeps_glyphs_and_labels():
    console = _record(color_system=None)
    ui, _ = _approval_ui()
    ui.console = console
    console.print(ui.build_approval_panel(tool_name="write_file", display_args={"a": 1}, choices=["y", "n"]))
    for kind in glyphs.NOTICE_KINDS:
        console.print(components.notice_text(kind, f"{kind} notice"))
    console.print(components.tool_marker("read_file", "src/a.py"))
    console.print(components.tool_marker("read_file", "src/a.py", state="done", elapsed=0.4))
    console.print(components.meter("CTX", 90, 100))
    for status in load_vocabulary()["status"]:
        console.print(components.status_chip(status))
    out = console.export_text()
    assert "◆ Needs you" in out and "[y] approve" in out
    for kind, (glyph, _) in glyphs.NOTICE.items():
        assert f"{glyph} {kind} notice" in out
    assert "● read" in out and "✓ read" in out and "0.4s" in out
    assert "CTX" in out and "90/100" in out
    for status, spec in load_vocabulary()["status"].items():
        assert f"{spec['glyph']} {spec['label']}" in out
    # Attribute-only resolution keeps meaning without colour.
    assert resolve("wash.muted", "none", "dusk") == "dim"
    assert resolve("wash.key", "none", "dusk") == "bold"


def test_gallery_renders_in_every_mode(monkeypatch):
    from mu.ui.theme import gallery

    for depth in MODES:
        monkeypatch.setattr(wash, "mode", lambda depth=depth: depth)
        monkeypatch.setattr(gallery, "mode", lambda depth=depth: depth)
        console = gallery.render(width=100, record=True, file=io.StringIO())
        out = console.export_text()
        for needle in ("μcli", "◆ Needs you", "Proposed changes", "CTX", "Sub-agents",
                       "◌ ○ ◎ ●", "container · build image", "[plan]"):
            assert needle in out, (depth, needle)


# --------------------------------------------------------------- detection


def test_color_depth_detection():
    assert detect.color_depth({"NO_COLOR": "1", "COLORTERM": "truecolor"}) == "none"
    assert detect.color_depth({"COLORTERM": "truecolor"}) == "truecolor"
    assert detect.color_depth({"COLORTERM": "24bit"}) == "truecolor"
    assert detect.color_depth({"WT_SESSION": "1"}) == "truecolor"
    assert detect.color_depth({"TERM": "xterm-256color"}) == "256"
    assert detect.color_depth({"TERM": "xterm"}) == "16"
    assert detect.color_depth({}) == "16"


def _no_query():
    raise AssertionError("OSC 11 must not be queried")


def test_background_precedence():
    tty = lambda env: True  # noqa: E731
    white = lambda: "\x1b]11;rgb:ffff/ffff/ffff\x1b\\"  # noqa: E731
    black = lambda: "\x1b]11;rgb:0000/0000/0000\x07"  # noqa: E731
    # 1. explicit override wins over everything
    assert detect.background({"MUCLI_TERM_THEME": "paper", "COLORFGBG": "15;0"}, query=_no_query, tty_check=tty) == "paper"
    assert detect.background({"MUCLI_TERM_THEME": "dark", "COLORFGBG": "0;15"}, query=_no_query, tty_check=tty) == "dusk"
    # 2. COLORFGBG: background index 7 or 15 means paper
    assert detect.background({"COLORFGBG": "0;15"}, query=_no_query, tty_check=tty) == "paper"
    assert detect.background({"COLORFGBG": "0;default;7"}, query=_no_query, tty_check=tty) == "paper"
    assert detect.background({"COLORFGBG": "15;0"}, query=_no_query, tty_check=tty) == "dusk"
    # 3. OSC 11 reply (mocked)
    assert detect.background({}, query=white, tty_check=tty) == "paper"
    assert detect.background({}, query=black, tty_check=tty) == "dusk"
    # no TTY, a garbled reply or a timeout fall back to dusk
    assert detect.background({}, query=_no_query, tty_check=lambda env: False) == "dusk"
    assert detect.background({}, query=lambda: None, tty_check=tty) == "dusk"
    assert detect.background({}, query=lambda: "garbage", tty_check=tty) == "dusk"
    assert detect.background({}, probe=False, query=_no_query, tty_check=tty) == "dusk"


def test_osc11_is_skipped_under_tmux_and_screen(monkeypatch):
    class _Tty:
        def isatty(self):
            return True

    monkeypatch.setattr(detect.sys, "stdin", _Tty())
    monkeypatch.setattr(detect.sys, "stdout", _Tty())
    assert detect.osc11_allowed({})
    assert not detect.osc11_allowed({"TMUX": "/tmp/tmux-1/default,1,0"})
    assert not detect.osc11_allowed({"TERM": "screen-256color"})
    assert not detect.osc11_allowed({"STY": "1234.pts-0"})
    assert detect.background({"TMUX": "x"}, query=_no_query) == "dusk"


def test_parse_osc11_handles_short_and_long_channels():
    assert detect.parse_osc11("\x1b]11;rgb:ff/ff/ff\x07") == (1.0, 1.0, 1.0)
    assert detect.theme_for_rgb(detect.parse_osc11("\x1b]11;rgb:f8f8/f6f6/f1f1\x1b\\")) == "paper"
    assert detect.theme_for_rgb(detect.parse_osc11("\x1b]11;rgb:1616/1313/1b1b\x1b\\")) == "dusk"
    assert detect.parse_osc11("nope") is None


def test_osc11_query_has_a_100ms_timeout():
    assert detect.OSC11_TIMEOUT_S == 0.1


# --------------------------------------------------------------- components


def test_meter_thresholds_are_caution_065_and_danger_085():
    assert components.CAUTION_AT == 0.65 and components.DANGER_AT == 0.85

    def fill_style(ratio):
        line = components.meter("CTX", int(ratio * 100), 100, width=10)
        return next(span.style for span in line.spans if line.plain[span.start] == "█")

    assert fill_style(0.30) == "wash.meter.fill"
    assert fill_style(0.65) == "wash.caution"
    assert fill_style(0.84) == "wash.caution"
    assert fill_style(0.85) == "wash.danger"


def test_layer_bar_has_every_layer_label_and_style():
    layers = [(name, 100) for name in load_tokens()["layer"]]
    bar = components.layer_bar(layers, limit=10_000, width=40)
    for name in load_tokens()["layer"]:
        assert f" {name} " in bar.plain
        assert any(span.style == f"wash.layer.{name}" for span in bar.spans)


def test_show_notice_routes_to_rich_web_and_plain_uis():
    ui, console = _approval_ui()
    show_notice(ui, "plan", "PLAN MODE ENABLED [x]")
    assert "◇ PLAN MODE ENABLED [x]" in console.export_text()

    class _Plain:
        def __init__(self):
            self.info = []

        def show_info(self, message):
            self.info.append(message)

    plain = _Plain()
    show_notice(plain, "ok", "done")
    assert plain.info == [notice_plain("ok", "done")] == ["✓ done"]

    from mu.gui.prompts import PromptStore
    from mu.gui.web_ui import WebUI

    events = []

    class _Bus:
        def publish_threadsafe(self, event):
            events.append(event)

    web = WebUI(_Bus(), PromptStore(), session_name="s1")
    show_notice(web, "caution", "YOLO mode: ON")
    notices = [e for e in events if e.get("notice")]
    assert notices and notices[-1]["kind"] == "info"
    assert notices[-1]["text"] == "YOLO mode: ON" and notices[-1]["notice"] == "caution"


def test_tool_kind_labels_come_from_the_vocabulary():
    labels = {k: v["label"] for k, v in load_vocabulary()["tool_kind"].items()}
    assert glyphs.tool_kind_label("read_file") == labels["read"]
    assert glyphs.tool_kind_label("bash") == labels["shell"]
    assert glyphs.tool_kind_label("apply_diff") == labels["edit"]
    assert glyphs.tool_kind_label("spawn_agent") == labels["agent"]
    assert glyphs.tool_kind_label("mystery") == labels["other"]
    assert glyphs.SPINNER_FRAMES == tuple(load_vocabulary()["spinner"]["frames"])


# --------------------------------------------------------------- prompt_toolkit


@pytest.mark.parametrize("mode", MODES)
def test_one_prompt_style_defines_every_class_without_blink(mode):
    from mu.ui.input import MODE_PROMPT_STYLES

    rules = style_dict(mode, "dusk")
    for cls in set(MODE_PROMPT_STYLES.values()) | {"plan-indicator", "yolo-indicator", "prompt",
                                                     "current", "selected", "hint", "title"}:
        assert cls in rules, cls
    for name in load_tokens()["mode"]:
        assert f"mode-{name}" in rules
    assert not any("blink" in value for value in rules.values())
    assert "ansiblack" not in " ".join(rules.values())  # invisible hints on dark themes


def test_pickers_share_the_single_style():
    for rel in ("mu/ui/choice_picker.py", "mu/ui/choice_prompt.py", "mu/ui/quiz_picker.py",
                "mu/ui/session_picker.py", "mu/ui/input.py"):
        source = (ROOT / rel).read_text(encoding="utf-8")
        assert "Style.from_dict" not in source, rel
        assert "picker_style()" in source or "prompt_style()" in source, rel


# --------------------------------------------------------------- codebase rules


def _python_files():
    files = [ROOT / "mucli.py"]
    for folder in ("mu", "utils"):
        for path in sorted((ROOT / folder).rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if not rel.startswith("mu/gui/static/"):
                files.append(path)
    return files


def _is_console_ctor(node: ast.Call) -> bool:
    func = node.func
    name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
    return name == "Console"


def test_single_console_constructor_outside_the_theme_package():
    """The process console is built once, in ``mu/ui/theme`` (``get_console``).

    Elsewhere a ``Console(...)`` call is allowed only when it renders to a
    string buffer: it passes ``file=`` (e.g. ``mu/ui/markup.py:render_plain``)
    or ``record=True``. Tests are out of scope.
    """
    offenders = []
    theme_ctors = 0
    for path in _python_files():
        rel = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
                if rel == "mu/ui/theme/__init__.py" and name == "WashConsole":
                    theme_ctors += 1
                if not _is_console_ctor(node):
                    continue
                if rel.startswith("mu/ui/theme/"):
                    continue
                keywords = {kw.arg: kw.value for kw in node.keywords}
                renders_to_buffer = "file" in keywords or (
                    isinstance(keywords.get("record"), ast.Constant) and keywords["record"].value is True
                )
                if not renders_to_buffer:
                    offenders.append(f"{rel}:{node.lineno}")
    assert offenders == [], f"use mu.ui.theme.get_console() instead: {offenders}"
    assert theme_ctors == 1


def test_richui_console_is_the_process_console():
    from mu.ui.rich_ui import RichUI

    assert RichUI().console is wash.get_console()


_INLINE_COLOR = re.compile(r"\[(bold |dim |italic )?(red|green|yellow|blue|cyan|magenta|white|black)")


def test_no_inline_colour_markup_outside_the_theme_package():
    """Spec §6.3 acceptance grep, scoped to Python and excluding mu/gui/static."""
    hits = []
    for path in _python_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("mu/ui/theme/"):
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _INLINE_COLOR.search(line):
                hits.append(f"{rel}:{lineno}: {line.strip()}")
    assert hits == [], "\n".join(hits)


_CHROME_EMOJI = ("🔨", "⚠", "⏹", "🔒", "✦", "🧠", "🎯", "⚡", "💭", "📢", "🛠")


def test_no_emoji_in_terminal_chrome():
    hits = []
    for path in _python_files():
        rel = path.relative_to(ROOT).as_posix()
        if not rel.startswith(("mu/ui/", "mu/cli/", "mu/commands/", "mu/container/", "mucli.py",
                               "mu/agent/plan_mode.py", "mu/skills/", "utils/runtime_metrics.py")):
            continue
        source = path.read_text(encoding="utf-8")
        for emoji in _CHROME_EMOJI:
            if emoji in source:
                hits.append(f"{rel}: {emoji}")
    assert hits == []


def test_no_monokai_anywhere_in_the_terminal():
    for path in _python_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("mu/ui/theme/"):
            continue
        assert "monokai" not in path.read_text(encoding="utf-8"), rel


_ASSET_SUFFIX = re.compile(r"\.(svg|css|js|json|png|webp|woff2|ttf|html)$")
_STYLE_REF = re.compile(r"""(?<![\w{])(wash\.[a-z0-9_]+(?:\.[a-zA-Z0-9_]+)*)(?=[\]"' ])""")


def test_every_referenced_wash_style_exists():
    """Every literal ``wash.*`` name used in the terminal code is defined."""
    known = set(STYLE_NAMES)
    missing = []
    for path in _python_files():
        rel = path.relative_to(ROOT).as_posix()
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for name in _STYLE_REF.findall(line):
                if name.endswith(".") or name in known:
                    continue
                if _ASSET_SUFFIX.search(name):  # e.g. the web icon sprite "wash.svg"
                    continue
                if any(name.startswith(prefix) for prefix in ("wash.mode.", "wash.layer.", "wash.series.",
                                                                  "wash.status.", "wash.severity.", "wash.pigment.")) and name.count(".") >= 2 and name.split(".")[2] == "":
                    continue
                missing.append(f"{rel}:{lineno}: {name}")
    assert missing == [], "\n".join(missing)


def test_command_tables_use_simple_head():
    """G§9.19 T: Rich tables use box.SIMPLE_HEAD (no HEAVY/DOUBLE/ROUNDED tables)."""
    offenders = []
    for path in _python_files():
        rel = path.relative_to(ROOT).as_posix()
        if not rel.startswith(("mu/commands/", "mu/cli/", "mucli.py", "mu/ui/rich_ui.py", "mu/ui/progress.py")):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            if name != "Table":
                continue
            box_kw = next((kw.value for kw in node.keywords if kw.arg == "box"), None)
            if box_kw is None:
                offenders.append(f"{rel}:{node.lineno} (default box)")
            elif isinstance(box_kw, ast.Attribute) and box_kw.attr != "SIMPLE_HEAD":
                offenders.append(f"{rel}:{node.lineno} ({box_kw.attr})")
    assert offenders == [], offenders


def test_panels_never_colour_their_border_with_state():
    """G§3.4: state lives in the panel title, never in the panel edge."""
    from mu.ui.jobs.primitives import panel as jobs_panel
    from mu.ui.rich_ui import RichUI
    from mu.ui.theme import components

    for tone in ("attention", "danger", "plan", "ok"):
        rendered = components.panel(Text("body"), title="State", tone=tone)
        assert rendered.border_style == "wash.border", tone
        assert rendered.title.style == f"wash.{tone}"
    approval = RichUI().build_approval_panel(
        tool_name="write_file", display_args={"filename": "a.py"}, count_info="",
        can_approve=True, approval_policy="default", choices=["y", "n", "e"],
    )
    assert approval.border_style == "wash.border"
    lines = jobs_panel([[("", "x")]], 20, 3, title="Approvals", tone="warn")
    borders = {style for line in lines for style, chunk in line if chunk and chunk[0] in "╭╮╰╯│─"}
    assert borders == {"muted"}
    assert ("bold warn", "Approvals") in lines[0]
