"""Layout primitives for the terminal job dashboard (planos ``src/tui/ui``).

Everything renders to *lines*: a line is a list of ``(style, text)`` segments
where ``style`` is a space-separated list of theme tokens and attributes
(``"accent bold"``). Lines are plain data, so every view can be unit-tested
as text (``to_plain``) or ANSI (``to_ansi``) without a terminal, and the
prompt_toolkit shell just converts them with ``to_pt``.
"""

from __future__ import annotations

import math
import re
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from prompt_toolkit.utils import get_cwidth

from .theme import SPINNER, STATUS, pt_style, sgr

Seg = Tuple[str, str]
Line = List[Seg]
Cellish = Union[str, Seg, Line]

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", str(text or ""))


def clean(text: Any) -> str:
    """One display line: no ANSI, no control characters, tabs expanded."""
    s = strip_ansi(str(text if text is not None else "")).replace("\t", "    ")
    return "".join(ch for ch in s if ch >= " " or ch == "\n").replace("\r", "")


def cwidth(text: str) -> int:
    return sum(max(0, get_cwidth(ch)) for ch in text)


def fit(text: str, width: int) -> str:
    """Truncate to ``width`` cells with an ellipsis (no padding)."""
    if width <= 0:
        return ""
    text = str(text)
    if cwidth(text) <= width:
        return text
    if width == 1:
        return "…"
    out, used = [], 0
    for ch in text:
        w = max(0, get_cwidth(ch))
        if used + w > width - 1:
            break
        out.append(ch)
        used += w
    return "".join(out) + "…"


def pad(text: str, width: int, align: str = "left") -> str:
    t = fit(text, width)
    gap = " " * max(0, width - cwidth(t))
    return gap + t if align == "right" else t + gap


def L(*parts: Cellish) -> Line:
    """Build a line from strings and ``(style, text)`` tuples."""
    out: Line = []
    for p in parts:
        if isinstance(p, str):
            out.append(("", p))
        elif isinstance(p, tuple):
            out.append((p[0], p[1]))
        elif isinstance(p, list):
            out.extend(p)
    return out


def line_width(line: Line) -> int:
    return sum(cwidth(t) for _, t in line)


def line_text(line: Line) -> str:
    return "".join(t for _, t in line)


def fit_line(line: Line, width: int, *, pad_to: bool = True, fill_style: str = "") -> Line:
    """Clip ``line`` to ``width`` cells (ellipsis when clipped) and pad it."""
    if width <= 0:
        return []
    total = line_width(line)
    out: Line = []
    if total <= width:
        out = list(line)
        if pad_to and total < width:
            out.append((fill_style, " " * (width - total)))
        return out
    room = width - 1
    used = 0
    last_style = ""
    for style, text in line:
        if used >= room:
            break
        chunk = []
        for ch in text:
            w = max(0, get_cwidth(ch))
            if used + w > room:
                break
            chunk.append(ch)
            used += w
        if chunk:
            out.append((style, "".join(chunk)))
        last_style = style
        if used >= room:
            break
    out.append((last_style, "…"))
    used += 1
    if pad_to and used < width:
        out.append(("", " " * (width - used)))
    return out


def add_style(line: Line, extra: str) -> Line:
    return [((s + " " + extra).strip(), t) for s, t in line]


def to_plain(lines: Iterable[Line]) -> str:
    return "\n".join(line_text(l).rstrip() for l in lines)


def to_ansi(lines: Iterable[Line], mode: str = "truecolor") -> str:
    rows = []
    for line in lines:
        buf = []
        for style, text in line:
            code = sgr(style, mode)
            buf.append(f"\x1b[{code}m{text}\x1b[0m" if code else text)
        rows.append("".join(buf))
    return "\n".join(rows)


