"""Interactive choice prompt — the agent asks the user, the user picks.

Two layers (same shape as `mu/ui/session_picker.py` and
`mu/ui/quiz_picker.py`):

  * `ChoicePromptState` — pure state machine. Cursor across options,
    selected set (multi-select mode), text-entry mode flag.
  * `run_interactive_choice_prompt(...)` — prompt-toolkit `Application`
    shell with two stacked panes: the options list, and a conditional
    text-input row that appears when the user picks the "Other"
    entry. Focus shifts between panes without ever leaving the
    Application; the panel never disappears mid-interaction.

When `allow_other=True` the picker appends a synthetic
"✎ Other (type your own)…" entry. Picking it (single-select) or
submitting with Other toggled (multi-select) reveals an inline text
row inside the SAME panel — the user types their answer right there
and presses Enter to finalize. Esc backs out of the text row to the
picker without losing the picker state; Ctrl-C cancels the whole
prompt.

Single-select bindings:
    ↑ / ↓ / k / j     — move cursor
    Enter             — submit highlighted option (opens text row if Other)
    q / Esc / Ctrl-C  — cancel

Multi-select bindings:
    ↑ / ↓ / k / j     — move cursor
    Space             — toggle highlighted option
    a                 — select all
    n                 — clear selection
    Enter             — submit toggled set (opens text row if Other toggled)
    q / Esc / Ctrl-C  — cancel

Text-entry mode (after picking Other):
    Type freely       — character input goes to the inline buffer
    Enter             — submit selection + typed text
    Esc               — return to the picker (keeps picker state)
    Ctrl-C            — cancel the whole prompt
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChoicePromptState:
    options: list[str]
    multi_select: bool = False
    cursor: int = 0
    selected: set[int] = field(default_factory=set)
    cancelled: bool = False
    submitted: bool = False
    entering_other: bool = False
    other_text: str = ""

    def move(self, delta: int) -> None:
        if not self.options:
            return
        self.cursor = (self.cursor + delta) % len(self.options)

    def toggle_current(self) -> None:
        if not self.multi_select or not self.options:
            return
        if self.cursor in self.selected:
            self.selected.discard(self.cursor)
        else:
            self.selected.add(self.cursor)

    def select_all(self) -> None:
        if not self.multi_select:
            return
        self.selected = set(range(len(self.options)))

    def clear_selection(self) -> None:
        if not self.multi_select:
            return
        self.selected.clear()

    def submit(self) -> list[str]:
        """Finalize the picker. For single-select, picks the highlighted
        option; for multi-select, returns every toggled option (in
        original order)."""
        self.submitted = True
        if not self.options:
            return []
        if self.multi_select:
            return [self.options[i] for i in sorted(self.selected)]
        return [self.options[self.cursor]]

    def cancel(self) -> None:
        self.cancelled = True
        self.submitted = True


_OTHER_LABEL = "✎ Other (type your own)…"


def run_interactive_choice_prompt(
    question: str,
    options: list[str],
    *,
    multi_select: bool = False,
    description: str = "",
    allow_other: bool = False,
) -> dict[str, Any]:
    """Run the full-screen choice picker. Blocks until the user submits.

    When `allow_other=True`, a synthetic "Other" entry is appended.
    Picking it slides an inline text-input row into the same panel;
    the user types their answer there and Enter finalizes the combined
    selection without ever leaving the full-screen view.

    Returns `{"selected": [...], "other_text": str, "cancelled": bool}`.
    `selected` contains the labels the user picked from the CANONICAL
    options (the synthetic Other entry is excluded). `other_text` is
    populated only when the user picked Other and typed something.
    """
    if not options:
        return {"selected": [], "other_text": "", "cancelled": True}

    from prompt_toolkit.application import Application
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import (
        ConditionalContainer,
        HSplit,
        Window,
    )
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.styles import Style
    from prompt_toolkit.widgets import TextArea

    canonical_options = list(options)
    display_options = list(canonical_options)
    other_idx: int | None = None
    if allow_other:
        other_idx = len(display_options)
        display_options.append(_OTHER_LABEL)

    state = ChoicePromptState(options=display_options, multi_select=multi_select)
    kb = KeyBindings()

    picker_mode = Condition(lambda: not state.entering_other)
    text_mode = Condition(lambda: state.entering_other)

    # --------------------------------------------------------- rendering

    def _render() -> FormattedText:
        lines: list = []
        lines.append(("class:title bold", f"  {question}\n"))
        if description:
            lines.append(("class:hint", f"  {description}\n"))
        lines.append(("", "\n"))
        for i, option in enumerate(state.options):
            is_cursor = (i == state.cursor) and not state.entering_other
            is_selected = i in state.selected
            is_other = other_idx is not None and i == other_idx
            cursor_marker = "❯ " if is_cursor else "  "
            if state.multi_select:
                box = "[x]" if is_selected else "[ ]"
                label = f"  {cursor_marker}{box} {option}\n"
            else:
                label = f"  {cursor_marker}{option}\n"
            if is_cursor:
                style = "class:current bold"
            elif is_selected:
                style = "class:selected"
            elif is_other:
                style = "class:other"
            else:
                style = ""
            lines.append((style, label))
        lines.append(("", "\n"))
        if state.entering_other:
            lines.append(
                (
                    "class:hint",
                    "  ↓ type your answer below   ·   Enter submit   ·   "
                    "Esc back to options   ·   Ctrl-C cancel\n",
                )
            )
        elif state.multi_select:
            footer = (
                "↑/↓ move  ·  Space toggle  ·  a all  ·  n none  ·  "
                "Enter submit  ·  q cancel"
            )
            count = len(state.selected)
            lines.append(("class:hint", f"  {count} selected   {footer}\n"))
        else:
            lines.append(
                ("class:hint", "  ↑/↓ move  ·  Enter submit  ·  q cancel\n")
            )
        return FormattedText(lines)

    body = Window(
        FormattedTextControl(_render, focusable=True, show_cursor=False),
        always_hide_cursor=True,
    )

    # --------------------------------------------------------- finalize

    def _finalize() -> dict[str, Any]:
        """Compute the wire-format result from the current state.
        Strips the synthetic Other entry from `selected` and records
        whether the user wants a text follow-up."""
        picked = state.submit()
        wants_other = False
        canonical_picks: list[str] = []
        if other_idx is not None and state.multi_select:
            wants_other = other_idx in state.selected
            canonical_picks = [
                state.options[i] for i in sorted(state.selected) if i != other_idx
            ]
        elif other_idx is not None and not state.multi_select:
            wants_other = state.cursor == other_idx
            canonical_picks = [] if wants_other else picked
        else:
            canonical_picks = picked
        return {
            "selected": canonical_picks,
            "cancelled": False,
            "wants_other_text": wants_other,
        }

    # --------------------------------------------------------- text row

    def _on_text_accept(buf) -> bool:
        """Triggered when the user presses Enter inside the text row."""
        state.other_text = (buf.text or "").strip()
        result = _finalize()
        # The text accept fires regardless of whether the user typed
        # anything; we treat blank as "no follow-up" and return what's
        # already selected. The agent gets `other_text=""` either way.
        _exit_with_text(result)
        return False  # don't keep the buffer alive

    other_input = TextArea(
        multiline=False,
        height=1,
        prompt="  Other › ",
        accept_handler=_on_text_accept,
    )

    text_row = ConditionalContainer(
        content=other_input,
        filter=text_mode,
    )

    # --------------------------------------------------------- bindings

    @kb.add("up", filter=picker_mode)
    @kb.add("k", filter=picker_mode)
    def _(event):
        state.move(-1)

    @kb.add("down", filter=picker_mode)
    @kb.add("j", filter=picker_mode)
    def _(event):
        state.move(1)

    @kb.add("space", filter=picker_mode)
    def _(event):
        if state.multi_select:
            state.toggle_current()
        else:
            _submit_picker(event)

    @kb.add("a", filter=picker_mode)
    def _(event):
        state.select_all()

    @kb.add("n", filter=picker_mode)
    def _(event):
        if state.multi_select:
            state.clear_selection()

    @kb.add("enter", filter=picker_mode)
    def _(event):
        _submit_picker(event)

    def _submit_picker(event) -> None:
        """Common handler for Enter / Space (single-select) in picker mode.
        If the submission involves Other, swap into text-entry mode
        instead of exiting."""
        result = _finalize()
        if result["wants_other_text"]:
            state.entering_other = True
            other_input.buffer.text = ""
            event.app.layout.focus(other_input)
            return
        event.app.exit(result=result)

    @kb.add("escape", filter=picker_mode)
    @kb.add("q", filter=picker_mode)
    def _(event):
        state.cancel()
        event.app.exit(
            result={
                "selected": [],
                "cancelled": True,
                "wants_other_text": False,
            }
        )

    @kb.add("c-c")
    def _(event):
        state.cancel()
        event.app.exit(
            result={
                "selected": [],
                "cancelled": True,
                "wants_other_text": False,
            }
        )

    @kb.add("escape", filter=text_mode)
    def _(event):
        """Back out of text-entry into the picker — keep picker state
        intact so the user can re-pick or toggle Other off."""
        state.entering_other = False
        other_input.buffer.text = ""
        state.other_text = ""
        # If we got here via single-select picking Other, move the cursor
        # off Other so a fresh Enter doesn't re-open text entry. If we
        # got here via multi-select with Other toggled, un-toggle it.
        if other_idx is not None:
            if state.multi_select:
                state.selected.discard(other_idx)
            elif state.cursor == other_idx:
                state.cursor = max(0, other_idx - 1)
        event.app.layout.focus(body)

    def _exit_with_text(result: dict[str, Any]) -> None:
        """Exit the Application with the combined picker + text result."""
        # Application is grabbed from the live invocation when the
        # accept_handler fires. We capture it lazily so the closure
        # doesn't have to track an Application reference up front.
        import prompt_toolkit.application.current as _current

        app = _current.get_app()
        app.exit(result=result)

    # --------------------------------------------------------- layout

    layout = Layout(
        HSplit([body, text_row]),
        focused_element=body,
    )

    style = Style.from_dict(
        {
            "title": "ansicyan",
            "current": "ansiyellow",
            "selected": "ansigreen",
            "other": "ansiblack bold italic",
            "hint": "ansiblack bold",
        }
    )
    app: Application = Application(
        layout=layout,
        key_bindings=kb,
        full_screen=True,
        style=style,
        mouse_support=False,
    )
    result = app.run()
    if not isinstance(result, dict):
        result = {"selected": [], "cancelled": True, "wants_other_text": False}

    return {
        "selected": list(result.get("selected") or []),
        "cancelled": bool(result.get("cancelled", False)),
        "other_text": state.other_text,
    }


__all__ = [
    "ChoicePromptState",
    "run_interactive_choice_prompt",
]
