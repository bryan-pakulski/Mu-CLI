"""Verification dispatch for teacher mode assignments.

Each assignment kind has a dedicated grader. The contract: a grader
takes an Assignment and a submission payload and returns a `Grade`.
Code-style kinds run a shell command via the same subprocess-with-
timeout-and-markers primitive the security engine uses for PoC
verification. Prose-style kinds use a keyword gate before an
LLM-judge step (the LLM step lands as a feedback string supplied by
the caller — the grader itself just enforces the structural gate).

`socratic-dialog` is handled separately via `engine.close_socratic_dialog`
because it has its own lifecycle (record_turn → close_dialog) and is
not a single-shot grade.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from typing import Any

from mu.teacher.engine import (
    ASSIGNMENT_FAILED,
    ASSIGNMENT_PASSED,
    Assignment,
    Grade,
    VerificationSpec,
)

_DEFAULT_TIMEOUT = 30


def grade(
    assignment: Assignment,
    submission: dict[str, Any] | None = None,
    *,
    feedback_override: str | None = None,
    llm_rubric_score: int | None = None,
) -> Grade:
    """Dispatch grading by assignment kind.

    `submission` may be omitted when the engine reads a previously
    persisted submission from disk; the caller is responsible for
    populating `assignment.submission` first if so.

    `feedback_override` / `llm_rubric_score` are optional overrides for
    rubric-judge kinds (the agent supplies its judgment alongside the
    grade call).
    """
    if assignment.kind == "socratic-dialog":
        raise ValueError(
            "socratic-dialog assignments are graded via close_socratic_dialog, "
            "not grade()"
        )

    payload = submission if submission is not None else (assignment.submission or {})
    spec = assignment.verification

    if assignment.kind in {"fix-broken-code", "implement-from-scratch", "command-output"}:
        grade_obj = _grade_exec(assignment, spec, payload)
    elif assignment.kind in {"multiple-choice", "fill-blank", "predict-output"}:
        grade_obj = _grade_match(assignment, spec, payload)
    elif assignment.kind in {"short-answer", "explain-trace"}:
        grade_obj = _grade_rubric(
            assignment,
            spec,
            payload,
            feedback_override=feedback_override,
            llm_rubric_score=llm_rubric_score,
        )
    else:
        raise ValueError(f"Unknown assignment kind: {assignment.kind!r}")

    assignment.grade = grade_obj
    assignment.status = ASSIGNMENT_PASSED if grade_obj.passed else ASSIGNMENT_FAILED
    return grade_obj


# --- exec-and-markers ----------------------------------------------------


def _grade_exec(
    assignment: Assignment,
    spec: VerificationSpec,
    submission: dict[str, Any],
) -> Grade:
    if not spec.verify_cmd:
        raise ValueError(
            f"assignment {assignment.assignment_id!r} requires a verify_cmd"
        )
    cwd = spec.working_dir or submission.get("working_dir") or os.getcwd()
    timeout = max(1, int(spec.timeout_seconds or _DEFAULT_TIMEOUT))
    started = time.time()
    try:
        proc = subprocess.run(
            ["/bin/bash", "-lc", spec.verify_cmd],
            cwd=cwd if os.path.isdir(cwd) else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        exit_code = proc.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        exit_code = -1
        timed_out = True
    elapsed = time.time() - started

    combined = stdout + "\n" + stderr
    missing = [m for m in (spec.expected_markers or []) if m not in combined]
    forbidden_present = [m for m in (spec.forbidden_markers or []) if m in combined]
    passed = (
        not timed_out
        and not missing
        and not forbidden_present
    )

    # Score = full credit on pass, partial on missing markers proportional
    # to fraction of markers matched (so the learner sees progress when
    # they get half-way there).
    if spec.expected_markers:
        matched = len(spec.expected_markers) - len(missing)
        ratio = matched / len(spec.expected_markers)
    else:
        ratio = 1.0 if passed else 0.0
    if forbidden_present or timed_out:
        ratio = 0.0
    score = int(round(100 * ratio)) if not passed else 100

    feedback_parts = []
    if passed:
        feedback_parts.append("Verification command produced every expected marker.")
    else:
        if missing:
            feedback_parts.append(
                "Missing expected markers: " + ", ".join(f"`{m}`" for m in missing)
            )
        if forbidden_present:
            feedback_parts.append(
                "Forbidden markers were emitted: "
                + ", ".join(f"`{m}`" for m in forbidden_present)
            )
        if timed_out:
            feedback_parts.append(f"Verification timed out after {timeout}s.")
    return Grade(
        score_pct=score,
        passed=passed,
        rubric_breakdown=[],
        verification_result={
            "method": "exec_markers",
            "verify_cmd": spec.verify_cmd,
            "cwd": cwd,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "elapsed_seconds": round(elapsed, 3),
            "missing_markers": missing,
            "forbidden_markers_present": forbidden_present,
            "stdout_tail": stdout[-2000:],
            "stderr_tail": stderr[-2000:],
        },
        feedback=" ".join(feedback_parts) or "Verification completed.",
    )


# --- exact / regex match -------------------------------------------------


def _grade_match(
    assignment: Assignment,
    spec: VerificationSpec,
    submission: dict[str, Any],
) -> Grade:
    # `submission` for these kinds is either {"answer": "..."} for a single
    # answer or {"answers": {qid: value}} for a multi-question quiz.
    if "answers" in submission:
        return _grade_multi_answer(assignment, spec, submission["answers"])
    answer = str(submission.get("answer", "")).strip()
    expected = (spec.expected_answer or "").strip()
    matched, detail = _match_one(spec.method, answer, expected, spec.case_sensitive)
    score = 100 if matched else 0
    feedback = (
        "Correct."
        if matched
        else f"Expected `{expected}`; got `{answer}`. ({detail})"
    )
    return Grade(
        score_pct=score,
        passed=matched,
        rubric_breakdown=[],
        verification_result={
            "method": spec.method,
            "expected": expected,
            "got": answer,
            "match_detail": detail,
        },
        feedback=feedback,
    )


def _grade_multi_answer(
    assignment: Assignment,
    spec: VerificationSpec,
    answers: dict[str, str],
) -> Grade:
    """Quiz path — the engine carries the per-question correct values in
    `assignment.submission['quiz_keys']` (populated when the engine
    persisted the questions). Each answer matched produces 1/N of the
    score."""
    quiz_keys = (assignment.submission or {}).get("quiz_keys") or {}
    if not quiz_keys:
        # No per-question keys available; fall back to comparing each answer
        # against the spec's expected_answer (rare but defined).
        matched_count = 0
        per_q = []
        for qid, value in answers.items():
            ok, _ = _match_one(
                spec.method, value or "", spec.expected_answer or "", spec.case_sensitive
            )
            per_q.append({"qid": qid, "answer": value, "correct": ok})
            if ok:
                matched_count += 1
        total = max(1, len(answers))
        score = int(round(100 * matched_count / total))
        passed = matched_count == total
        return Grade(
            score_pct=score,
            passed=passed,
            rubric_breakdown=[],
            verification_result={"method": spec.method, "per_question": per_q},
            feedback=f"{matched_count}/{total} answers correct.",
        )
    # `quiz_keys` carries both `qid -> expected` and `qid__method -> method`
    # entries. Filter the method-suffix keys out of the question iteration.
    question_keys = {
        qid: expected
        for qid, expected in quiz_keys.items()
        if not qid.endswith("__method")
    }
    total = len(question_keys)
    matched_count = 0
    per_q = []
    for qid, expected in question_keys.items():
        value = answers.get(qid, "")
        ok, detail = _match_one(
            quiz_keys.get(f"{qid}__method", spec.method),
            str(value or ""),
            str(expected or ""),
            spec.case_sensitive,
        )
        per_q.append({"qid": qid, "answer": value, "expected": expected, "correct": ok})
        if ok:
            matched_count += 1
    score = int(round(100 * matched_count / total)) if total else 0
    passed = score >= assignment.pass_threshold
    feedback = f"{matched_count}/{total} correct ({score}%)."
    return Grade(
        score_pct=score,
        passed=passed,
        rubric_breakdown=[],
        verification_result={"method": spec.method, "per_question": per_q},
        feedback=feedback,
    )


def _match_one(method: str, value: str, expected: str, case_sensitive: bool) -> tuple[bool, str]:
    if not expected:
        return False, "no expected_answer configured"
    if method == "regex_match":
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            ok = bool(re.search(expected, value, flags))
            return ok, "regex_match"
        except re.error as exc:
            return False, f"regex error: {exc}"
    # exact_match (default)
    if case_sensitive:
        return value == expected, "exact_match (case sensitive)"
    return value.casefold() == expected.casefold(), "exact_match (case insensitive)"


# --- rubric (prose answers) ---------------------------------------------


def _grade_rubric(
    assignment: Assignment,
    spec: VerificationSpec,
    submission: dict[str, Any],
    *,
    feedback_override: str | None,
    llm_rubric_score: int | None,
) -> Grade:
    """Two-stage gate: every `rubric_keywords` must appear (case-insensitive
    substring) before the LLM-judge score is admitted. The agent supplies
    the score via `llm_rubric_score` and feedback via `feedback_override`."""
    response = str(submission.get("answer", "") or submission.get("response", "")).strip()
    lower = response.lower()
    missing = [
        kw for kw in (spec.rubric_keywords or [])
        if kw.strip() and kw.strip().lower() not in lower
    ]
    if missing:
        return Grade(
            score_pct=0,
            passed=False,
            rubric_breakdown=[],
            verification_result={
                "method": "rubric_judge",
                "stage": "keyword_gate",
                "missing_keywords": missing,
            },
            feedback=(
                "Required concept terms were not mentioned: "
                + ", ".join(f"`{m}`" for m in missing)
                + ". Revise the answer to address each before the rubric will admit it."
            ),
        )
    if llm_rubric_score is None:
        # Caller hasn't supplied a judgment yet — signal that gate-1 passed
        # but a separate `submit_rubric_judgment` is needed. We return a
        # provisional Grade with passed=False so the assignment stays
        # ungraded until the agent provides the judgment.
        return Grade(
            score_pct=0,
            passed=False,
            rubric_breakdown=[],
            verification_result={
                "method": "rubric_judge",
                "stage": "awaiting_judgment",
            },
            feedback=(
                "Keyword gate passed. Provide a rubric judgment via "
                "`grade_assignment(..., llm_rubric_score=N, feedback=...)` "
                "to finalize."
            ),
        )
    score = max(0, min(100, int(llm_rubric_score)))
    passed = score >= assignment.pass_threshold
    rubric_breakdown = [
        {
            "criterion": item.criterion,
            "weight": item.weight,
            "description": item.description,
        }
        for item in assignment.rubric
    ]
    return Grade(
        score_pct=score,
        passed=passed,
        rubric_breakdown=rubric_breakdown,
        verification_result={
            "method": "rubric_judge",
            "stage": "judged",
            "matched_keywords": list(spec.rubric_keywords or []),
        },
        feedback=(feedback_override or "").strip()
        or f"Rubric judgment: {score}% ({'pass' if passed else 'fail'}).",
    )


__all__ = ["grade"]
