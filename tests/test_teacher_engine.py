"""Tests for the teacher-mode engine.

Coverage:
  * Course / Module / Lesson / Assignment lifecycle and JSON round-trip.
  * Lesson status transitions: refuses illegal moves.
  * `decide_next("advance")` refuses unless latest grade passed.
  * `decide_next("remediate")` flips lesson back to remediating, increments
    remediation_count, and allows a new assignment cycle.
  * Socratic dialog: `record_dialog_turn` and `close_dialog`. Refuses close
    without min_turns; refuses close without required_concepts coverage.
  * `course_metrics` reports counts, percentages, and average score.
"""

from __future__ import annotations

import os
import tempfile

import pytest

import mu.teacher.engine as engine
from mu.teacher.engine import (
    ASSIGNMENT_PASSED,
    Assignment,
    COURSE_CURRICULUM_PROPOSED,
    COURSE_IN_PROGRESS,
    DialogTurn,
    Grade,
    LESSON_ASSIGNED,
    LESSON_COMPLETED,
    LESSON_GRADED,
    LESSON_PENDING,
    LESSON_PRESENTING,
    LESSON_REMEDIATING,
    Lesson,
    Module,
    RubricItem,
    VerificationSpec,
    advance_lesson_status,
    close_socratic_dialog,
    course_metrics,
    create_course,
    decide_next,
    find_assignment,
    find_lesson,
    is_valid_lesson_transition,
    load_course,
    next_pending_lesson,
    record_dialog_turn,
    save_course,
)


@pytest.fixture
def isolated_workspace(tmp_path, monkeypatch):
    """Make the temp dir the workspace so courses land under it.

    The storage helpers fall back to cwd when no folder_context is
    attached — chdir into tmp_path so `<workspace>/courses/<id>/` lands
    inside the fixture's isolated tree.
    """
    monkeypatch.chdir(tmp_path)
    yield tmp_path


def _seed_course(folder_context=None, subject="Perl") -> engine.Course:
    course = create_course(subject=subject, folder_context=folder_context)
    course.modules.append(
        Module(module_id="m1", title="Basics", goal="Learn the basics", order=1,
               lesson_ids=["l1"], mastery_threshold=70)
    )
    course.lessons.append(
        Lesson(lesson_id="l1", module_id="m1", title="Hello World",
               learning_objectives=["print"], concept_brief="")
    )
    course.status = COURSE_IN_PROGRESS
    save_course(course)
    return course


def test_create_course_writes_state(isolated_workspace):
    course = create_course(subject="Perl")
    assert course.status == "diagnosing"
    assert os.path.exists(os.path.join(course.directory, "course.json"))
    reloaded = load_course(course.course_id)
    assert reloaded is not None
    assert reloaded.subject == "Perl"


def test_full_round_trip_preserves_nested_structures(isolated_workspace):
    course = _seed_course()
    spec = VerificationSpec(
        method="exec_markers",
        verify_cmd="echo PASS",
        expected_markers=["PASS"],
        timeout_seconds=5,
    )
    a = Assignment(
        assignment_id="a1",
        lesson_id="l1",
        kind="fix-broken-code",
        prompt="Fix it",
        verification=spec,
        rubric=[RubricItem(criterion="correctness", weight=2)],
    )
    a.grade = Grade(score_pct=88, passed=True, feedback="nice")
    course.assignments.append(a)
    course.lessons[0].assignment_ids.append("a1")
    save_course(course)

    reloaded = load_course(course.course_id)
    a2 = find_assignment(reloaded, "a1")
    assert a2 is not None
    assert a2.verification.expected_markers == ["PASS"]
    assert a2.grade is not None and a2.grade.score_pct == 88
    assert a2.rubric[0].criterion == "correctness"