def to_pt(lines: Sequence[Line], mode: str = "truecolor") -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    cache: Dict[str, str] = {}
    for i, line in enumerate(lines):
        for style, text in line:
            if style not in cache:
                cache[style] = pt_style(style, mode)
            out.append((cache[style], text))
        if i < len(lines) - 1:
            out.append(("", "\n"))
    return out


# ---------------------------------------------------------------- formatting

def money(value: Any) -> str:
    try:
        v = float(value or 0.0)
    except (TypeError, ValueError):
        v = 0.0
    return f"${v:.2f}"


def duration(seconds: Any) -> str:
    try:
        s = max(0, int(float(seconds or 0)))
    except (TypeError, ValueError):
        return ""
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m"
    h, m = divmod(m, 60)
    if h < 48:
        return f"{h}h{m:02d}m" if m else f"{h}h"
    return f"{h // 24}d"


def age(ts: Any, now: Optional[float] = None) -> str:
    try:
        t = float(ts or 0)
    except (TypeError, ValueError):
        return ""
    if not t:
        return ""
    s = max(0, int((now or time.time()) - t))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400 * 2:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def nbytes(value: Any) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    for unit in ("B", "K", "M", "G", "T"):
        if abs(v) < 1024 or unit == "T":
            return f"{v:.0f}{unit}" if unit in ("B", "K") else f"{v:.1f}{unit}"
        v /= 1024
    return f"{v:.1f}T"


# ---------------------------------------------------------------- status / spinner

def spinner_frame(tick: int) -> str:
    return SPINNER[tick % len(SPINNER)]


def status_icon(status: str, tick: Optional[int] = None) -> Seg:
    """The status vocabulary glyph; ``active`` animates when ``tick`` is given."""
    spec = STATUS.get(status, STATUS["pending"])
    if status == "active" and tick is not None:
        return (spec["token"], spinner_frame(tick))
    return (spec["token"], spec["icon"])


def key_hints(hints: Sequence[Tuple[str, str]], width: int,
              pinned: Sequence[Tuple[str, str]] = ((":", "commands"), ("?", "help"))) -> Line:
    """`enter open · : commands · ? help` — screen hints first, pinned always."""
    def length(hs):
        return sum((3 if i else 0) + cwidth(k) + 1 + cwidth(l) for i, (k, l) in enumerate(hs))
    room = width - 1 - (length(pinned) + 3 if pinned else 0)
    shown: List[Tuple[str, str]] = []
    for h in hints:
        if length(shown + [h]) > room:
            break
        shown.append(h)
    out: Line = [("", " ")]
    for i, (k, label) in enumerate(list(shown) + list(pinned)):
        if i:
            out.append(("muted", " · "))
        out.append(("accent bold", k))
        out.append(("muted", " " + label))
    return fit_line(out, width)


# ---------------------------------------------------------------- bars

_EIGHTHS = ("", "▏", "▎", "▍", "▌", "▋", "▊", "▉")


def progress_text(value: float, width: int) -> str:
    """`▕████▌    ▏` with eighth-block resolution; ``width`` includes the caps."""
    inner = max(1, width - 2)
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = 0.0
    if not math.isfinite(v):
        v = 0.0
    v = max(0.0, min(1.0, v))
    cells = v * inner
    full = int(math.floor(cells))
    part = _EIGHTHS[int(round((cells - full) * 8))] if round((cells - full) * 8) < 8 else ""
    body = ("█" * full + (part if full < inner else "")).ljust(inner)[:inner]
    return f"▕{body}▏"


def stacked_cells(values: Sequence[float], width: int) -> List[int]:
    """Proportional segments; every non-zero value gets at least one cell."""
    total = sum(values)
    if not total or width <= 0:
        return [0 for _ in values]
    raw = [v / total * width for v in values]
    cells = [max(1, int(math.floor(r))) if values[i] > 0 else 0 for i, r in enumerate(raw)]
    diff = width - sum(cells)
    order = sorted(range(len(values)), key=lambda i: -(raw[i] - math.floor(raw[i])))
    k = 0
    while diff > 0 and k < len(order) * 4:
        i = order[k % len(order)]
        if values[i] > 0:
            cells[i] += 1
            diff -= 1
        k += 1
    while diff < 0:
        i = cells.index(max(cells))
        if cells[i] <= 1:
            break
        cells[i] -= 1
        diff += 1
    return cells


