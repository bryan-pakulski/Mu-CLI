"""Pure state-machine tests for the live quiz UI.

These never instantiate the prompt-toolkit Application — they exercise
QuizPickerState directly. The Application shell is a thin wrapper that
simply dispatches key events into these same transitions.
"""

from __future__ import annotations

import pytest

from mu.ui.quiz_picker import QuizPickerState, QuizQuestion, _is_correct


@pytest.fixture
def quiz():
    return [
        QuizQuestion(
            qid="q1",
            prompt="2+2?",
            kind="multiple_choice",
            options=["3", "4", "5"],
            correct_index=1,
            explanation="basic arithmetic",
        ),
        QuizQuestion(
            qid="q2",
            prompt="Capital of France?",
            kind="fill_blank",
            expected_pattern="Paris",
        ),
    ]


def test_move_option_wraps(quiz):
    s = QuizPickerState(questions=quiz)
    s.move_option(1)
    assert s.cursor_option == 1
    s.move_option(1)
    assert s.cursor_option == 2
    s.move_option(1)  # wraps
    assert s.cursor_option == 0
    s.move_option(-1)  # wraps backwards
    assert s.cursor_option == 2


def test_submit_multiple_choice_records_choice_and_reveals(quiz):
    s = QuizPickerState(questions=quiz)
    s.move_option(1)
    answer = s.submit_current()
    assert answer == "4"
    assert s.reveal["q1"] is True
    assert s.submissions["q1"] == "4"
    # Locked after reveal.
    s.move_option(1)
    assert s.cursor_option == 1, "cursor should not move once revealed"


def test_submit_locks_text_buffer(quiz):
    s = QuizPickerState(questions=quiz)
    s.cursor_question = 1
    for ch in "Paris":
        s.append_text(ch)
    answer = s.submit_current()
    assert answer == "Paris"
    s.append_text("X")
    assert s.submissions["q2"] == "Paris"
    assert s.text_buffer == "Paris"  # buffer didn't change after reveal


def test_correct_so_far_counts_only_revealed(quiz):
    s = QuizPickerState(questions=quiz)
    s.move_option(1)
    s.submit_current()
    right, total = s.correct_so_far()
    assert (right, total) == (1, 1)
    s.next_question()
    s.append_text("Berlin")
    s.submit_current()
    right, total = s.correct_so_far()
    assert (right, total) == (1, 2)  # only q1 was right


def test_next_question_only_after_submit(quiz):
    s = QuizPickerState(questions=quiz)
    assert not s.next_question()
    s.move_option(1)
    s.submit_current()
    assert s.next_question()
    assert s.cursor_question == 1


def test_is_complete_true_only_after_all_submitted(quiz):
    s = QuizPickerState(questions=quiz)
    assert not s.is_complete()
    s.submit_current()  # submits cursor_option=0 = "3"
    assert not s.is_complete()
    s.next_question()
    s.append_text("Paris")
    s.submit_current()
    assert s.is_complete()


def test_prev_question_restores_text_buffer(quiz):
    s = QuizPickerState(questions=quiz)
    s.submit_current()  # q1
    s.next_question()
    s.append_text("Paris")
    s.submit_current()  # q2
    s.prev_question()
    assert s.cursor_question == 0
    s.next_question()
    assert s.cursor_question == 1
    assert s.text_buffer == "Paris"


def test_is_correct_fill_blank_case_insensitive_substring():
    q = QuizQuestion(qid="x", prompt="?", kind="fill_blank", expected_pattern="Paris")
    assert _is_correct(q, "Paris")
    assert _is_correct(q, "paris")
    assert _is_correct(q, "  Paris ")
    assert not _is_correct(q, "Berlin")


def test_is_correct_multiple_choice():
    q = QuizQuestion(
        qid="x", prompt="?", kind="multiple_choice", options=["a", "b"], correct_index=1
    )
    assert _is_correct(q, "b")
    assert not _is_correct(q, "a")


def test_from_dict_round_trips_a_question_payload():
    raw = {
        "qid": "q1",
        "prompt": "Pick one",
        "kind": "multiple_choice",
        "options": ["x", "y"],
        "correct_index": 0,
        "explanation": "duh",
    }
    q = QuizQuestion.from_dict(raw)
    assert q.qid == "q1" and q.correct_index == 0 and q.options == ["x", "y"]
