"""Interactive arrow-key session picker.

Two layers:

  * `SessionPickerState` — pure state machine. Cursor, list, pending-
    delete flag. Pure functions, no I/O. Trivially testable.
  * `run_interactive_picker(...)` — prompt-toolkit `Application` shell
    that wires keys to state transitions and renders the list. Falls
    back gracefully when prompt-toolkit can't drive the current TTY
    (CI, weird shells) — the caller catches the exception and shows
    the numbered fallback picker.

Key bindings:
    ↑ / ↓        — move cursor (also k / j for vim-style)
    Enter        — load highlighted session  (or [+ New Session])
    n            — shortcut to [+ New Session]
    d            — request delete on highlighted session
    Y            — confirm pending delete
    Esc / other  — cancel pending delete
    q / Ctrl-C   — quit (returns ("quit", None) — caller decides)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple


# Sentinel for the "[+ New Session]" virtual item.
NEW_SESSION = None  # type: Optional[str]


@dataclass
class SessionPickerState:
    """Pure picker state. Holds the list, the cursor, and any pending
    delete. Every key binding in the UI shell calls one of these
    methods — keeps the imperative TTY code thin."""

    sessions: List[str] = field(default_factory=list)
    cursor: int = 0
    pending_delete: Optional[str] = None

    @property
    def items(self) -> List[Optional[str]]:
        """Sessions plus a `None` sentinel for [+ New Session]."""
        return [*self.sessions, NEW_SESSION]

    def move(self, delta: int) -> None:
        n = len(self.items)
        if n == 0:
            self.cursor = 0
            return
        self.cursor = (self.cursor + delta) % n

    def current(self) -> Optional[str]:
        items = self.items
        if not items:
            return NEW_SESSION
        self.cursor = max(0, min(self.cursor, len(items) - 1))
        return items[self.cursor]

    def request_delete(self) -> bool:
        """Stage a delete on the highlighted session. Returns True if a
        delete is now pending; False if the cursor was on [+ New
        Session] (can't delete that)."""
        cur = self.current()
        if cur is NEW_SESSION:
            return False
        self.pending_delete = cur
        return True

    def confirm_delete(self) -> Optional[str]:
        """Apply the pending delete locally and return the deleted name.
        Caller is responsible for the actual on-disk removal. Returns
        None if no delete was pending."""
        if not self.pending_delete:
            return None
        name = self.pending_delete
        self.sessions = [s for s in self.sessions if s != name]
        # Keep cursor in-bounds. If we deleted the last item, clamp.
        if self.cursor >= len(self.items):
            self.cursor = max(0, len(self.items) - 1)
        self.pending_delete = None
        return name

    def cancel_delete(self) -> None:
        self.pending_delete = None


def run_interactive_picker(
    sessions: List[str],
    *,
    on_delete: Callable[[str], None],
) -> Tuple[str, Optional[str]]:
    """Run the prompt-toolkit picker.

    `on_delete(name)` is invoked for every confirmed delete so the
    caller can drop the session from disk. The picker keeps running
    after each delete so the user can remove multiple.

    Returns one of:
        ("load", session_name)
        ("new", None)
        ("quit", None)        — Ctrl-C / q
    """
    from prompt_toolkit.application import Application
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import HSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl

    state = SessionPickerState(sessions=list(sessions))
    kb = KeyBindings()

    def _render() -> FormattedText:
        lines: list = []
        for i, item in enumerate(state.items):
            is_current = i == state.cursor
            cursor_marker = "❯ " if is_current else "  "
            if item is NEW_SESSION:
                label = "[+ New Session]"
                style = "class:new bold" if is_current else "class:new"
            else:
                label = str(item)
                style = "class:current bold" if is_current else ""
            lines.append((style, f"{cursor_marker}{label}\n"))

        if state.pending_delete:
            lines.append(("", "\n"))
            lines.append(
                (
                    "class:warn bold",
                    f"Delete {state.pending_delete!r}? Press Y to confirm, "
                    "any other key to cancel.\n",
                )
            )
        else:
            lines.append(("", "\n"))
            lines.append(
                (
                    "class:hint",
                    "↑/↓ navigate · Enter load · n new · d delete · q quit\n",
                )
            )
        return FormattedText(lines)

    @kb.add("up")
    @kb.add("k")
    def _(event):
        if state.pending_delete:
            state.cancel_delete()
            return
        state.move(-1)

    @kb.add("down")
    @kb.add("j")
    def _(event):
        if state.pending_delete:
            state.cancel_delete()
            return
        state.move(1)

    @kb.add("enter")
    def _(event):
        if state.pending_delete:
            # Enter does NOT confirm — Y does. Treat as cancel for safety.
            state.cancel_delete()
            return
        cur = state.current()
        if cur is NEW_SESSION:
            event.app.exit(result=("new", None))
        else:
            event.app.exit(result=("load", cur))

    @kb.add("n")
    def _(event):
        if state.pending_delete:
            state.cancel_delete()
            return
        event.app.exit(result=("new", None))

    @kb.add("d")
    def _(event):
        if state.pending_delete:
            state.cancel_delete()
            return
        state.request_delete()  # ignored when on [+ New Session]

    @kb.add("Y")
    @kb.add("y")
    def _(event):
        if not state.pending_delete:
            return
        name = state.confirm_delete()
        if name:
            try:
                on_delete(name)
            except Exception:
                # The on-disk delete shouldn't crash the picker — the
                # in-memory list still drops the entry so the user
                # sees progress; they can retry on the next launch.
                pass
        # If we just deleted the last session AND the user hadn't
        # explicitly navigated to [+ New Session], exit straight to
        # the new-session flow (the only remaining option).
        if not state.sessions:
            event.app.exit(result=("new", None))

    @kb.add("escape")
    def _(event):
        if state.pending_delete:
            state.cancel_delete()
            return
        # Esc at the top-level closes the picker → quit.
        event.app.exit(result=("quit", None))

    @kb.add("q")
    def _(event):
        if state.pending_delete:
            state.cancel_delete()
            return
        event.app.exit(result=("quit", None))

    @kb.add("c-c")
    def _(event):
        event.app.exit(result=("quit", None))

    body = Window(
        FormattedTextControl(_render, focusable=True, show_cursor=False),
        always_hide_cursor=True,
    )
    title = Window(
        FormattedTextControl(
            [("class:title bold", "Sessions"), ("", "\n")],
            focusable=False,
            show_cursor=False,
        ),
        height=1,
    )
    layout = Layout(HSplit([title, body]))

    from prompt_toolkit.styles import Style

    style = Style.from_dict(
        {
            "title": "ansicyan",
            "current": "ansigreen",
            "new": "ansiyellow",
            "warn": "ansired",
            "hint": "ansiblack bold",
        }
    )

    # full_screen=True opens the alternate-screen buffer (`\x1b[?1049h`)
    # so each frame is redrawn from a clean slate. Without this,
    # prompt-toolkit's in-place render leaves "ghost" rows when the
    # list shrinks (e.g. after a delete) and stale text bleeds into
    # the footer when the hint line replaces the pending-delete prompt.
    # On exit the buffer is restored and the terminal's prior content
    # reappears unchanged.
    app: Application = Application(
        layout=layout,
        key_bindings=kb,
        full_screen=True,
        style=style,
        mouse_support=False,
    )
    result = app.run()
    # Defensive: if some path slipped through without setting result.
    return result if isinstance(result, tuple) else ("quit", None)


__all__ = [
    "NEW_SESSION",
    "SessionPickerState",
    "run_interactive_picker",
]