def stacked_bar(segments: Sequence[Tuple[float, str]], width: int) -> Line:
    cells = stacked_cells([s[0] for s in segments], width)
    if not any(cells):
        return [("muted", "░" * max(0, width))]
    return [(tone, "█" * n) for (_, tone), n in zip(segments, cells) if n]


_LEFT = (0x40, 0x04, 0x02, 0x01)
_RIGHT = (0x80, 0x20, 0x10, 0x08)


def sparkline(values: Sequence[Optional[float]], width: int, top: Optional[float] = None) -> str:
    """Braille sparkline: two samples per cell, four dot rows; ``None`` = gap."""
    n = max(1, width) * 2
    vals = list(values)[-n:]
    known = [v for v in vals if v is not None and math.isfinite(v)]
    peak = top if top else max([1e-9] + known)

    def level(v):
        if v is None:
            return -1
        return max(0, min(4, int(round(v / peak * 4))))

    padded = [None] * max(0, n - len(vals)) + vals
    out = []
    for i in range(0, len(padded), 2):
        a = level(padded[i])
        b = level(padded[i + 1] if i + 1 < len(padded) else None)
        bits = 0
        for r in range(4):
            if a > r or (a == 0 and r == 0):
                bits |= _LEFT[r]
            if b > r or (b == 0 and r == 0):
                bits |= _RIGHT[r]
        out.append(" " if a < 0 and b < 0 else chr(0x2800 + bits))
    return "".join(out)


# ---------------------------------------------------------------- stepper

def _step_secs(step: Dict[str, Any], now: Optional[float], fetched_at: Optional[float]) -> float:
    secs = float(step.get("elapsed") or 0.0)
    if step.get("status") in ("active",) and now and fetched_at:
        secs += max(0.0, now - fetched_at)
    return secs


def mini_stepper(steps: Sequence[Dict[str, Any]], tick: Optional[int] = None) -> Line:
    """Compact pipeline `✓─✓─●─○─○─○` (11 cells)."""
    out: Line = []
    for i, s in enumerate(steps):
        if i:
            out.append(("muted", "─"))
        out.append(status_icon(str(s.get("status") or "pending"), tick))
    return out


def stepper(steps: Sequence[Dict[str, Any]], width: int, *, tick: Optional[int] = None,
            now: Optional[float] = None, fetched_at: Optional[float] = None) -> Line:
    """`Plan ✓ 6m ── Approve ✓ ── Implement ● 18m ── …`; compact when narrow."""
    def dur(s):
        secs = _step_secs(s, now, fetched_at)
        return f" {duration(secs)}" if s.get("id") != "approve" and secs >= 1 else ""
    full = " ── ".join(f"{s.get('label')} {STATUS.get(s.get('status'), STATUS['pending'])['icon']}{dur(s)}"
                       for s in steps)
    if cwidth(full) > width:
        short = " ─ ".join(f"{str(s.get('label'))[:4]} {STATUS.get(s.get('status'), STATUS['pending'])['icon']}"
                           for s in steps)
        if cwidth(short) <= width:
            out: Line = []
            for i, s in enumerate(steps):
                st = str(s.get("status") or "pending")
                if i:
                    out.append(("muted", " ─ "))
                out.append(("muted" if st in ("pending", "skipped") else "", str(s.get("label"))[:4] + " "))
                out.append(status_icon(st, tick))
            return out
        return mini_stepper(steps, tick)
    out = []
    for i, s in enumerate(steps):
        st = str(s.get("status") or "pending")
        if i:
            out.append(("muted", " ── "))
        style = "muted" if st in ("pending", "skipped") else ("bold" if st in ("active", "waiting", "failed") else "")
        out.append((style, f"{s.get('label')} "))
        out.append(status_icon(st, tick))
        d = dur(s)
        if d:
            out.append(("muted", d))
    return out


