"""Tests for teacher-mode assignment grading.

One test per assignment kind plus the LLM-rubric two-stage gate.
"""

from __future__ import annotations

import pytest

from mu.teacher.engine import (
    Assignment,
    RubricItem,
    VerificationSpec,
)
from mu.teacher.grading import grade


def _assignment(kind: str, **overrides) -> Assignment:
    return Assignment(
        assignment_id="a1",
        lesson_id="l1",
        kind=kind,
        prompt="x",
        verification=overrides.pop("verification", VerificationSpec()),
        **overrides,
    )


def test_fix_broken_code_passes_when_markers_match(tmp_path):
    a = _assignment(
        "fix-broken-code",
        verification=VerificationSpec(
            method="exec_markers",
            verify_cmd="echo PASS && echo count=5",
            expected_markers=["PASS", "count=5"],
            timeout_seconds=5,
            working_dir=str(tmp_path),
        ),
    )
    g = grade(a, submission={})
    assert g.passed
    assert g.score_pct == 100
    assert "every expected marker" in g.feedback


def test_fix_broken_code_fails_on_missing_marker(tmp_path):
    a = _assignment(
        "fix-broken-code",
        verification=VerificationSpec(
            method="exec_markers",
            verify_cmd="echo PASS",
            expected_markers=["PASS", "count=5"],
            timeout_seconds=5,
            working_dir=str(tmp_path),
        ),
    )
    g = grade(a, submission={})
    assert not g.passed
    assert "Missing expected markers" in g.feedback
    assert "count=5" in g.feedback
    # Partial credit when half the markers match.
    assert 0 < g.score_pct < 100


def test_fix_broken_code_fails_on_forbidden_marker(tmp_path):
    a = _assignment(
        "fix-broken-code",
        verification=VerificationSpec(
            method="exec_markers",
            verify_cmd="echo PASS && echo FATAL",
            expected_markers=["PASS"],
            forbidden_markers=["FATAL"],
            timeout_seconds=5,
            working_dir=str(tmp_path),
        ),
    )
    g = grade(a, submission={})
    assert not g.passed
    assert g.score_pct == 0
    assert "Forbidden markers" in g.feedback


def test_predict_output_exact_match():
    a = _assignment(
        "predict-output",
        verification=VerificationSpec(
            method="exact_match", expected_answer="42"
        ),
    )
    g = grade(a, submission={"answer": "42"})
    assert g.passed
    g_wrong = grade(_assignment("predict-output",
        verification=VerificationSpec(method="exact_match", expected_answer="42")),
        submission={"answer": "43"})
    assert not g_wrong.passed


def test_fill_blank_regex_match():
    a = _assignment(
        "fill-blank",
        verification=VerificationSpec(
            method="regex_match", expected_answer=r"^\d+$"
        ),
    )
    assert grade(a, submission={"answer": "123"}).passed
    assert not grade(
        _assignment("fill-blank",
            verification=VerificationSpec(method="regex_match", expected_answer=r"^\d+$")),
        submission={"answer": "abc"},
    ).passed


def test_multiple_choice_multi_answer_quiz_path():
    a = _assignment("multiple-choice", verification=VerificationSpec(method="exact_match"))
    # Pre-populate quiz_keys (as assign_exercise_tool would have done).
    a.submission = {
        "quiz_keys": {
            "q1": "4",
            "q1__method": "exact_match",
            "q2": "Paris",
            "q2__method": "exact_match",
        }
    }
    a.pass_threshold = 70
    g = grade(a, submission={
        **a.submission,
        "answers": {"q1": "4", "q2": "Berlin"},
    })
    assert g.score_pct == 50  # 1/2
    assert not g.passed
    g2 = grade(a, submission={
        **a.submission,
        "answers": {"q1": "4", "q2": "Paris"},
    })
    assert g2.score_pct == 100
    assert g2.passed


def test_short_answer_keyword_gate_blocks_missing_keywords():
    a = _assignment(
        "short-answer",
        verification=VerificationSpec(
            method="rubric_judge", rubric_keywords=["closure", "scope"]
        ),
    )
    a.rubric = [RubricItem(criterion="depth", weight=2)]
    g = grade(a, submission={"answer": "Just talking about variables."})
    assert not g.passed
    assert g.score_pct == 0
    assert "concept terms were not mentioned" in g.feedback
    assert "closure" in g.feedback


def test_short_answer_awaits_judgment_after_gate_passes():
    a = _assignment(
        "short-answer",
        verification=VerificationSpec(
            method="rubric_judge", rubric_keywords=["closure", "scope"]
        ),
    )
    g = grade(
        a,
        submission={"answer": "A closure captures its lexical scope."},
    )
    # Keyword gate passed but no llm_rubric_score provided yet.
    assert not g.passed
    assert g.verification_result["stage"] == "awaiting_judgment"


def test_short_answer_admits_judgment_after_gate_passes():
    a = _assignment(
        "short-answer",
        verification=VerificationSpec(
            method="rubric_judge", rubric_keywords=["closure", "scope"]
        ),
    )
    a.rubric = [RubricItem(criterion="depth", weight=2)]
    g = grade(
        a,
        submission={"answer": "A closure captures its lexical scope and surrounding state."},
        llm_rubric_score=85,
        feedback_override="Solid: covers capture and lexical scoping.",
    )
    assert g.passed
    assert g.score_pct == 85
    assert g.rubric_breakdown[0]["criterion"] == "depth"


def test_socratic_dialog_kind_rejects_grade_dispatch():
    a = _assignment("socratic-dialog")
    with pytest.raises(ValueError, match="close_socratic_dialog"):
        grade(a, submission={})


def test_unknown_kind_raises():
    a = _assignment("madness")
    with pytest.raises(ValueError, match="Unknown assignment kind"):
        grade(a, submission={})


def test_exec_timeout_marks_zero(tmp_path):
    a = _assignment(
        "fix-broken-code",
        verification=VerificationSpec(
            method="exec_markers",
            verify_cmd="sleep 10",
            expected_markers=["PASS"],
            timeout_seconds=1,
            working_dir=str(tmp_path),
        ),
    )
    g = grade(a, submission={})
    assert not g.passed
    assert g.score_pct == 0
    assert g.verification_result["timed_out"] is True
