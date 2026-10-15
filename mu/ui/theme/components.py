"""Small Wash terminal components shared by RichUI, commands and the gallery.

Every builder returns a Rich renderable that uses ``wash.*`` style names
only, so it renders correctly in every colour mode (and with ``NO_COLOR`` the
glyphs and labels still carry the meaning).
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence, Tuple

from rich import box
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from mu.design import load_tokens

from . import glyphs

#: meter ratio thresholds (aligned with the web meters, spec §10.8)
CAUTION_AT = 0.65
DANGER_AT = 0.85

PANEL_BOX = box.ROUNDED
TABLE_BOX = box.SIMPLE_HEAD


def notice_text(kind: str, message: str) -> Text:
    """``glyph message`` in the semantic colour of ``kind`` (message literal)."""
    glyph, role = glyphs.NOTICE.get(kind, glyphs.NOTICE["info"])
    text = Text()
    text.append(f"{glyph} ", style=f"wash.{role}.bold")
    text.append(str(message), style=f"wash.{role}")
    return text


def key_hints(pairs: Iterable[Tuple[str, str]]) -> Text:
    """Key-hint row: ``[a] approve  [d] deny`` (key accent bold, label ink-2)."""
    text = Text()
    for index, (key, label) in enumerate(pairs):
        if index:
            text.append("  ")
        text.append(f"[{key}]", style="wash.key")
        text.append(f" {label}", style="wash.ink2")
    return text


def meter_style(ratio: float, base: str = "wash.meter.fill") -> str:
    if ratio >= DANGER_AT:
        return "wash.danger"
    if ratio >= CAUTION_AT:
        return "wash.caution"
    return base


def meter(label: str, current: int, maximum: int, *, width: int = 16,
          fill_style: str = "wash.meter.fill",
          caution_at: float = CAUTION_AT, danger_at: float = DANGER_AT) -> Text:
    """``LABEL  ██████░░░░ 12/40``; caution at ≥0.65, danger at ≥0.85."""
    maximum = max(1, int(maximum or 1))
    current = max(0, int(current or 0))
    ratio = min(current / maximum, 1.0)
    filled = min(width, int(round(width * ratio)))
    if ratio >= danger_at:
        active = "wash.danger"
    elif ratio >= caution_at:
        active = "wash.caution"
    else:
        active = fill_style
    line = Text()
    line.append(f"{label:<8}", style="wash.ink2.bold")
    line.append(" ")
    line.append(glyphs.METER_FILL * filled, style=active)
    line.append(glyphs.METER_EMPTY * (width - filled), style="wash.meter.empty")
    line.append(f" {current}/{maximum}", style="wash.muted")
    return line


def layer_cells(values: Sequence[int], width: int) -> List[int]:
    """Split ``width`` cells across ``values`` (every non-zero value gets ≥1)."""
    total = sum(max(0, int(v or 0)) for v in values)
    if total <= 0 or width <= 0:
        return [0 for _ in values]
    cells = [int(width * max(0, int(v or 0)) / total) for v in values]
    for i, v in enumerate(values):
        if v and not cells[i]:
            cells[i] = 1
    while sum(cells) > width:
        i = cells.index(max(cells))
        cells[i] -= 1
    remainders = sorted(
        range(len(values)),
        key=lambda i: (width * max(0, int(values[i] or 0)) / total) - cells[i],
        reverse=True,
    )
    for i in remainders:
        if sum(cells) >= width:
            break
        if values[i]:
            cells[i] += 1
    return cells


def layer_bar(layers: Sequence[Tuple[str, int]], *, limit: Optional[int] = None,
              width: int = 48) -> Text:
    """Stacked L0–L5 bar in block characters, coloured per layer, with labels.

    ``layers`` is ``[("L0", tokens), ("L1A", tokens), ...]``. With ``limit``
    the bar is scaled against the context cap and the remainder is empty.
    """
    used = sum(max(0, int(v or 0)) for _, v in layers)
    span = width
    if limit:
        span = min(width, int(round(width * min(1.0, used / max(1, int(limit))))))
        if used and not span:
            span = 1
    cells = layer_cells([v for _, v in layers], span)
    bar = Text()
    for (name, _), count in zip(layers, cells):
        if count:
            bar.append(glyphs.METER_FILL * count, style=f"wash.layer.{name}")
    bar.append(glyphs.METER_EMPTY * max(0, width - sum(cells)), style="wash.meter.empty")
    names = load_tokens().get("layer_names", {})
    legend = Text()
    for index, (name, value) in enumerate(layers):
        if index:
            legend.append("  ")
        legend.append(glyphs.METER_FILL, style=f"wash.layer.{name}")
        legend.append(f" {name}", style="wash.ink2.bold")
        label = names.get(name)
        if label:
            legend.append(f" {label}", style="wash.muted")
        legend.append(f" {int(value or 0):,}", style="wash.muted")
    return Text.assemble(bar, "\n", legend)


def gradient_rule(width: int = 32) -> Text:
    """``━━━━`` cycling apricot → rose → lilac → lagoon (pigment step)."""
    stops = ("apricot", "rose", "lilac", "lagoon")
    text = Text()
    per = max(1, width // len(stops))
    for index in range(width):
        stop = stops[min(len(stops) - 1, index // per)]
        text.append(glyphs.RULE, style=f"wash.pigment.{stop}")
    return text


def tool_marker(tool_name: str, target: str = "", *, state: str = "active",
                elapsed: Optional[float] = None) -> Text:
    """``● read  src/app.py`` live; ``✓ read  src/app.py  0.4s`` muted when done."""
    kind = glyphs.tool_kind_label(tool_name)
    target = str(target or tool_name)
    text = Text()
    if state == "active":
        text.append(f"{glyphs.ACTIVE} ", style="wash.running")
        text.append(f"{kind:<7}", style="wash.running")
        text.append(f" {target}", style="wash.ink2")
        return text
    if state == "failed":
        text.append(f"{glyphs.FAILED} ", style="wash.danger")
        text.append(f"{kind:<7} {target}", style="wash.muted")
    else:
        text.append(f"{glyphs.DONE} {kind:<7} {target}", style="wash.muted")
    if elapsed is not None:
        text.append(f"  {elapsed:.1f}s", style="wash.muted")
    return text


def status_chip(status: str, label: Optional[str] = None) -> Text:
    """``◆ needs you`` in the status's semantic ink, no background."""
    glyph = glyphs.status_glyph(status)
    text = Text(f"{glyph} {label or glyphs.STATUS_LABELS.get(status, status)}",
                style=f"wash.status.{status if status in glyphs.STATUS_GLYPHS else 'pending'}")
    return text