# ---------------------------------------------------------------- panel / split / tabs

def panel(body: Sequence[Line], width: int, height: int, *, title: str = "", badge: str = "",
          badge_tone: str = "muted", focused: bool = False, tone: str = "", pad_x: int = 0,
          top: int = 0) -> List[Line]:
    """Rounded panel with a title (and badge) in the top border; body clipped."""
    if width < 4 or height < 2:
        return [fit_line(l, width) for l in list(body)[:max(0, height)]]
    border = tone or ("accent" if focused else "muted")
    head: Line = [(border, "╭")]
    used = 1
    if title:
        t = fit(title, max(1, width - 4))
        head += [("", " "), ("bold accent" if focused else "bold", t)]
        used += 1 + cwidth(t)
        if badge and used + 2 + cwidth(badge) < width - 1:
            head.append((badge_tone, " " + badge))
            used += 1 + cwidth(badge)
        head.append(("", " "))
        used += 1
    head.append((border, "─" * max(0, width - used - 1) + "╮"))
    inner = width - 2 - 2 * pad_x
    rows = list(body)[top:top + height - 2]
    out = [head]
    for r in rows:
        out.append([(border, "│"), ("", " " * pad_x)] + fit_line(r, inner) + [("", " " * pad_x), (border, "│")])
    for _ in range(height - 2 - len(rows)):
        out.append([(border, "│"), ("", " " * (width - 2)), (border, "│")])
    out.append([(border, "╰" + "─" * (width - 2) + "╯")])
    return out


def hjoin(blocks: Sequence[Tuple[List[Line], int]], gap: int = 0) -> List[Line]:
    """Place blocks (lines, width) side by side, padding each to its width."""
    height = max((len(b) for b, _ in blocks), default=0)
    out: List[Line] = []
    for i in range(height):
        row: Line = []
        for j, (b, w) in enumerate(blocks):
            if j and gap:
                row.append(("", " " * gap))
            row.extend(fit_line(b[i] if i < len(b) else [], w))
        out.append(row)
    return out


def split_widths(total: int, *, ratio: float = 0.5, fixed: Optional[int] = None,
                 collapse_below: Optional[int] = None, gap: int = 0) -> Tuple[int, int]:
    if collapse_below is not None and total < collapse_below:
        return total, 0
    if fixed is not None:
        second = min(fixed, max(0, total - gap - 10))
    else:
        second = int((total - gap) * (1 - ratio))
    return max(0, total - second - gap), second


def tabs(specs: Sequence[Dict[str, Any]], active: str, width: int, tick: Optional[int] = None) -> Line:
    """`Session  Plan  Diff +142 −9  Review 3  Checks ●` — active underlined."""
    out: Line = []
    used = 0
    for t in specs:
        label = str(t["label"])
        count = t.get("count")
        count_s = "" if count in (None, "") else f" {count}"
        icon = t.get("icon")
        w = cwidth(label) + cwidth(count_s) + (2 if icon else 0) + 2
        if used + w > width and out:
            break
        used += w
        on = t["id"] == active
        out.append(("", " "))
        out.append(("accent bold underline" if on else (t.get("tone") or ""), label))
        if count_s:
            out.append((t.get("tone") or "muted", count_s))
        if icon:
            out.append(("", " "))
            out.append(status_icon(icon, tick))
        out.append(("", " "))
    return fit_line(out, width)


def rule(width: int, label: str = "") -> Line:
    head = f"── {label} " if label else ""
    return [("muted", fit(head + "─" * max(0, width - cwidth(head)), width))]


