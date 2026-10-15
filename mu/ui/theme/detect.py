"""Terminal colour-depth and background detection for the Wash theme.

Colour depth (``color_depth``):

* ``NO_COLOR`` set (any non-empty value) → ``none``
* ``COLORTERM`` in ``truecolor``/``24bit`` or ``WT_SESSION`` → ``truecolor``
* ``TERM`` containing ``256color`` → ``256``
* otherwise ``16``

Background (``background``), first match wins:

1. ``MUCLI_TERM_THEME=paper|dusk`` (``light``/``dark`` accepted)
2. ``COLORFGBG``: a background index of 7 or 15 means a light terminal
3. an OSC 11 query, only when stdin and stdout are TTYs, never under
   tmux/screen, with a 100ms timeout and a non-blocking read
4. ``dusk``

Both results are cached per process when read from ``os.environ``.
"""

from __future__ import annotations

import os
import re
import sys
import time
from typing import Callable, Mapping, Optional, Tuple

DEPTHS = ("none", "16", "256", "truecolor")
OSC11_TIMEOUT_S = 0.1

_cache: dict = {}


def reset_cache() -> None:
    """Forget cached detection results (tests and theme reloads)."""
    _cache.clear()


def color_depth(env: Optional[Mapping[str, str]] = None) -> str:
    """``none`` | ``16`` | ``256`` | ``truecolor``."""
    if env is None:
        if "depth" not in _cache:
            _cache["depth"] = color_depth(os.environ)
        return _cache["depth"]
    if env.get("NO_COLOR") not in (None, ""):
        return "none"
    colorterm = str(env.get("COLORTERM") or "").lower()
    if colorterm in ("truecolor", "24bit") or env.get("WT_SESSION"):
        return "truecolor"
    if "256color" in str(env.get("TERM") or "").lower():
        return "256"
    return "16"


def _theme_from_override(value: str) -> Optional[str]:
    text = str(value or "").strip().lower()
    if text in ("paper", "light"):
        return "paper"
    if text in ("dusk", "dark"):
        return "dusk"
    return None


def _theme_from_colorfgbg(value: str) -> Optional[str]:
    parts = [p for p in str(value or "").split(";") if p.strip()]
    if not parts:
        return None
    try:
        bg = int(parts[-1])
    except ValueError:
        return None
    return "paper" if bg in (7, 15) else "dusk"


_OSC11_RE = re.compile(
    r"\]11;rgba?:([0-9a-fA-F]{1,4})/([0-9a-fA-F]{1,4})/([0-9a-fA-F]{1,4})"
)


def parse_osc11(response: str) -> Optional[Tuple[float, float, float]]:
    """Parse an OSC 11 reply into 0..1 RGB channels (``None`` if malformed)."""
    match = _OSC11_RE.search(str(response or ""))
    if not match:
        return None
    channels = [int(part, 16) / float(16 ** len(part) - 1) for part in match.groups()]
    return channels[0], channels[1], channels[2]


def theme_for_rgb(rgb: Tuple[float, float, float]) -> str:
    r, g, b = rgb
    luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return "paper" if luminance >= 0.5 else "dusk"


def multiplexed(env: Mapping[str, str]) -> bool:
    """True under tmux or GNU screen, where OSC replies are unreliable."""
    term = str(env.get("TERM") or "").lower()
    return bool(env.get("TMUX") or env.get("STY")) or term.startswith(("screen", "tmux"))


def query_osc11(timeout: float = OSC11_TIMEOUT_S) -> Optional[str]:
    """Ask the terminal for its background colour and return the raw reply.

    POSIX only. The terminal is put in cbreak mode for at most ``timeout``
    seconds and always restored. Any failure returns ``None``.
    """
    try:
        import select
        import termios
        import tty
    except ImportError:  # pragma: no cover - non-POSIX
        return None
    try:
        fd_in = sys.stdin.fileno()
        fd_out = sys.stdout.fileno()
        old = termios.tcgetattr(fd_in)
    except Exception:  # noqa: BLE001 - not a real terminal
        return None
    reply = b""
    try:
        tty.setcbreak(fd_in)
        os.write(fd_out, b"\x1b]11;?\x1b\\")
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            ready, _, _ = select.select([fd_in], [], [], remaining)
            if not ready:
                break
            chunk = os.read(fd_in, 64)
            if not chunk:
                break
            reply += chunk
            if reply.endswith(b"\x07") or reply.endswith(b"\x1b\\"):
                break
    except OSError:
        return None
    finally:
        try:
            termios.tcsetattr(fd_in, termios.TCSADRAIN, old)
        except Exception:  # noqa: BLE001
            pass
    return reply.decode("ascii", errors="ignore") or None


def osc11_allowed(env: Mapping[str, str]) -> bool:
    if multiplexed(env):
        return False
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except (AttributeError, ValueError):
        return False


def background(
    env: Optional[Mapping[str, str]] = None,
    *,
    probe: bool = True,
    query: Optional[Callable[[], Optional[str]]] = None,
    tty_check: Optional[Callable[[Mapping[str, str]], bool]] = None,
) -> str:
    """``paper`` | ``dusk`` for the terminal background (see module doc)."""
    cached = env is None
    if cached and "background" in _cache:
        return _cache["background"]
    source = os.environ if env is None else env
    theme = _theme_from_override(source.get("MUCLI_TERM_THEME", ""))
    if theme is None:
        theme = _theme_from_colorfgbg(source.get("COLORFGBG", ""))
    settled = theme is not None or probe
    if theme is None and probe and (tty_check or osc11_allowed)(source):
        reply = (query or query_osc11)()
        rgb = parse_osc11(reply) if reply else None
        if rgb is not None:
            theme = theme_for_rgb(rgb)
    if theme is None:
        theme = "dusk"
    if cached and settled:
        # An unprobed default is not cached, so a later probing call can
        # still ask the terminal.
        _cache["background"] = theme
    return theme


__all__ = [
    "DEPTHS", "OSC11_TIMEOUT_S", "background", "color_depth", "multiplexed",
    "osc11_allowed", "parse_osc11", "query_osc11", "reset_cache", "theme_for_rgb",
]
