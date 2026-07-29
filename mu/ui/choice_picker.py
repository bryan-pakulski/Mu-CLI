"""Reusable full-screen arrow-key picker for terminal choices.

The startup flow uses this instead of numbered prompts so provider, model,
session-boundary, container, and management selections share the same
interaction model as the original session picker.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True)
class ChoiceItem:
    value: str
    label: str
    description: str = ""


ChoiceSpec = str | tuple[str, str] | tuple[str, str, str] | ChoiceItem


def normalize_choices(options: Iterable[ChoiceSpec]) -> list[ChoiceItem]:
    items: list[ChoiceItem] = []
    seen: set[str] = set()
    for option in options:
        if isinstance(option, ChoiceItem):
            item = option
        elif isinstance(option, str):
            item = ChoiceItem(option, option)
        elif len(option) == 2:
            item = ChoiceItem(str(option[0]), str(option[1]))
        elif len(option) == 3:
            item = ChoiceItem(str(option[0]), str(option[1]), str(option[2]))
        else:  # pragma: no cover - defensive for malformed downstream callers
            raise ValueError(f"invalid choice specification: {option!r}")
        if not item.value:
            raise ValueError("choice values must not be empty")
        if item.value in seen:
            raise ValueError(f"duplicate choice value: {item.value}")
        seen.add(item.value)
        items.append(item)
    if not items:
        raise ValueError("at least one choice is required")
    return items


@dataclass
class ChoicePickerState:
    items: list[ChoiceItem]
    cursor: int = 0

    @classmethod
    def create(
        cls,
        options: Iterable[ChoiceSpec],
        *,
        default: str | None = None,
    ) -> "ChoicePickerState":
        items = normalize_choices(options)
        cursor = 0
        if default is not None:
            for index, item in enumerate(items):
                if item.value == default:
                    cursor = index
                    break
        return cls(items=items, cursor=cursor)

    def move(self, delta: int) -> None:
        self.cursor = (self.cursor + delta) % len(self.items)

    def page(self, delta: int) -> None:
        self.cursor = max(0, min(len(self.items) - 1, self.cursor + delta))

    def first(self) -> None:
        self.cursor = 0

    def last(self) -> None:
        self.cursor = len(self.items) - 1

    def current(self) -> ChoiceItem:
        self.cursor = max(0, min(self.cursor, len(self.items) - 1))
        return self.items[self.cursor]


def run_choice_picker(
    title: str,
    options: Sequence[ChoiceSpec],
    *,
    default: str | None = None,
    subtitle: str = "",
) -> str:
    """Render a full-screen, arrow-key choice picker and return its value."""
    from prompt_toolkit.application import Application
    from prompt_toolkit.data_structures import Point
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import HSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.styles import Style

    state = ChoicePickerState.create(options, default=default)
    kb = KeyBindings()

    def _render() -> FormattedText:
        rows: list[tuple[str, str]] = []
        for index, item in enumerate(state.items):
            current = index == state.cursor
            marker = "❯ " if current else "  "
            style = "class:current bold" if current else "class:item"
            rows.append((style, f"{marker}{item.label}"))
            if item.description:
                rows.append(("class:description", f"  —  {item.description}"))
            rows.append(("", "\n"))
        return FormattedText(rows)

    @kb.add("up")
    @kb.add("k")
    def _move_up(event):
        state.move(-1)

    @kb.add("down")
    @kb.add("j")
    def _move_down(event):
        state.move(1)

    @kb.add("pageup")
    def _page_up(event):
        state.page(-8)

    @kb.add("pagedown")
    def _page_down(event):
        state.page(8)

    @kb.add("home")
    def _home(event):
        state.first()

    @kb.add("end")
    def _end(event):
        state.last()

    @kb.add("enter")
    def _accept(event):
        event.app.exit(result=state.current().value)

    def _back_value() -> str | None:
        values = {item.value for item in state.items}
        for candidate in ("__back__", "back", "quit", "no"):
            if candidate in values:
                return candidate
        return None

    @kb.add("escape")
    @kb.add("q")
    def _back(event):
        value = _back_value()
        if value is not None:
            event.app.exit(result=value)

    @kb.add("c-c")
    def _interrupt(event):
        event.app.exit(exception=KeyboardInterrupt())

    title_rows: list[tuple[str, str]] = [("class:title bold", title), ("", "\n")]
    if subtitle:
        title_rows.extend([("class:subtitle", subtitle), ("", "\n")])

    header = Window(
        FormattedTextControl(FormattedText(title_rows)),
        height=2 if subtitle else 1,
        dont_extend_height=True,
    )
    body = Window(
        FormattedTextControl(
            _render,
            focusable=True,
            show_cursor=False,
            get_cursor_position=lambda: Point(x=0, y=state.cursor),
        ),
        always_hide_cursor=True,
        allow_scroll_beyond_bottom=False,
    )
    footer = Window(
        FormattedTextControl(
            FormattedText(
                [
                    (
                        "class:hint",
                        "↑/↓ navigate · Enter select · q/Esc back · "
                        "Home/End jump · PgUp/PgDn scroll",
                    ),
                    ("", "\n"),
                ]
            )
        ),
        height=1,
        dont_extend_height=True,
    )
    app: Application = Application(
        layout=Layout(HSplit([header, body, footer]), focused_element=body),
        key_bindings=kb,
        full_screen=True,
        mouse_support=False,
        style=Style.from_dict(
            {
                "title": "ansicyan",
                "subtitle": "ansiblack bold",
                "current": "ansigreen",
                "item": "",
                "description": "ansiblack bold",
                "hint": "ansiblack bold",
            }
        ),
    )
    result = app.run()
    return str(result)


def prompt_choice(
    title: str,
    options: Sequence[ChoiceSpec],
    *,
    default: str | None = None,
    subtitle: str = "",
) -> str:
    """Run the interactive picker, with a text-choice fallback for non-TTY use."""
    items = normalize_choices(options)
    try:
        return run_choice_picker(title, items, default=default, subtitle=subtitle)
    except (ImportError, EOFError, OSError):
        from rich.prompt import Prompt

        values = [item.value for item in items]
        fallback_default = default if default in values else values[0]
        return str(Prompt.ask(title, choices=values, default=fallback_default))


def prompt_confirm(title: str, *, default: bool = False) -> bool:
    default_value = "yes" if default else "no"
    return (
        prompt_choice(
            title,
            [
                ("yes", "Yes", "Continue with this action"),
                ("no", "No", "Return without making the change"),
            ],
            default=default_value,
        )
        == "yes"
    )


__all__ = [
    "ChoiceItem",
    "ChoicePickerState",
    "normalize_choices",
    "prompt_choice",
    "prompt_confirm",
    "run_choice_picker",
]