def test_lesson_transition_table_rejects_illegal_moves():
    assert is_valid_lesson_transition(LESSON_PENDING, LESSON_PRESENTING)
    assert is_valid_lesson_transition(LESSON_PRESENTING, LESSON_ASSIGNED)
    assert is_valid_lesson_transition(LESSON_ASSIGNED, LESSON_GRADED)
    assert is_valid_lesson_transition(LESSON_GRADED, LESSON_COMPLETED)
    assert is_valid_lesson_transition(LESSON_GRADED, LESSON_REMEDIATING)
    assert is_valid_lesson_transition(LESSON_REMEDIATING, LESSON_ASSIGNED)
    # No skipping.
    assert not is_valid_lesson_transition(LESSON_PENDING, LESSON_ASSIGNED)
    assert not is_valid_lesson_transition(LESSON_PENDING, LESSON_GRADED)
    assert not is_valid_lesson_transition(LESSON_GRADED, LESSON_PENDING)
    # Completed is terminal.
    assert not is_valid_lesson_transition(LESSON_COMPLETED, LESSON_PRESENTING)


def test_advance_lesson_status_rejects_invalid(isolated_workspace):
    course = _seed_course()
    with pytest.raises(ValueError, match="Invalid lesson transition"):
        advance_lesson_status(course, "l1", LESSON_GRADED)


def test_decide_next_advance_refused_without_passing_grade(isolated_workspace):
    course = _seed_course()
    advance_lesson_status(course, "l1", LESSON_PRESENTING)
    advance_lesson_status(course, "l1", LESSON_ASSIGNED)
    a = Assignment(
        assignment_id="a1",
        lesson_id="l1",
        kind="fix-broken-code",
        prompt="x",
    )
    a.grade = Grade(score_pct=40, passed=False, feedback="fail")
    course.assignments.append(a)
    course.lessons[0].assignment_ids.append("a1")
    advance_lesson_status(course, "l1", LESSON_GRADED)
    with pytest.raises(ValueError, match="did not pass"):
        decide_next(course, "l1", "advance")


def test_decide_next_remediate_then_pass_then_advance(isolated_workspace):
    course = _seed_course()
    advance_lesson_status(course, "l1", LESSON_PRESENTING)
    advance_lesson_status(course, "l1", LESSON_ASSIGNED)
    failing = Assignment(
        assignment_id="a1", lesson_id="l1", kind="fix-broken-code", prompt="x"
    )
    failing.grade = Grade(score_pct=40, passed=False)
    course.assignments.append(failing)
    course.lessons[0].assignment_ids.append("a1")
    advance_lesson_status(course, "l1", LESSON_GRADED)
    decide_next(course, "l1", "remediate")
    assert course.lessons[0].status == LESSON_REMEDIATING
    assert course.lessons[0].remediation_count == 1
    # New assignment, this time passing
    passing = Assignment(
        assignment_id="a2", lesson_id="l1", kind="fix-broken-code", prompt="x"
    )
    passing.grade = Grade(score_pct=90, passed=True)
    course.assignments.append(passing)
    course.lessons[0].assignment_ids.append("a2")
    advance_lesson_status(course, "l1", LESSON_ASSIGNED)
    advance_lesson_status(course, "l1", LESSON_GRADED)
    decide_next(course, "l1", "advance")
    assert course.lessons[0].status == LESSON_COMPLETED


def test_socratic_dialog_close_refuses_below_min_turns(isolated_workspace):
    course = _seed_course()
    assignment = Assignment(
        assignment_id="a1",
        lesson_id="l1",
        kind="socratic-dialog",
        prompt="discuss closures",
        verification=VerificationSpec(
            method="dialog_close", min_turns=3, required_concepts=[]
        ),
    )
    course.assignments.append(assignment)
    record_dialog_turn(course, "a1", role="agent_question", content="What is a closure?")
    record_dialog_turn(course, "a1", role="learner_answer", content="A function that...")
    # Only 1 learner answer; min_turns is 3.
    with pytest.raises(ValueError, match="at least 3 learner answers"):
        close_socratic_dialog(course, "a1", mastery_pct=80, summary="", gaps=[])


