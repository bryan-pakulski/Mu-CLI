"""Tests for the teacher-mode conversation watcher.

The watcher classifies chat messages into engine events (explanation,
check, learner_response, …) so the agent doesn't have to call
record_lecture_turn-style tools. These tests stub the classifier's
provider call with a fake provider returning fixed JSON, so we can
exercise the apply_* paths deterministically.
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

import mu.teacher.engine as engine
from mu.teacher.engine import (
    LESSON_LECTURING,
    LESSON_PRESENTING,
    Course,
    Lesson,
    Module,
    create_course,
    find_lesson,
    save_course,
    start_lecture,
)
from mu.teacher.watcher import (
    WatcherResult,
    apply_assistant_classification,
    apply_learner_classification,
    classify_assistant_message,
    classify_user_message,
    is_watcher_eligible,
)
from providers.base import ProviderResponse


# ----- fixtures ---------------------------------------------------------------


class _FakeProvider:
    """Minimal provider stub: returns the queued JSON for every call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def generate(self, messages=None, system_prompt=None, **kwargs):
        self.calls.append({"system": system_prompt, "messages": messages})
        text = self._responses.pop(0) if self._responses else "{}"
        return ProviderResponse(
            text=text,
            parts=[],
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
        )


@pytest.fixture
def fake_course(tmp_path, monkeypatch):
    """Course with one module and one lesson, lesson in PRESENTING state."""
    monkeypatch.setattr(
        engine._storage,
        "ensure_course_directory",
        lambda slug, fc=None: str(tmp_path / "course"),
    )
    monkeypatch.setattr(
        engine._storage,
        "course_state_path",
        lambda cid, fc=None: str(tmp_path / "course" / "course.json"),
    )
    monkeypatch.setattr(engine._storage, "slugify", lambda s: str(s).lower().replace(" ", "-"))
    os.makedirs(tmp_path / "course", exist_ok=True)

    course = create_course(subject="Kalman filters")
    module = Module(module_id="m1", title="Intro")
    lesson = Lesson(
        lesson_id="m1l1",
        module_id="m1",
        title="What is a state?",
        learning_objectives=["Explain state vectors", "Identify state in tracking"],
        status=LESSON_PRESENTING,
    )
    module.lesson_ids.append(lesson.lesson_id)
    course.modules.append(module)
    course.lessons.append(lesson)
    course.current_lesson_id = lesson.lesson_id
    return course


# ----- assistant-message classification ---------------------------------------


def test_assistant_explanation_auto_starts_lecture(fake_course):
    """The watcher must auto-fire start_lecture when the first
    substantive explanation lands in a lesson that's still in
    `presenting` status."""
    explanation = (
        "A state vector packs everything we need to predict the next "
        "step into one column of numbers — position, velocity, the "
        "swimmer's heading. Without it the filter has nothing to update."
    )
    classification = {
        "narration_only": False,
        "wrap_up": False,
        "explanation": explanation,
        "check": "What single quantity could you NOT recover from position alone?",
    }
    result = apply_assistant_classification(fake_course, "m1l1", classification)

    lesson = find_lesson(fake_course, "m1l1")
    assert lesson.status == LESSON_LECTURING
    assert result.auto_started_lecture is True
    assert any(turn.role == "agent_explanation" for turn in lesson.lecture_turns)
    assert any(turn.role == "agent_check" for turn in lesson.lecture_turns)
    assert result.dropped == []


def test_assistant_short_explanation_is_dropped(fake_course):
    """Explanations shorter than MIN_EXPLANATION_CHARS_BEFORE_CHECK
    don't get recorded — they'd let the agent place a placeholder
    string in the transcript and skip the actual teaching."""
    start_lecture(fake_course, "m1l1")
    classification = {
        "narration_only": False,
        "wrap_up": False,
        "explanation": "covered intro",  # ≪ 80 chars
        "check": None,
    }
    result = apply_assistant_classification(fake_course, "m1l1", classification)
    lesson = find_lesson(fake_course, "m1l1")

    assert all(turn.role != "agent_explanation" for turn in lesson.lecture_turns)
    assert any(item["reason"] == "too_short" for item in result.dropped)


