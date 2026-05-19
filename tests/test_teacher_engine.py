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
    LESSON_LECTURING,
    LESSON_PENDING,
    LESSON_PRESENTING,
    LESSON_REMEDIATING,
    Lesson,
    Module,
    REVIEW_DONE,
    REVIEW_PENDING,
    REVIEW_SKIPPED,
    RubricItem,
    ScheduledReview,
    VerificationSpec,
    advance_lesson_status,
    close_socratic_dialog,
    complete_review,
    conclude_lecture,
    course_metrics,
    create_course,
    decide_next,
    due_reviews,
    find_assignment,
    find_lesson,
    find_review,
    is_valid_lesson_transition,
    load_course,
    next_pending_lesson,
    record_dialog_turn,
    record_lecture_turn,
    save_course,
    schedule_review,
    start_lecture,
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
    assert is_valid_lesson_transition(LESSON_PRESENTING, LESSON_LECTURING)
    assert is_valid_lesson_transition(LESSON_PRESENTING, LESSON_ASSIGNED)
    assert is_valid_lesson_transition(LESSON_LECTURING, LESSON_ASSIGNED)
    assert is_valid_lesson_transition(LESSON_ASSIGNED, LESSON_GRADED)
    assert is_valid_lesson_transition(LESSON_GRADED, LESSON_COMPLETED)
    assert is_valid_lesson_transition(LESSON_GRADED, LESSON_REMEDIATING)
    assert is_valid_lesson_transition(LESSON_REMEDIATING, LESSON_ASSIGNED)
    assert is_valid_lesson_transition(LESSON_REMEDIATING, LESSON_LECTURING)
    # No skipping.
    assert not is_valid_lesson_transition(LESSON_PENDING, LESSON_ASSIGNED)
    assert not is_valid_lesson_transition(LESSON_PENDING, LESSON_GRADED)
    assert not is_valid_lesson_transition(LESSON_GRADED, LESSON_PENDING)
    # Lecturing is forward-only; it doesn't go back to presenting.
    assert not is_valid_lesson_transition(LESSON_LECTURING, LESSON_PRESENTING)
    # Completed is terminal.
    assert not is_valid_lesson_transition(LESSON_COMPLETED, LESSON_PRESENTING)


def test_start_lecture_transitions_from_presenting(isolated_workspace):
    course = _seed_course()
    advance_lesson_status(course, "l1", LESSON_PRESENTING)
    lesson = start_lecture(course, "l1", plan="Cover malloc/free")
    assert lesson.status == LESSON_LECTURING
    assert lesson.lecture_plan == "Cover malloc/free"


def test_start_lecture_is_idempotent_when_already_lecturing(isolated_workspace):
    course = _seed_course()
    advance_lesson_status(course, "l1", LESSON_PRESENTING)
    start_lecture(course, "l1")
    # Calling again shouldn't raise.
    lesson = start_lecture(course, "l1")
    assert lesson.status == LESSON_LECTURING


def test_record_lecture_turn_requires_lecturing_status(isolated_workspace):
    course = _seed_course()
    advance_lesson_status(course, "l1", LESSON_PRESENTING)
    # Skipped start_lecture — should refuse.
    with pytest.raises(ValueError, match="requires lesson status 'lecturing'"):
        record_lecture_turn(course, "l1", role="agent_explanation", content="x")


def test_record_lecture_turn_validates_role(isolated_workspace):
    course = _seed_course()
    advance_lesson_status(course, "l1", LESSON_PRESENTING)
    start_lecture(course, "l1")
    with pytest.raises(ValueError, match="lecture turn role"):
        record_lecture_turn(course, "l1", role="not_a_role", content="x")


def test_record_lecture_turn_accepts_learner_question(isolated_workspace):
    """Mid-lecture interrupts: the learner can ask a question outside
    the agent's planned check rhythm; the engine accepts it as a
    distinct role and tracks it in the transcript."""
    course = _seed_course()
    advance_lesson_status(course, "l1", LESSON_PRESENTING)
    start_lecture(course, "l1")
    turn = record_lecture_turn(
        course, "l1", role="learner_question",
        content="Wait — does malloc zero the memory?",
    )
    assert turn.role == "learner_question"
    lesson = find_lesson(course, "l1")
    assert lesson.lecture_turns[-1].role == "learner_question"


