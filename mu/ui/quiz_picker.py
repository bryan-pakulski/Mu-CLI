"""Interactive teacher-mode quiz UI.

Two layers (same shape as `mu/ui/session_picker.py`):

  * `QuizQuestion` / `QuizPickerState` — pure state machine. No I/O.
    Cursor across questions + options, submission map, reveal flag.
  * `run_interactive_quiz(...)` — prompt-toolkit `Application` shell
    that wires keys to state transitions and renders the live quiz.
    Falls back gracefully (caller catches) when prompt-toolkit can't
    drive the current TTY (CI, dumb terminal, redirected stdin).

Key bindings:
    ↑ / ↓ / k / j     — move option cursor (multiple-choice only)
    Enter             — submit current question's answer; reveal correctness
    → / l / space     — advance to next question (only after submission)
    ← / h             — review previous question (read-only — can't change answer)
    q / Esc / Ctrl-C  — exit early; whatever was submitted gets returned
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Tuple


# ---------------------------------------------------------- data shapes


@dataclass
class QuizQuestion:
    qid: str
    prompt: str
    kind: str  # "multiple_choice" | "fill_blank"
    options: list[str] = field(default_factory=list)
    correct_index: int | None = None
    expected_pattern: str | None = None
    case_sensitive: bool = False
    explanation: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "QuizQuestion":
        return cls(
            qid=str(raw.get("qid", "")),
            prompt=str(raw.get("prompt", "")),
            kind=str(raw.get("kind", "multiple_choice")),
            options=list(raw.get("options") or []),
            correct_index=(
                int(raw["correct_index"])
                if raw.get("correct_index") is not None
                else None
            ),
            expected_pattern=raw.get("expected_pattern"),
            case_sensitive=bool(raw.get("case_sensitive", False)),
            explanation=str(raw.get("explanation", "")),
        )


@dataclass
class QuizPickerState:
    """Pure state machine. Every key handler in the UI shell calls one of
    these methods — keeps the imperative TTY code thin."""

    questions: list[QuizQuestion]
    cursor_question: int = 0
    cursor_option: int = 0
    submissions: dict[str, str] = field(default_factory=dict)
    reveal: dict[str, bool] = field(default_factory=dict)
    text_buffer: str = ""  # for fill_blank

    @property
    def current(self) -> Optional[QuizQuestion]:
        if not self.questions:
            return None
        self.cursor_question = max(0, min(self.cursor_question, len(self.questions) - 1))
        return self.questions[self.cursor_question]

    def move_option(self, delta: int) -> None:
        q = self.current
        if q is None or q.kind != "multiple_choice" or not q.options:
            return
        if self.reveal.get(q.qid):
            return  # locked after reveal
        self.cursor_option = (self.cursor_option + delta) % len(q.options)

    def append_text(self, ch: str) -> None:
        q = self.current
        if q is None or q.kind != "fill_blank":
            return
        if self.reveal.get(q.qid):
            return
        self.text_buffer += ch

    def backspace_text(self) -> None:
        q = self.current
        if q is None or q.kind != "fill_blank":
            return
        if self.reveal.get(q.qid):
            return
        self.text_buffer = self.text_buffer[:-1]

    def submit_current(self) -> Optional[str]:
        q = self.current
        if q is None or self.reveal.get(q.qid):
            return None
        if q.kind == "multiple_choice":
            if not q.options:
                return None
            answer = q.options[self.cursor_option]
        else:
            answer = self.text_buffer
        self.submissions[q.qid] = answer
        self.reveal[q.qid] = True
        return answer

    def next_question(self) -> bool:
        q = self.current
        if q is None:
            return False
        if not self.reveal.get(q.qid):
            return False
        if self.cursor_question >= len(self.questions) - 1:
            return False
        self.cursor_question += 1
        self.cursor_option = 0
        self._reset_text_buffer_for_current()
        return True

    def prev_question(self) -> bool:
        if self.cursor_question <= 0:
            return False
        self.cursor_question -= 1
        self.cursor_option = 0
        self._reset_text_buffer_for_current()
        return True

    def _reset_text_buffer_for_current(self) -> None:
        q = self.current
        if q is not None and q.kind == "fill_blank":
            self.text_buffer = self.submissions.get(q.qid, "")
        else:
            self.text_buffer = ""

    def is_complete(self) -> bool:
        return all(self.reveal.get(q.qid) for q in self.questions)

    def correct_so_far(self) -> Tuple[int, int]:
        right = 0
        total = 0
        for q in self.questions:
            if not self.reveal.get(q.qid):
                continue
            total += 1
            answer = self.submissions.get(q.qid, "")
            if _is_correct(q, answer):
                right += 1
        return right, total


def _is_correct(question: QuizQuestion, answer: str) -> bool:
    if question.kind == "multiple_choice":
        if question.correct_index is None or not question.options:
            return False
        idx = question.correct_index
        if not (0 <= idx < len(question.options)):
            return False
        return answer == question.options[idx]
    # fill_blank
    expected = question.expected_pattern or ""
    if not expected:
        return False
    if question.case_sensitive:
        return expected in answer or expected == answer
    return expected.casefold() in answer.casefold() or expected.casefold() == answer.casefold()


# ---------------------------------------------------------- prompt-toolkit shell


def run_interactive_quiz(
    questions: list[QuizQuestion] | list[dict],
    *,
    title: str = "Quiz",
) -> dict[str, str]:
    """Run the live quiz Application.

    Returns `{qid: submitted_value}`. Quitting early returns whatever was
    submitted so far (questions never reached are simply absent).
    """
    # Accept dicts for convenience — handler hands us the raw question payloads.
    normalized: list[QuizQuestion] = [
        q if isinstance(q, QuizQuestion) else QuizQuestion.from_dict(q) for q in questions
    ]
    if not normalized:
        return {}

    from prompt_toolkit.application import Application
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import HSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.styles import Style

    state = QuizPickerState(questions=list(normalized))
    kb = KeyBindings()

    def _render() -> FormattedText:
        lines: list = []
        total = len(state.questions)
        idx = state.cursor_question + 1
        right, answered = state.correct_so_far()
        progress = "".join(
            "●" if state.reveal.get(q.qid) else "○" for q in state.questions
        )
        lines.append(("class:title bold", f"  {title}\n"))
        lines.append(
            ("class:hint", f"  Question {idx} / {total}   {progress}   "
                          f"({right} correct of {answered})\n\n")
        )
        q = state.current
        if q is None:
            lines.append(("", "  (no questions)\n"))
            return FormattedText(lines)
        lines.append(("class:prompt bold", f"  {q.prompt}\n\n"))
        if q.kind == "multiple_choice":
            for i, option in enumerate(q.options):
                cursor = "❯ " if i == state.cursor_option else "  "
                style = "class:current bold" if i == state.cursor_option else ""
                marker = ""
                if state.reveal.get(q.qid):
                    submitted = state.submissions.get(q.qid)
                    if submitted == option and _is_correct(q, submitted):
                        style = "class:correct bold"
                        marker = "  ✓"
                    elif submitted == option:
                        style = "class:wrong bold"
                        marker = "  ✗"
                    elif i == q.correct_index:
                        style = "class:correct"
                        marker = "  (correct)"
                lines.append((style, f"  {cursor}{option}{marker}\n"))
        else:
            current = state.text_buffer if not state.reveal.get(q.qid) else state.submissions.get(q.qid, "")
            border = "│" if not state.reveal.get(q.qid) else " "
            lines.append(("class:input", f"  {border} {current}\n"))
            if state.reveal.get(q.qid):
                if _is_correct(q, current):
                    lines.append(("class:correct bold", f"  ✓ Correct\n"))
                else:
                    lines.append(("class:wrong bold", f"  ✗ Incorrect\n"))
                    if q.expected_pattern:
                        lines.append(
                            ("class:hint", f"    Expected pattern: {q.expected_pattern}\n")
                        )

        if state.reveal.get(q.qid) and q.explanation:
            lines.append(("", "\n"))
            lines.append(("class:explain", f"  {q.explanation}\n"))

        lines.append(("", "\n"))
        if state.reveal.get(q.qid):
            footer = "→ next  ·  ← back  ·  q quit"
        else:
            if q.kind == "multiple_choice":
                footer = "↑/↓ choose  ·  Enter submit  ·  ← back  ·  q quit"
            else:
                footer = "type your answer  ·  Enter submit  ·  ← back  ·  q quit"
        lines.append(("class:hint", f"  {footer}\n"))
        return FormattedText(lines)

    @kb.add("up")
    @kb.add("k")
    def _(event):
        state.move_option(-1)

    @kb.add("down")
    @kb.add("j")
    def _(event):
        state.move_option(1)

    @kb.add("enter")
    def _(event):
        q = state.current
        if q is None:
            return
        if state.reveal.get(q.qid):
            advanced = state.next_question()
            if not advanced and state.is_complete():
                event.app.exit(result=dict(state.submissions))
            return
        state.submit_current()
        if state.is_complete() and state.cursor_question == len(state.questions) - 1:
            # Allow the user to see the final reveal before exiting.
            pass

    @kb.add("right")
    @kb.add("l")
    @kb.add(" ")
    def _(event):
        if state.next_question():
            return
        if state.is_complete():
            event.app.exit(result=dict(state.submissions))

    @kb.add("left")
    @kb.add("h")
    def _(event):
        state.prev_question()

    @kb.add("backspace")
    def _(event):
        state.backspace_text()

    @kb.add("escape")
    @kb.add("q")
    @kb.add("c-c")
    def _(event):
        event.app.exit(result=dict(state.submissions))

    # For fill_blank questions, capture printable keys into the buffer.
    @kb.add("<any>")
    def _(event):
        q = state.current
        if q is None or q.kind != "fill_blank":
            return
        for key in event.key_sequence:
            data = getattr(key, "data", "") or ""
            if len(data) == 1 and (data.isprintable()):
                state.append_text(data)

    body = Window(
        FormattedTextControl(_render, focusable=True, show_cursor=False),
        always_hide_cursor=True,
    )
    layout = Layout(HSplit([body]))

    style = Style.from_dict(
        {
            "title": "ansicyan",
            "prompt": "ansiwhite",
            "current": "ansiyellow",
            "correct": "ansigreen",
            "wrong": "ansired",
            "input": "ansiwhite",
            "explain": "ansiblack bold",
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
    return result if isinstance(result, dict) else dict(state.submissions)


__all__ = [
    "QuizPickerState",
    "QuizQuestion",
    "run_interactive_quiz",
]