def wrap_text(text: str, width: int) -> List[str]:
    """Word-wrap ``text`` (newlines kept) to ``width`` cells."""
    import textwrap
    out: List[str] = []
    width = max(4, width)
    for raw in clean(text).split("\n"):
        if not raw.strip():
            out.append("")
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        out.extend(textwrap.wrap(raw, width=width, subsequent_indent=" " * min(indent, 8),
                                 break_long_words=True, break_on_hyphens=False,
                                 drop_whitespace=True) or [""])
    return out


# ---------------------------------------------------------------- table

class Column:
    def __init__(self, id: str, header: str, cell: Callable[[Any], Cellish], *, width: Optional[int] = None,
                 flex: float = 0, min: int = 6, align: str = "left", hide_below: int = 0):
        self.id, self.header, self.cell = id, header, cell
        self.width, self.flex, self.min, self.align, self.hide_below = width, flex, min, align, hide_below


def layout_columns(cols: Sequence[Column], width: int, gap: int = 2) -> List[Tuple[Column, int]]:
    visible = [c for c in cols if not c.hide_below or width >= c.hide_below]
    fixed = sum(0 if c.flex else (c.width or cwidth(c.header)) for c in visible)
    gaps = gap * max(0, len(visible) - 1)
    flex_total = sum(c.flex for c in visible if c.flex) or 1
    out = []
    for c in visible:
        if c.flex:
            out.append([c, max(c.min, int((width - fixed - gaps) * (c.flex / flex_total)))])
        else:
            out.append([c, c.width or cwidth(c.header)])
    used = sum(w for _, w in out) + gaps
    flexes = [o for o in out if o[0].flex]
    if flexes:
        flexes[-1][1] = max(1, flexes[-1][1] + (width - used))
    return [(c, w) for c, w in out]


def _cell_line(value: Cellish) -> Line:
    if isinstance(value, str):
        return [("", value)]
    if isinstance(value, tuple):
        return [value]
    return list(value or [])


def table(cols: Sequence[Column], rows: Sequence[Any], width: int, height: int, *, cursor: int = -1,
          active: bool = True, empty: str = "(nothing here)", header: bool = True) -> List[Line]:
    """Column-spec table with a sticky header and a cursor (planos Table)."""
    layout = layout_columns(cols, max(4, width - 2))
    out: List[Line] = []
    if header:
        h: Line = [("", "  ")]
        for i, (c, w) in enumerate(layout):
            if i:
                h.append(("", "  "))
            h.append(("muted bold", pad(c.header, w, c.align)))
        out.append(fit_line(h, width))
    body_h = max(1, height - (1 if header else 0))
    if not rows:
        out.append(fit_line([("muted", "  " + empty)], width))
        return out
    scrolls = len(rows) > body_h
    view_h = max(1, body_h - 1) if scrolls else body_h
    start = 0 if cursor < 0 else min(max(0, cursor - view_h // 2), max(0, len(rows) - view_h))
    for idx in range(start, min(len(rows), start + view_h)):
        r = rows[idx]
        sel = idx == cursor
        line: Line = [("accent", "› " if sel else "  ")]
        for i, (c, w) in enumerate(layout):
            if i:
                line.append(("", "  "))
            cell = fit_line(_cell_line(c.cell(r)), w)
            if c.align == "right":
                txt = line_text(cell).rstrip()
                cell = [(cell[0][0] if cell else "", pad(txt, w, "right"))]
            line.extend(add_style(cell, "bold") if sel and active else cell)
        line = fit_line(line, width)
        out.append(add_style(line, "inverse") if sel and active else line)
    if scrolls:
        pos = cursor + 1 if cursor >= 0 else min(len(rows), start + view_h)
        out.append([("muted", f"  {pos}/{len(rows)}")])
    return out


def scroll_window(lines: Sequence[Line], height: int, top: int) -> Tuple[List[Line], int]:
    """Clamp ``top`` and return the visible slice."""
    max_top = max(0, len(lines) - height)
    top = max(0, min(top, max_top))
    return list(lines[top:top + height]), top