def panel(body, *, title=None, tone: str = "border", **kwargs) -> Panel:
    """Rounded panel with a neutral border.

    Wash never colours panel edges with state (G§3.4): a semantic ``tone``
    colours a plain-string title instead; callers that pass a styled
    ``Text`` title keep their own styling.
    """
    border = "wash.border"
    if isinstance(title, str) and tone != "border":
        title = Text(title, style=f"wash.{tone}")
    kwargs.setdefault("box", PANEL_BOX)
    kwargs.setdefault("title_align", "left")
    return Panel(body, title=title, border_style=border, **kwargs)


def table(*columns: str, title: Optional[str] = None, **kwargs) -> Table:
    """Rich table in the Wash shape: ``SIMPLE_HEAD``, ink-2 bold header."""
    kwargs.setdefault("box", TABLE_BOX)
    kwargs.setdefault("header_style", "wash.table.header")
    kwargs.setdefault("title_style", "wash.heading")
    kwargs.setdefault("border_style", "wash.border")
    kwargs.setdefault("show_edge", False)
    return Table(*columns, title=title, **kwargs)


__all__ = [
    "CAUTION_AT", "DANGER_AT", "PANEL_BOX", "TABLE_BOX", "gradient_rule", "key_hints",
    "layer_bar", "layer_cells", "meter", "meter_style", "notice_text", "panel",
    "status_chip", "table", "tool_marker",
]