def test_inline_multiple_choice_surfaces_feedback(fake_course):
    """An assistant message that writes A)/B)/C) options inline must
    trigger the 'use ask_user_choice instead' feedback. Inline MC text
    bypasses the interactive TUI; the watcher catches it."""
    start_lecture(fake_course, "m1l1")
    explanation = (
        "When you skip frames, the filter's uncertainty grows because it "
        "keeps extrapolating from the last detection without any new "
        "evidence to pin the estimate down."
    )
    inline_check = (
        "What happens to variance after 10 missed frames?\n"
        "A) stays the same\n"
        "B) shrinks\n"
        "C) grows\n"
        "D) hits zero"
    )
    classification = {
        "narration_only": False,
        "wrap_up": False,
        "explanation": explanation,
        "check": inline_check,
    }
    result = apply_assistant_classification(fake_course, "m1l1", classification)

    assert "ask_user_choice" in result.agent_feedback
    # The explanation + check are still recorded so the lecture doesn't
    # silently drop the turn — but the agent is told to redo the check
    # through the picker.
    lesson = find_lesson(fake_course, "m1l1")
    assert any(turn.role == "agent_check" for turn in lesson.lecture_turns)


def test_assistant_narration_only_surfaces_feedback(fake_course):
    """Pure meta-talk ('let me record that') with no teaching content
    triggers a watcher feedback note so the agent learns the engine
    is watching the chat, not its tool narration."""
    start_lecture(fake_course, "m1l1")
    classification = {
        "narration_only": True,
        "wrap_up": False,
        "explanation": None,
        "check": None,
    }
    result = apply_assistant_classification(fake_course, "m1l1", classification)

    assert result.events_applied == []
    assert "watcher" in result.agent_feedback.lower()


def test_wrap_up_with_passing_comprehension_auto_concludes(fake_course):
    """When the assistant signals wrap-up and recorded learner responses
    average above the lesson threshold, the watcher fires
    conclude_lecture so the lesson advances without the agent having
    to call it."""
    start_lecture(fake_course, "m1l1")
    lesson = find_lesson(fake_course, "m1l1")
    # Seed two passing learner responses + the required agent_check pairs.
    engine.record_lecture_turn(
        fake_course,
        "m1l1",
        role="agent_explanation",
        content="x" * 100,
    )
    engine.record_lecture_turn(
        fake_course,
        "m1l1",
        role="agent_check",
        content="check 1",
    )
    engine.record_lecture_turn(
        fake_course,
        "m1l1",
        role="learner_response",
        content="answer 1",
        comprehension_signal="on track",
    )
    engine.record_lecture_turn(
        fake_course,
        "m1l1",
        role="agent_explanation",
        content="y" * 100,
    )
    engine.record_lecture_turn(
        fake_course,
        "m1l1",
        role="agent_check",
        content="check 2",
    )
    engine.record_lecture_turn(
        fake_course,
        "m1l1",
        role="learner_response",
        content="answer 2",
        comprehension_signal="on track",
    )

    classification = {
        "narration_only": False,
        "wrap_up": True,
        "explanation": None,
        "check": None,
    }
    result = apply_assistant_classification(fake_course, "m1l1", classification)

    lesson = find_lesson(fake_course, "m1l1")
    assert result.auto_concluded_lecture is True
    assert lesson.lecture_concluded is True
    assert lesson.lecture_comprehension_pct is not None


def test_wrap_up_without_comprehension_signals_blocks(fake_course):
    """Wrap-up signal with no recorded learner responses surfaces
    feedback rather than fabricating a comprehension score."""
    start_lecture(fake_course, "m1l1")
    classification = {
        "narration_only": False,
        "wrap_up": True,
        "explanation": None,
        "check": None,
    }
    result = apply_assistant_classification(fake_course, "m1l1", classification)
    lesson = find_lesson(fake_course, "m1l1")

    assert result.auto_concluded_lecture is False
    assert lesson.lecture_concluded is False
    assert "no learner responses" in result.agent_feedback.lower()