def test_agent_check_refused_without_prior_explanation(isolated_workspace):
    """The engine forces 'explain, then check' cadence. Asking a
    comprehension question without first recording a substantive
    explanation in the same window is refused."""
    course = _seed_course()
    advance_lesson_status(course, "l1", LESSON_PRESENTING)
    start_lecture(course, "l1")
    with pytest.raises(ValueError, match="no `agent_explanation`"):
        record_lecture_turn(
            course, "l1", role="agent_check",
            content="What does malloc return?",
        )


def test_agent_check_refused_when_explanation_too_short(isolated_workspace):
    """A tiny placeholder explanation ("covered intro") shouldn't unlock
    the check gate — the rule wants real teaching content."""
    course = _seed_course()
    advance_lesson_status(course, "l1", LESSON_PRESENTING)
    start_lecture(course, "l1")
    record_lecture_turn(
        course, "l1", role="agent_explanation", content="covered intro",
    )
    with pytest.raises(ValueError, match="no `agent_explanation`"):
        record_lecture_turn(
            course, "l1", role="agent_check", content="ok so what now?",
        )


def test_agent_check_allowed_after_substantive_explanation(isolated_workspace):
    course = _seed_course()
    advance_lesson_status(course, "l1", LESSON_PRESENTING)
    start_lecture(course, "l1")
    record_lecture_turn(
        course, "l1", role="agent_explanation",
        content=(
            "malloc(size_t n) allocates n bytes of uninitialized memory and "
            "returns a void* pointer to the start of the block, or NULL on "
            "failure. The pointer must be released via free() — there is no "
            "garbage collector in C."
        ),
    )
    # Substantive explanation precedes — check is allowed.
    record_lecture_turn(
        course, "l1", role="agent_check",
        content="What does malloc return on failure?",
    )
    lesson = find_lesson(course, "l1")
    assert lesson.lecture_turns[-1].role == "agent_check"


def test_second_check_requires_fresh_explanation(isolated_workspace):
    """After explanation→check→response, the agent can't just chain
    another check without explaining the next chunk first."""
    course = _seed_course()
    advance_lesson_status(course, "l1", LESSON_PRESENTING)
    start_lecture(course, "l1")
    long_explanation = (
        "malloc(size_t n) returns a void* and may return NULL on failure. "
        "Always check the return value before dereferencing. There is no "
        "garbage collector — pair every malloc with exactly one free."
    )
    record_lecture_turn(course, "l1", role="agent_explanation", content=long_explanation)
    record_lecture_turn(course, "l1", role="agent_check", content="What does malloc return?")
    record_lecture_turn(course, "l1", role="learner_response", content="a void pointer")
    # Now try another check without a new explanation chunk — must refuse.
    with pytest.raises(ValueError, match="no `agent_explanation`"):
        record_lecture_turn(
            course, "l1", role="agent_check",
            content="And what about free()?",
        )
    # Add a substantive explanation for the second chunk; now the check
    # is allowed.
    record_lecture_turn(
        course, "l1", role="agent_explanation",
        content=(
            "free(ptr) releases the block that malloc handed back. Calling "
            "free on a NULL pointer is a no-op; calling it twice on the same "
            "non-NULL pointer is undefined behaviour."
        ),
    )
    record_lecture_turn(
        course, "l1", role="agent_check", content="What happens if you free twice?",
    )