def test_socratic_dialog_close_refuses_uncovered_concepts(isolated_workspace):
    course = _seed_course()
    assignment = Assignment(
        assignment_id="a1",
        lesson_id="l1",
        kind="socratic-dialog",
        prompt="discuss scoping",
        verification=VerificationSpec(
            method="dialog_close",
            min_turns=2,
            required_concepts=["closure", "lexical scope"],
        ),
    )
    course.assignments.append(assignment)
    record_dialog_turn(course, "a1", role="agent_question", content="What is a closure?")
    record_dialog_turn(course, "a1", role="learner_answer", content="A function and its env.")
    record_dialog_turn(course, "a1", role="agent_question", content="Why does it matter?")
    record_dialog_turn(course, "a1", role="learner_answer", content="Encapsulation.")
    # 'lexical scope' was never mentioned by the agent.
    with pytest.raises(ValueError, match="required concepts"):
        close_socratic_dialog(course, "a1", mastery_pct=80, summary="", gaps=[])


def test_socratic_dialog_close_succeeds_when_thresholds_met(isolated_workspace):
    course = _seed_course()
    assignment = Assignment(
        assignment_id="a1",
        lesson_id="l1",
        kind="socratic-dialog",
        prompt="discuss scoping",
        verification=VerificationSpec(
            method="dialog_close",
            min_turns=2,
            required_concepts=["closure", "lexical scope"],
        ),
    )
    course.assignments.append(assignment)
    record_dialog_turn(
        course, "a1", role="agent_question",
        content="What is a closure, and how does lexical scope relate to it?",
    )
    record_dialog_turn(course, "a1", role="learner_answer", content="It's a fn + env.")
    record_dialog_turn(course, "a1", role="agent_question", content="Give an example?")
    record_dialog_turn(course, "a1", role="learner_answer", content="A counter generator.")
    grade = close_socratic_dialog(
        course, "a1", mastery_pct=82, summary="strong basics", gaps=["edge cases"]
    )
    assert grade.passed
    assert grade.score_pct == 82
    assert grade.verification_result["concepts_covered"] == ["closure", "lexical scope"]


def test_course_metrics_reports_averages_and_progress(isolated_workspace):
    course = _seed_course()
    # Add a second lesson.
    course.lessons.append(
        Lesson(lesson_id="l2", module_id="m1", title="Variables")
    )
    course.modules[0].lesson_ids.append("l2")
    # Grade one passing, one failing
    a1 = Assignment(assignment_id="a1", lesson_id="l1", kind="fix-broken-code", prompt="x")
    a1.grade = Grade(score_pct=80, passed=True)
    a2 = Assignment(assignment_id="a2", lesson_id="l2", kind="fix-broken-code", prompt="x")
    a2.grade = Grade(score_pct=40, passed=False)
    course.assignments.extend([a1, a2])
    course.lessons[0].status = LESSON_COMPLETED  # bypass the transition machinery for this test
    metrics = course_metrics(course)
    assert metrics["total_lessons"] == 2
    assert metrics["lessons_completed"] == 1
    assert metrics["overall_pct"] == 50
    assert metrics["average_score_pct"] == 60  # (80 + 40)/2


def test_next_pending_lesson_skips_completed(isolated_workspace):
    course = _seed_course()
    course.lessons.append(Lesson(lesson_id="l2", module_id="m1", title="Vars"))
    course.modules[0].lesson_ids.append("l2")
    course.lessons[0].status = LESSON_COMPLETED
    nxt = next_pending_lesson(course)
    assert nxt is not None and nxt.lesson_id == "l2"


def test_record_dialog_turn_rejects_wrong_kind(isolated_workspace):
    course = _seed_course()
    course.assignments.append(
        Assignment(assignment_id="a1", lesson_id="l1", kind="fix-broken-code", prompt="x")
    )
    with pytest.raises(ValueError, match="only valid for socratic-dialog"):
        record_dialog_turn(course, "a1", role="agent_question", content="?")