# ----- learner-message classification -----------------------------------------


def test_learner_response_records_with_signal(fake_course):
    """A classified learner reply with comprehension_signal becomes a
    learner_response in the lesson transcript."""
    start_lecture(fake_course, "m1l1")
    # Need a prior explanation+check for record_lecture_turn ordering to be sensible
    engine.record_lecture_turn(
        fake_course,
        "m1l1",
        role="agent_explanation",
        content="z" * 100,
    )
    engine.record_lecture_turn(
        fake_course,
        "m1l1",
        role="agent_check",
        content="any question",
    )

    classification = {
        "kind": "response",
        "comprehension_signal": "partial",
        "content": "I think the state is just position, right?",
    }
    result = apply_learner_classification(fake_course, "m1l1", classification)
    lesson = find_lesson(fake_course, "m1l1")

    responses = [t for t in lesson.lecture_turns if t.role == "learner_response"]
    assert len(responses) == 1
    assert responses[0].comprehension_signal == "partial"
    assert "position" in responses[0].content
    assert result.events_applied


def test_learner_acknowledgement_is_skipped(fake_course):
    """Low-content acks ('ok', 'got it') don't pollute the transcript."""
    start_lecture(fake_course, "m1l1")
    classification = {
        "kind": "acknowledgement",
        "comprehension_signal": None,
        "content": "ok",
    }
    result = apply_learner_classification(fake_course, "m1l1", classification)
    lesson = find_lesson(fake_course, "m1l1")

    assert all(turn.role != "learner_response" for turn in lesson.lecture_turns)
    assert result.events_applied == []


# ----- classifier invocation (mocked provider) --------------------------------


def test_classify_assistant_message_parses_json_in_fences():
    """The classifier accepts JSON wrapped in code fences (some models
    add them despite the system prompt asking for raw JSON)."""
    fenced = (
        "```json\n"
        '{"narration_only": false, "wrap_up": false, '
        '"explanation": "abc", "check": null}\n'
        "```"
    )
    provider = _FakeProvider([fenced])
    out = classify_assistant_message(provider, "the assistant's chat content")
    assert out == {
        "narration_only": False,
        "wrap_up": False,
        "explanation": "abc",
        "check": None,
    }


def test_classify_assistant_message_returns_none_on_garbage():
    """If the model returns junk, the classifier returns None and the
    caller treats it as a no-op (never crashes the turn)."""
    provider = _FakeProvider(["I think it was an explanation, idk"])
    out = classify_assistant_message(provider, "something")
    assert out is None


def test_classify_user_message_picks_first_json_block():
    """A model that prefixes prose still parses — the watcher hunts
    for the first { ... } block."""
    prefixed = (
        "Sure! Here's the classification:\n"
        '{"kind": "response", "comprehension_signal": "on track", '
        '"content": "yes"}'
    )
    provider = _FakeProvider([prefixed])
    out = classify_user_message(provider, "yes I think so")
    assert out == {
        "kind": "response",
        "comprehension_signal": "on track",
        "content": "yes",
    }


# ----- eligibility ------------------------------------------------------------


def test_is_watcher_eligible_only_during_active_lecture(fake_course):
    assert is_watcher_eligible(fake_course, "m1l1") is True  # presenting → eligible
    start_lecture(fake_course, "m1l1")
    assert is_watcher_eligible(fake_course, "m1l1") is True  # lecturing → eligible

    # Force lesson into a non-eligible status.
    lesson = find_lesson(fake_course, "m1l1")
    lesson.status = "graded"
    assert is_watcher_eligible(fake_course, "m1l1") is False

    assert is_watcher_eligible(fake_course, None) is False
    assert is_watcher_eligible(fake_course, "does-not-exist") is False