def test_learner_question_does_not_count_toward_min_checks(isolated_workspace):
    """`conclude_lecture` requires `min_lecture_checks` agent_check
    turns. learner_question is a separate role and must NOT be
    counted, otherwise the agent could skip checks by relying on the
    learner's curiosity."""
    course = _seed_course()
    advance_lesson_status(course, "l1", LESSON_PRESENTING)
    start_lecture(course, "l1")
    # Default min_lecture_checks=2. Record 2 learner_question turns but
    # zero agent_check turns; conclude_lecture should still refuse.
    record_lecture_turn(course, "l1", role="agent_explanation", content="malloc...")
    record_lecture_turn(course, "l1", role="learner_question", content="q1?")
    record_lecture_turn(course, "l1", role="agent_explanation", content="ans1")
    record_lecture_turn(course, "l1", role="learner_question", content="q2?")
    record_lecture_turn(course, "l1", role="agent_explanation", content="ans2")
    with pytest.raises(ValueError, match="agent_check.* turns required"):
        conclude_lecture(course, "l1", comprehension_pct=90, gaps=[])


_EXPLANATION_A = (
    "First chunk: malloc(n) returns void* to an uninitialised block of n "
    "bytes, or NULL on failure. Check the return before dereferencing."
)
_EXPLANATION_B = (
    "Second chunk: free(ptr) releases the block. Pair every malloc with "
    "exactly one free. Calling free on NULL is a no-op; calling it twice "
    "on the same non-NULL pointer is undefined behaviour."
)


def _two_check_lecture(course):
    """Drive a lesson through two explain→check→response cycles. The
    new gate requires substantive explanations before each check, so
    helper tests share this scaffold."""
    record_lecture_turn(course, "l1", role="agent_explanation", content=_EXPLANATION_A)
    record_lecture_turn(course, "l1", role="agent_check", content="What does malloc return on failure?")
    record_lecture_turn(course, "l1", role="learner_response", content="NULL")
    record_lecture_turn(course, "l1", role="agent_explanation", content=_EXPLANATION_B)
    record_lecture_turn(course, "l1", role="agent_check", content="What happens if you free twice?")
    record_lecture_turn(course, "l1", role="learner_response", content="undefined behaviour")


def test_conclude_lecture_refuses_below_min_checks(isolated_workspace):
    course = _seed_course()
    advance_lesson_status(course, "l1", LESSON_PRESENTING)
    start_lecture(course, "l1")
    # Only one agent_check; default min_lecture_checks is 2.
    record_lecture_turn(course, "l1", role="agent_explanation", content=_EXPLANATION_A)
    record_lecture_turn(course, "l1", role="agent_check", content="What does it return on failure?")
    record_lecture_turn(course, "l1", role="learner_response", content="NULL", comprehension_signal="on track")
    with pytest.raises(ValueError, match="agent_check.* turns required"):
        conclude_lecture(course, "l1", comprehension_pct=90, gaps=[])


def test_conclude_lecture_refuses_below_comprehension_threshold(isolated_workspace):
    course = _seed_course()
    advance_lesson_status(course, "l1", LESSON_PRESENTING)
    start_lecture(course, "l1")
    _two_check_lecture(course)
    # Default threshold is 60. comprehension_pct=40 < 60 → refusal.
    with pytest.raises(ValueError, match="below threshold"):
        conclude_lecture(course, "l1", comprehension_pct=40, gaps=["X"])


def test_conclude_lecture_advances_to_assigned(isolated_workspace):
    course = _seed_course()
    advance_lesson_status(course, "l1", LESSON_PRESENTING)
    start_lecture(course, "l1")
    _two_check_lecture(course)
    lesson = conclude_lecture(
        course, "l1", comprehension_pct=85, gaps=["pointer arithmetic"], summary="solid"
    )
    assert lesson.status == LESSON_ASSIGNED
    assert lesson.lecture_concluded is True
    assert lesson.lecture_comprehension_pct == 85
    assert lesson.lecture_gaps == ["pointer arithmetic"]


def test_conclude_lecture_can_stay_lecturing_when_not_ready(isolated_workspace):
    course = _seed_course()
    advance_lesson_status(course, "l1", LESSON_PRESENTING)
    start_lecture(course, "l1")
    _two_check_lecture(course)
    lesson = conclude_lecture(
        course, "l1", comprehension_pct=80, gaps=[], ready_for_assignment=False
    )
    # Agent decided more lecture is needed; lesson stays lecturing.
    assert lesson.status == LESSON_LECTURING
    assert lesson.lecture_concluded is True


def test_schedule_review_refuses_unknown_lesson(isolated_workspace):
    course = _seed_course()
    with pytest.raises(ValueError, match="source lesson"):
        schedule_review(course, source_lesson_id="not_a_lesson", after_n_lessons=2)


def test_schedule_review_refuses_zero_interval(isolated_workspace):
    course = _seed_course()
    with pytest.raises(ValueError, match="after_n_lessons must be >= 1"):
        schedule_review(course, source_lesson_id="l1", after_n_lessons=0)


def test_due_reviews_empty_until_counter_advances(isolated_workspace):
    course = _seed_course()
    schedule_review(course, source_lesson_id="l1", after_n_lessons=2)
    # No lessons completed yet; nothing's due.
    assert due_reviews(course) == []
    # Manually bump the counter to simulate two finished lessons.
    course.lessons_completed_count += 2
    assert len(due_reviews(course)) == 1


def test_advance_to_completed_bumps_counter(isolated_workspace):
    course = _seed_course()
    assert course.lessons_completed_count == 0
    advance_lesson_status(course, "l1", LESSON_PRESENTING)
    advance_lesson_status(course, "l1", LESSON_ASSIGNED)
    advance_lesson_status(course, "l1", LESSON_GRADED)
    advance_lesson_status(course, "l1", LESSON_COMPLETED)
    assert course.lessons_completed_count == 1
    # Idempotent — re-completing doesn't double-bump (the transition
    # table forbids completed→completed anyway, but the guard is here).


def test_complete_review_records_score_and_status(isolated_workspace):
    course = _seed_course()
    r = schedule_review(course, source_lesson_id="l1", after_n_lessons=2)
    course.lessons_completed_count += 2
    finalized = complete_review(course, r.review_id, score_pct=85)
    assert finalized.status == REVIEW_DONE
    assert finalized.score_pct == 85
    assert finalized.completed_at is not None
    # The completed review is no longer in due_reviews.
    assert due_reviews(course) == []


def test_complete_review_skipped_path(isolated_workspace):
    course = _seed_course()
    r = schedule_review(course, source_lesson_id="l1", after_n_lessons=1)
    course.lessons_completed_count += 1
    finalized = complete_review(course, r.review_id, score_pct=0, skipped=True)
    assert finalized.status == REVIEW_SKIPPED
    assert due_reviews(course) == []


def test_complete_review_refuses_double_complete(isolated_workspace):
    course = _seed_course()
    r = schedule_review(course, source_lesson_id="l1", after_n_lessons=1)
    course.lessons_completed_count += 1
    complete_review(course, r.review_id, score_pct=90)
    with pytest.raises(ValueError, match="already finalized"):
        complete_review(course, r.review_id, score_pct=80)


def test_scheduled_reviews_survive_disk_round_trip(isolated_workspace):
    course = _seed_course()
    r = schedule_review(
        course, source_lesson_id="l1", after_n_lessons=3, notes="weak on pointers"
    )
    course.lessons_completed_count = 5
    save_course(course)
    reloaded = load_course(course.course_id)
    assert reloaded is not None
    assert reloaded.lessons_completed_count == 5
    assert len(reloaded.scheduled_reviews) == 1
    assert reloaded.scheduled_reviews[0].review_id == r.review_id
    assert reloaded.scheduled_reviews[0].notes == "weak on pointers"


def test_lecture_round_trip_through_disk(isolated_workspace):
    course = _seed_course()
    advance_lesson_status(course, "l1", LESSON_PRESENTING)
    start_lecture(course, "l1", plan="malloc + free")
    _two_check_lecture(course)
    conclude_lecture(course, "l1", comprehension_pct=80, gaps=["arithmetic"])
    save_course(course)
    reloaded = load_course(course.course_id)
    lesson = find_lesson(reloaded, "l1")
    assert lesson is not None
    assert lesson.status == LESSON_ASSIGNED
    assert lesson.lecture_concluded is True
    # Two explain→check→response cycles = 6 turns.
    assert len(lesson.lecture_turns) == 6
    assert lesson.lecture_turns[0].role == "agent_explanation"
    assert lesson.lecture_turns[1].role == "agent_check"


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
