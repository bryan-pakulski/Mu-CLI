"""Teacher-mode tools over the course engine (``mu.teacher.engine``) and its
gates (``mu.teacher.gates``).

The learner's decisions — approving the curriculum, finishing early,
overriding lesson order — are not tools; they live on the panel, ``/teach``
and mobile. Each mutation saves ``course.json`` and refreshes the session's
pointer record (``gates.course_record``).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from typing import Any, Dict

from mu.modes.events import GateError
from mu.modes.preview import register_preview
from mu.modes.tooling import envelope
from mu.teacher import gates
from mu.teacher import storage as _storage
from mu.teacher.engine import (
    ASSIGNMENT_SUBMITTED, COURSE_COMPLETED, GRADE_AWAITING, LESSON_ASSIGNED, LESSON_GRADED,
    LESSON_LECTURING, LESSON_PENDING, LESSON_PRESENTING, LESSON_REMEDIATING, Assignment, Course,
    RubricItem, VerificationSpec, add_event, advance_lesson_status, close_socratic_dialog,
    course_metrics, create_course, decide_next, find_assignment, find_lesson, load_course,
    record_dialog_turn, register_exercise_file, save_course, write_lecture_transcript,
)
from mu.teacher.grading import grade as grade_assignment_payload
from mu.tools import tool

from . import schemas

_PHASE = dict(requires_approval=False, phase="teacher", execution_kind="memory", preview_policy="none",
              server_policy="session_only")


# ----------------------------------------------------------- helpers


def _session(context: Any):
    session = getattr(context, "session", None)
    if session is None:
        raise ValueError("teacher tools need an active session")
    return session


def _fc(context: Any):
    return getattr(context, "folder_context", None) or getattr(getattr(context, "session", None), "folder_context", None)


def _persist(session, course: Course) -> Dict[str, Any]:
    save_course(course)
    record = gates.course_record(course, getattr(session, "folder_context", None))
    manager = session.session_manager
    manager.upsert_teacher_course(record)
    if manager.active_course_id == course.course_id:
        manager.teacher_state = dict(record)
    manager.save_history_turn(getattr(session, "folder_context", None))
    return record


def _activate(session, course: Course) -> Dict[str, Any]:
    record = _persist(session, course)
    manager = session.session_manager
    manager.active_course_id = course.course_id
    manager.teacher_state = dict(record)
    manager.save_history_turn(getattr(session, "folder_context", None))
    return record


def _course(session, context, course_id: str | None = None) -> Course:
    """Load the active (or named) course, mutable, from its course.json."""
    manager = session.session_manager
    target = _storage.slugify(str(course_id)) if course_id else (manager.active_course_id or
                                                                  (manager.get_teacher_state() or {}).get("course_id"))
    if not target:
        raise GateError("No active course — create_course, or the learner resumes one with /teach load <id>.",
                        code="no_course")
    record = manager.get_course(target) or {}
    for path in (str(record.get("course_path") or ""), _storage.course_state_path(target, _fc(context))):
        if path and os.path.exists(path):
            course = load_course(path, _fc(context))
            if course is not None:
                return course
    course = load_course(target, _fc(context))
    if course is None:
        raise GateError(f"Course {target!r} not found.", code="not_found")
    return course


def _lesson(course: Course, lesson_id: Any):
    lesson = find_lesson(course, _storage.slugify(str(lesson_id or "")))
    if lesson is None:
        raise GateError(f"Lesson {lesson_id!r} not found. Known: {[l.lesson_id for l in course.lessons][:12]}",
                        code="not_found")
    return lesson


def guarded(name: str):
    def deco(fn):
        def wrapper(args: Dict[str, Any], context) -> Dict[str, Any]:
            try:
                return fn(args or {}, context)
            except GateError as exc:
                return envelope(name, False, str(exc), dict(exc.data or {}), exc.code)
            except (ValueError, KeyError) as exc:
                return envelope(name, False, str(exc.args[0] if exc.args else exc))
        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper
    return deco


def _write_curriculum_md(course: Course) -> None:
    if not course.directory:
        return
    lines = [f"# {course.subject} — Curriculum (revision {course.curriculum_revision})", "",
             f"- **Status**: {course.status}", ""]
    for module in sorted(course.modules, key=lambda m: m.order):
        lines += [f"## Module {module.order}: {module.title}", f"_Goal_: {module.goal}" if module.goal else "", ""]
        for lesson_id in module.lesson_ids:
            lesson = find_lesson(course, lesson_id)
            if lesson is None:
                continue
            lines.append(f"- **{lesson.title}** ({lesson.status})")
            lines += [f"  - {o}" for o in lesson.learning_objectives]
            for src in lesson.sources:
                lines.append(f"  - Source: [{src.get('title') or src.get('url')}]({src.get('url')})")
        lines.append("")
    with open(os.path.join(course.directory, "curriculum.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def _write_report_card(course: Course) -> str:
    path = os.path.join(course.directory, "report_card.md")
    metrics = course_metrics(course)
    lines = [f"# Report Card — {course.subject}", "",
             f"- **Lessons completed**: {metrics['lessons_completed']} / {metrics['total_lessons']}",
             f"- **Course mastery**: {gates.course_mastery(course) or 0}%"]
    if course.early_exit:
        lines.append(f"- **Finished early**: {course.early_exit.get('reason')}")
    lines += ["", "## Mastery by objective", ""]
    for row in gates.mastery_grid(course):
        pct = row["mastery_pct"]
        lines.append(f"- {row['lesson_id']} · {row['objective']}: {pct if pct is not None else '—'}%"
                     f" ({row['attempts']} attempts)")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def _write_artifacts(course: Course, assignment: Assignment, files) -> list:
    written = []
    work = gates.work_dir(course, assignment)
    os.makedirs(work, exist_ok=True)
    for entry in files or []:
        rel = str(entry.get("path", "") or "").strip()
        if not rel or rel.startswith("/") or ".." in rel.split(os.sep):
            continue
        target = os.path.join(work, rel)
        os.makedirs(os.path.dirname(target) or work, exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(str(entry.get("content", "") or ""))
        written.append(target)
    return written


def _default_method(kind: str) -> str:
    return {"fix-broken-code": "exec_markers", "implement-from-scratch": "exec_markers",
            "command-output": "exec_markers", "predict-output": "exact_match", "multiple-choice": "exact_match",
            "fill-blank": "regex_match", "short-answer": "rubric_judge", "explain-trace": "rubric_judge",
            "socratic-dialog": "dialog_close"}.get(kind, "exec_markers")


# ============================================================ course lifecycle


@tool(name="create_course", description=(
    "Open a teacher-mode course (status 'diagnosing'). What earlier courses learned about this learner is "
    "returned; then run the diagnostic conversation and record_diagnostic."),
    parameters=schemas.CREATE_COURSE, **_PHASE)
@guarded("create_course")
def create_course_tool(args: Dict[str, Any], context) -> Dict[str, Any]:
    session = _session(context)
    course = create_course(subject=str(args.get("subject", "")).strip(),
                           target_level=str(args.get("target_level") or "beginner"),
                           learner_summary=str(args.get("learner_summary") or ""), folder_context=_fc(context),
                           course_id=str(args.get("course_id") or "").strip() or None)
    prior = gates.prior_learner_model(session)
    if prior:
        course.learner_profile = {**(course.learner_profile or {}), "prior": prior}
    record = _activate(session, course)
    return envelope("create_course", True, f"Course {course.course_id} created.",
                    {"course": record, "prior_learner_model": prior})


@tool(name="record_diagnostic", description=(
    "Save the learner profile from the diagnostic Q&A (required before propose_curriculum). Re-running it "
    "merges: lists are unioned, given scalars replace."),
    parameters=schemas.RECORD_DIAGNOSTIC, **_PHASE)
@guarded("record_diagnostic")
def record_diagnostic_tool(args: Dict[str, Any], context) -> Dict[str, Any]:
    session = _session(context)
    course = _course(session, context, args.get("course_id"))
    incoming = {k: v for k, v in args.items() if k != "course_id"}
    profile = gates.merge_profile(course.learner_profile or {}, incoming)
    profile.setdefault("stumbling_blocks", [])
    profile.setdefault("calibration_history", [])
    profile["recorded_at"] = time.time()
    course.learner_profile = profile
    add_event(course, kind="diagnostic_recorded", entity="course", entity_id=course.course_id,
              payload={k: v for k, v in incoming.items()})
    record = _persist(session, course)
    gates.remember_profile(session, course)
    return envelope("record_diagnostic", True, "Diagnostic recorded; the profile now rides in every turn.",
                    {"course": record, "learner_profile": profile})


@tool(name="update_learner_profile", description=(
    "Update the learner profile when you observe a pattern (an analogy lands, pace is off, a stumbling block). "
    "Lists merge, scalars overwrite; the observation is kept in calibration_history."),
    parameters=schemas.UPDATE_LEARNER_PROFILE, **_PHASE)
@guarded("update_learner_profile")
def update_learner_profile_tool(args: Dict[str, Any], context) -> Dict[str, Any]:
    session = _session(context)
    course = _course(session, context, args.get("course_id"))
    observation = str(args.get("observation") or "").strip()
    if not observation:
        raise ValueError("observation is required — describe what you saw.")
    deltas = {k: v for k, v in args.items() if k not in ("course_id", "observation") and v not in (None, "", [])}
    profile = gates.merge_profile(course.learner_profile or {}, deltas)
    history = list(profile.get("calibration_history") or [])
    history.append({"at": time.time(), "observation": observation, "deltas": deltas})
    profile["calibration_history"] = history[-50:]
    course.learner_profile = profile
    add_event(course, kind="learner_profile_updated", entity="course", entity_id=course.course_id,
              payload={"observation": observation, "deltas": deltas})
    record = _persist(session, course)
    gates.remember_profile(session, course, observation)
    return envelope("update_learner_profile", True, "Profile updated.",
                    {"course": record, "deltas": deltas})


@tool(name="propose_curriculum", description=(
    "Propose modules → lessons (1–4 learning objectives each). Needs the diagnostic. On a course in "
    "progress it makes a revision that keeps lessons with graded work. The learner approves it — there is "
    "no approval tool; ask them to approve (/teach approve or the panel)."),
    parameters=schemas.PROPOSE_CURRICULUM, **_PHASE)
@guarded("propose_curriculum")
def propose_curriculum_tool(args: Dict[str, Any], context) -> Dict[str, Any]:
    session = _session(context)
    course = _course(session, context, args.get("course_id"))
    result = gates.propose_curriculum(course, args.get("modules") or [])
    _write_curriculum_md(course)
    record = _persist(session, course)
    return envelope("propose_curriculum", True,
                    f"Curriculum revision {result['revision']} proposed ({len(course.modules)} modules, "
                    f"{len(course.lessons)} lessons; kept {len(result['kept'])} with history). Show it to the "
                    "learner and ask them to approve it.",
                    {"course": record, **result})


# ============================================================ lessons and lecture


@tool(name="start_lesson", description=(
    "Begin a lesson. Needs the approved curriculum and respects order (earlier lessons done, earlier modules "
    "completed with complete_module)."),
    parameters={"type": "object", "properties": {"lesson_id": {"type": "string"}}, "required": ["lesson_id"]},
    **_PHASE)
@guarded("start_lesson")
def start_lesson_tool(args: Dict[str, Any], context) -> Dict[str, Any]:
    session = _session(context)
    course = _course(session, context)
    lesson = gates.start_lesson(course, _storage.slugify(str(args.get("lesson_id") or "")))
    record = _persist(session, course)
    due = gates.due_reviews(course)
    message = f"Lesson {lesson.lesson_id} started: {lesson.title}. Objectives: {lesson.learning_objectives}."
    if due:
        message += " Reviews due first: " + ", ".join(r.review_id for r in due[:4]) + "."
    return envelope("start_lesson", True, message,
                    {"course": record, "lesson": asdict(lesson), "due_reviews": [asdict(r) for r in due]})


@tool(name="teach_check", description=(
    "Record a comprehension check in the lecture. kind=choice shows the learner a picker and records their "
    "pick; kind=open: ask it in your message and end the message — their reply is attached automatically. "
    "Then assess_response."),
    parameters={"type": "object", "properties": {
        "question": {"type": "string"},
        "kind": {"type": "string", "enum": ["open", "choice"]},
        "options": {"type": "array", "items": {"type": "string"}},
        "objective": {"type": "string", "description": "Which lesson objective this checks."},
        "lesson_id": {"type": "string"},
    }, "required": ["question", "kind"]},
    **_PHASE)
@guarded("teach_check")
def teach_check_tool(args: Dict[str, Any], context) -> Dict[str, Any]:
    session = _session(context)
    course = _course(session, context)
    check = gates.teach_check(course, question=str(args.get("question") or ""), kind=str(args.get("kind") or "open"),
                              options=args.get("options") or [], lesson_id=str(args.get("lesson_id") or ""),
                              objective=str(args.get("objective") or ""))
    if check.kind == "choice":
        from mu.tools.prompt.handlers import ask_user_choice_tool

        raw = json.loads(ask_user_choice_tool({"question": check.question, "options": check.options}, context))
        picked = ", ".join(str(x) for x in raw.get("selected") or []) or str(raw.get("other_text") or "")
        if picked and not raw.get("cancelled"):
            gates.record_answer(course, check.check_id, picked, actor="user:gui")
    _persist(session, course)
    if check.answer is not None:
        return envelope("teach_check", True, f"{check.check_id}: the learner chose {check.answer!r}. "
                        "Assess it with assess_response.", asdict(check))
    return envelope("teach_check", True, f"{check.check_id} recorded. Ask it and end your message; the "
                    "learner's reply is attached to it.", asdict(check))


@tool(name="assess_response", description=(
    "Record your read of the learner's answer to a check: on_track, partial or confused (one line note). "
    "The lecture concludes automatically once enough checks are on track."),
    parameters={"type": "object", "properties": {
        "check_id": {"type": "string"},
        "signal": {"type": "string", "enum": list(gates.SIGNALS)},
        "note": {"type": "string"},
    }, "required": ["check_id", "signal"]},
    **_PHASE)
@guarded("assess_response")
def assess_response_tool(args: Dict[str, Any], context) -> Dict[str, Any]:
    session = _session(context)
    course = _course(session, context)
    result = gates.assess_response(course, str(args.get("check_id") or ""), str(args.get("signal") or ""),
                                   str(args.get("note") or ""))
    _persist(session, course)
    lecture = result["lecture"]
    message = (f"Assessed. Comprehension {lecture.get('comprehension_pct')}% over {lecture.get('assessed')} "
               f"check(s).")
    if result["concluded"]:
        message += " The lecture concluded: write the transcript (if not yet) and assign the exercise."
    return envelope("assess_response", True, message, result)


@tool(name="skip_lecture", description=(
    "Skip the lecture for a lesson whose concept the diagnostic marks known (in strengths / anchors / "
    "background); the reason is recorded."),
    parameters={"type": "object", "properties": {
        "lesson_id": {"type": "string"}, "concept": {"type": "string"}, "reason": {"type": "string"},
    }, "required": ["lesson_id", "concept", "reason"]},
    **_PHASE)
@guarded("skip_lecture")
def skip_lecture_tool(args: Dict[str, Any], context) -> Dict[str, Any]:
    session = _session(context)
    course = _course(session, context)
    lesson = gates.skip_lecture(course, _storage.slugify(str(args.get("lesson_id") or "")),
                                concept=str(args.get("concept") or ""), reason=str(args.get("reason") or ""))
    _persist(session, course)
    return envelope("skip_lecture", True, f"Lecture skipped for {lesson.lesson_id}.", lesson.known_concept_skip)


@tool(name="write_lecture_transcript", description=(
    "Write the lesson's lecture transcript (lessons/<id>/lecture.md): the end-to-end explanation the learner "
    "reads, referencing exercise files by relative path. Required before assign_exercise."),
    parameters=schemas.WRITE_LECTURE_TRANSCRIPT, **{**_PHASE, "execution_kind": "mutate"})
@guarded("write_lecture_transcript")
def write_lecture_transcript_tool(args: Dict[str, Any], context) -> Dict[str, Any]:
    session = _session(context)
    course = _course(session, context)
    lesson = _lesson(course, args.get("lesson_id"))
    if lesson.status == LESSON_PENDING:
        raise GateError("Start the lesson before writing its transcript.", code="wrong_status")
    path = write_lecture_transcript(course, lesson.lesson_id, str(args.get("content") or ""))
    _persist(session, course)
    return envelope("write_lecture_transcript", True, f"Transcript written: {path}",
                    {"lesson_id": lesson.lesson_id, "path": path,
                     "lecture_transcript_path": lesson.lecture_transcript_path})


@tool(name="register_exercise_file", description=(
    "Write an illustrative example file for a lesson (lessons/<id>/exercises/<path>); not graded."),
    parameters=schemas.REGISTER_EXERCISE_FILE, **{**_PHASE, "execution_kind": "mutate"})
@guarded("register_exercise_file")
def register_exercise_file_tool(args: Dict[str, Any], context) -> Dict[str, Any]:
    session = _session(context)
    course = _course(session, context)
    lesson = _lesson(course, args.get("lesson_id"))
    if lesson.status == LESSON_PENDING:
        raise GateError("Start the lesson before adding exercise files.", code="wrong_status")
    path = register_exercise_file(course, lesson.lesson_id, str(args.get("path") or ""), str(args.get("content") or ""))
    _persist(session, course)
    return envelope("register_exercise_file", True, f"Exercise file written: {path}",
                    {"lesson_id": lesson.lesson_id, "path": path, "exercise_file_paths": lesson.exercise_file_paths})


@tool(name="teach_sources", description=(
    "Attach sources you have READ (research-ledger citation numbers from url_grounding / research_fetch) to "
    "a lesson; cite them in the transcript as [^n]."),
    parameters={"type": "object", "properties": {
        "lesson_id": {"type": "string"}, "citation_ids": {"type": "array", "items": {"type": "integer"}},
    }, "required": ["lesson_id", "citation_ids"]},
    **_PHASE)
@guarded("teach_sources")
def teach_sources_tool(args: Dict[str, Any], context) -> Dict[str, Any]:
    session = _session(context)
    course = _course(session, context)
    research = getattr(session.session_manager, "research", None)
    lesson = gates.teach_sources(course, _storage.slugify(str(args.get("lesson_id") or "")),
                                 [int(c) for c in args.get("citation_ids") or []], research)
    _persist(session, course)
    return envelope("teach_sources", True, f"{lesson.lesson_id} cites {lesson.citations}.",
                    {"citations": lesson.citations, "sources": lesson.sources})


# ============================================================ assignments


@tool(name="assign_exercise", description=(
    "Create the lesson's assignment. Refused until the lecture concluded (or was skipped for a known concept) "
    "and the transcript exists. Code verifiers run against the starting files first and must fail there. "
    "For a due review, pass review_id."),
    parameters=schemas.ASSIGN_EXERCISE, **{**_PHASE, "requires_approval": True, "execution_kind": "mutate",
                                            "preview_policy": "optional"})
@guarded("assign_exercise")
def assign_exercise_tool(args: Dict[str, Any], context) -> Dict[str, Any]:
    session = _session(context)
    course = _course(session, context)
    lesson = _lesson(course, args.get("lesson_id"))
    review_id = str(args.get("review_id") or "").strip()
    review = next((r for r in course.scheduled_reviews if r.review_id == review_id), None) if review_id else None
    if review_id and review is None:
        raise GateError(f"No review {review_id}.", code="not_found")
    if review is None:
        gates.assign_gate(course, lesson)
    kind = str(args.get("kind") or "")
    raw = args.get("verification") or {}
    spec = VerificationSpec(
        method=str(raw.get("method") or "").strip() or _default_method(kind), verify_cmd=raw.get("verify_cmd"),
        expected_markers=[str(m) for m in raw.get("expected_markers") or []],
        forbidden_markers=[str(m) for m in raw.get("forbidden_markers") or []],
        expected_answer=raw.get("expected_answer"), case_sensitive=bool(raw.get("case_sensitive", False)),
        rubric_keywords=[str(k) for k in raw.get("rubric_keywords") or []],
        min_turns=int(raw.get("min_turns", 0) or 0),
        required_concepts=[str(c) for c in raw.get("required_concepts") or []],
        timeout_seconds=int(raw.get("timeout_seconds", 30) or 30),
        use_live_quiz_ui=bool(raw.get("use_live_quiz_ui", kind in {"multiple-choice", "fill-blank"})))
    objectives = [str(o) for o in args.get("objectives") or []]
    bad = [o for o in objectives if o not in lesson.learning_objectives]
    if bad:
        raise ValueError(f"objectives must be the lesson's own: {lesson.learning_objectives}")
    if review is not None and review.objective and not objectives:
        objectives = [review.objective]
    assignment_id = _storage.slugify(str(args.get("assignment_id") or
                                         f"{lesson.lesson_id}_a{len(lesson.assignment_ids) + 1}"))
    assignment = Assignment(
        assignment_id=assignment_id, lesson_id=lesson.lesson_id, kind=kind, prompt=str(args.get("prompt") or "").strip(),
        rubric=[RubricItem(criterion=str(i.get("criterion", "")).strip(), weight=int(i.get("weight", 1) or 1),
                           description=str(i.get("description", "")).strip()) for i in args.get("rubric") or []],
        verification=spec, pass_threshold=int(args.get("pass_threshold", 70) or 70), objectives=objectives,
        review_id=review_id)
    questions = args.get("quiz_questions") or []
    if questions:
        keys: Dict[str, Any] = {}
        for q in questions:
            qid = str(q.get("qid") or "")
            if not qid:
                continue
            if q.get("kind", "multiple_choice") == "multiple_choice":
                options, idx = q.get("options") or [], int(q.get("correct_index", -1) if q.get("correct_index") is not None else -1)
                keys[qid] = options[idx] if 0 <= idx < len(options) else ""
                keys[f"{qid}__method"] = "exact_match"
            else:
                keys[qid] = str(q.get("expected_pattern") or "")
                keys[f"{qid}__method"] = "regex_match" if q.get("expected_pattern") else "exact_match"
        assignment.submission = {"quiz_questions": questions, "quiz_keys": keys}
    assignment.artifact_paths = _write_artifacts(course, assignment, args.get("artifact_files") or [])
    gates.precheck_verifier(course, assignment, context)
    course.assignments.append(assignment)
    lesson.assignment_ids.append(assignment_id)
    course.current_assignment_id = assignment_id
    prompt_dir = os.path.dirname(gates.work_dir(course, assignment))
    os.makedirs(prompt_dir, exist_ok=True)
    with open(os.path.join(prompt_dir, "prompt.md"), "w", encoding="utf-8") as fh:
        fh.write(f"# Assignment {assignment_id}\n\n{assignment.prompt}\n")
    if review is None and lesson.status in (LESSON_PRESENTING, LESSON_LECTURING, LESSON_REMEDIATING):
        advance_lesson_status(course, lesson.lesson_id, LESSON_ASSIGNED)
    add_event(course, kind="assignment_created", entity="assignment", entity_id=assignment_id,
              payload={"kind": kind, "review": review_id, "precheck": assignment.precheck_run})
    launch = {"attempted": False, "launched": False, "reason": None}
    if kind in {"multiple-choice", "fill-blank"} and spec.use_live_quiz_ui and questions:
        launch["attempted"] = True
        ui = getattr(session, "ui", None)
        if ui is None or not hasattr(ui, "run_quiz"):
            launch["reason"] = "no UI with a quiz picker; ask the questions in chat"
        else:
            try:
                answers = ui.run_quiz(questions)
                if isinstance(answers, dict) and answers:
                    assignment.submission = {**(assignment.submission or {}), "answers": answers}
                    assignment.status = ASSIGNMENT_SUBMITTED
                    launch["launched"] = True
                else:
                    launch["reason"] = "the learner closed the quiz without answers"
            except Exception as exc:  # noqa: BLE001
                launch["reason"] = f"quiz UI failed: {exc}"
    _persist(session, course)
    return envelope("assign_exercise", True, f"Assignment {assignment_id} created.",
                    {"assignment_id": assignment_id, "lesson_id": lesson.lesson_id,
                     "artifact_paths": assignment.artifact_paths, "precheck_run": assignment.precheck_run,
                     "live_quiz_launch": launch})


@tool(name="submit_assignment", description="Record the learner's submission (answers, or a note that the work/ files are edited).",
      parameters=schemas.SUBMIT_ASSIGNMENT, **_PHASE)
@guarded("submit_assignment")
def submit_assignment_tool(args: Dict[str, Any], context) -> Dict[str, Any]:
    session = _session(context)
    course = _course(session, context)
    assignment = find_assignment(course, _storage.slugify(str(args.get("assignment_id") or "")))
    if assignment is None:
        raise GateError(f"Assignment {args.get('assignment_id')!r} not found.", code="not_found")
    assignment.submission = {**(assignment.submission or {}), **(args.get("submission") or {})}
    assignment.status = ASSIGNMENT_SUBMITTED
    add_event(course, kind="assignment_submitted", entity="assignment", entity_id=assignment.assignment_id,
              actor="user:chat")
    _persist(session, course)
    return envelope("submit_assignment", True, f"{assignment.assignment_id} submitted.",
                    {"assignment_id": assignment.assignment_id, "status": assignment.status})


@tool(name="grade_assignment", description=(
    "Grade a submitted assignment. Code kinds: the engine runs the verifier in work/. Prose kinds: after the "
    "keyword gate, give rubric_scores — a score per rubric criterion (weights apply); until then the grade is "
    "'awaiting judgement', not a fail."),
    parameters={"type": "object", "properties": {
        "assignment_id": {"type": "string"},
        "rubric_scores": {"type": "array", "items": {"type": "object", "properties": {
            "criterion": {"type": "string"}, "score_pct": {"type": "integer"}, "note": {"type": "string"}},
            "required": ["criterion", "score_pct"]}},
        "feedback": {"type": "string"},
    }, "required": ["assignment_id"]},
    **{**_PHASE, "requires_approval": True, "execution_kind": "mutate", "preview_policy": "optional"})
@guarded("grade_assignment")
def grade_assignment_tool(args: Dict[str, Any], context) -> Dict[str, Any]:
    session = _session(context)
    course = _course(session, context)
    assignment = find_assignment(course, _storage.slugify(str(args.get("assignment_id") or "")))
    if assignment is None:
        raise GateError(f"Assignment {args.get('assignment_id')!r} not found.", code="not_found")
    if assignment.kind == "socratic-dialog":
        raise GateError("Socratic dialogs are graded with close_dialog.", code="wrong_kind")
    if assignment.kind in gates.EXEC_KINDS:
        grade_obj = gates.grade_exec(course, assignment, context)
        assignment.grade = grade_obj
        assignment.status = "passed" if grade_obj.passed else "failed"
    else:
        grade_obj = grade_assignment_payload(assignment, submission=assignment.submission,
                                             feedback_override=str(args.get("feedback") or "") or None)
        stage = (grade_obj.verification_result or {}).get("stage")
        if stage == "awaiting_judgment":
            if args.get("rubric_scores"):
                if assignment.rubric:
                    grade_obj = gates.grade_rubric(assignment, args["rubric_scores"], str(args.get("feedback") or ""))
                else:
                    score = max(0, min(100, int(args["rubric_scores"][0].get("score_pct", 0))))
                    grade_obj.score_pct, grade_obj.passed = score, score >= assignment.pass_threshold
                    grade_obj.verification_result = {"method": "rubric_judge", "stage": "judged"}
                    grade_obj.feedback = str(args.get("feedback") or "") or grade_obj.feedback
                assignment.grade = grade_obj
                assignment.status = "passed" if grade_obj.passed else "failed"
            else:
                grade_obj.status = GRADE_AWAITING
                assignment.grade = grade_obj
                assignment.status = ASSIGNMENT_SUBMITTED
    lesson = find_lesson(course, assignment.lesson_id)
    awaiting = getattr(grade_obj, "status", "graded") == GRADE_AWAITING
    if not awaiting:
        if lesson is not None and lesson.status == LESSON_ASSIGNED and not assignment.review_id:
            advance_lesson_status(course, lesson.lesson_id, LESSON_GRADED)
        gates.schedule_after_grade(course, assignment)
    add_event(course, kind="assignment_graded", entity="assignment", entity_id=assignment.assignment_id,
              payload={"score_pct": grade_obj.score_pct, "passed": grade_obj.passed, "status": grade_obj.status})
    grade_path = os.path.join(os.path.dirname(gates.work_dir(course, assignment)), "grade.json")
    os.makedirs(os.path.dirname(grade_path), exist_ok=True)
    with open(grade_path, "w", encoding="utf-8") as fh:
        json.dump(asdict(grade_obj), fh, indent=2, default=str)
    _persist(session, course)
    if awaiting:
        return envelope("grade_assignment", True, "Keyword gate passed; awaiting your rubric judgement "
                        "(grade_assignment with rubric_scores).", {"grade": asdict(grade_obj)})
    return envelope("grade_assignment", True, f"{grade_obj.score_pct}% — {'passed' if grade_obj.passed else 'failed'}.",
                    {"assignment_id": assignment.assignment_id, "grade": asdict(grade_obj)})


@tool(name="decide_next", description=(
    "After a graded lesson: advance (only if the latest assignment passed) or remediate (re-teach, then a "
    "new exercise)."), parameters=schemas.DECIDE_NEXT, **_PHASE)
@guarded("decide_next")
def decide_next_tool(args: Dict[str, Any], context) -> Dict[str, Any]:
    session = _session(context)
    course = _course(session, context)
    lesson_id = _storage.slugify(str(args.get("lesson_id") or ""))
    action = str(args.get("action") or "")
    decide_next(course, lesson_id, action)
    if action == "remediate":
        lesson = find_lesson(course, lesson_id)
        lesson.lecture_concluded = False
    nxt = gates.next_lesson(course)
    if action == "advance":
        course.current_lesson_id = nxt.lesson_id if nxt else None
        course.current_module_id = nxt.module_id if nxt else None
    record = _persist(session, course)
    return envelope("decide_next", True, f"{action}: next lesson {course.current_lesson_id}.",
                    {"course": record, "next_lesson_id": course.current_lesson_id})


@tool(name="record_dialog_turn", description="Append one turn to a socratic-dialog assignment (agent question or learner answer).",
      parameters=schemas.RECORD_DIALOG_TURN, **_PHASE)
@guarded("record_dialog_turn")
def record_dialog_turn_tool(args: Dict[str, Any], context) -> Dict[str, Any]:
    session = _session(context)
    course = _course(session, context)
    turn = record_dialog_turn(course, _storage.slugify(str(args.get("assignment_id") or "")),
                              role=str(args.get("role") or ""), content=str(args.get("content") or ""),
                              quality_signal=str(args.get("quality_signal") or "") or None)
    _persist(session, course)
    return envelope("record_dialog_turn", True, "Turn recorded.", asdict(turn))


@tool(name="close_dialog", description=(
    "Finalize a socratic dialog with an honest mastery score, summary and gaps (min_turns and required "
    "concepts are enforced)."), parameters=schemas.CLOSE_DIALOG,
    **{**_PHASE, "requires_approval": True})
@guarded("close_dialog")
def close_dialog_tool(args: Dict[str, Any], context) -> Dict[str, Any]:
    session = _session(context)
    course = _course(session, context)
    assignment_id = _storage.slugify(str(args.get("assignment_id") or ""))
    grade_obj = close_socratic_dialog(course, assignment_id, mastery_pct=int(args.get("mastery_pct", 0)),
                                      summary=str(args.get("summary") or ""), gaps=[str(g) for g in args.get("gaps") or []])
    assignment = find_assignment(course, assignment_id)
    lesson = find_lesson(course, assignment.lesson_id) if assignment else None
    if lesson is not None and lesson.status == LESSON_ASSIGNED:
        advance_lesson_status(course, lesson.lesson_id, LESSON_GRADED)
    if assignment is not None:
        gates.schedule_after_grade(course, assignment)
    _persist(session, course)
    return envelope("close_dialog", True, f"Dialog closed at {grade_obj.score_pct}%.", {"grade": asdict(grade_obj)})


# ============================================================ state, modules, reviews


@tool(name="get_course_state", description=(
    "The course: status, current lesson and its lecture gate state, objective mastery, due and upcoming "
    "reviews."), parameters={"type": "object", "properties": {"course_id": {"type": "string"}}},
    **{**_PHASE, "execution_kind": "read"})
@guarded("get_course_state")
def get_course_state_tool(args: Dict[str, Any], context) -> Dict[str, Any]:
    session = _session(context)
    course = _course(session, context, args.get("course_id"))
    lesson = find_lesson(course, course.current_lesson_id) if course.current_lesson_id else None
    data = {"course_id": course.course_id, "status": course.status, "metrics": course_metrics(course),
            "current_lesson_id": course.current_lesson_id,
            "lecture": gates.lecture_state(course, lesson) if lesson else None,
            "mastery": gates.mastery_grid(course), "course_mastery": gates.course_mastery(course),
            "due_reviews": [asdict(r) for r in gates.due_reviews(course)],
            "upcoming_reviews": [asdict(r) for r in gates.upcoming_reviews(course)[:6]]}
    return envelope("get_course_state", True, gates.capsule(session), data)


@tool(name="complete_module", description=(
    "Complete a module (required before the next module's lessons start): every lesson completed and the "
    "module's objective mastery at or above its threshold."), parameters=schemas.COMPLETE_MODULE,
    **{**_PHASE, "requires_approval": True})
@guarded("complete_module")
def complete_module_tool(args: Dict[str, Any], context) -> Dict[str, Any]:
    session = _session(context)
    course = _course(session, context)
    mastery = gates.complete_module(course, _storage.slugify(str(args.get("module_id") or "")))
    _persist(session, course)
    return envelope("complete_module", True, f"Module completed at {mastery}% mastery.", {"mastery_pct": mastery})


@tool(name="finalize_course", description=(
    "Finish the course: every lesson passed (or the learner confirmed an early finish). Writes the report "
    "card and saves the learner model for future courses."),
    parameters={"type": "object", "properties": {"course_id": {"type": "string"}}},
    **{**_PHASE, "requires_approval": True, "execution_kind": "mutate"})
@guarded("finalize_course")
def finalize_course_tool(args: Dict[str, Any], context) -> Dict[str, Any]:
    session = _session(context)
    course = _course(session, context, args.get("course_id"))
    gates.finalize_gate(course)
    course.status = COURSE_COMPLETED
    add_event(course, kind="course_completed", entity="course", entity_id=course.course_id,
              payload={"mastery_pct": gates.course_mastery(course), "early_exit": bool(course.early_exit)})
    report = _write_report_card(course)
    gates.remember_profile(session, course, f"Completed {course.subject} at {gates.course_mastery(course) or 0}% mastery.")
    record = _persist(session, course)
    return envelope("finalize_course", True, f"Course finished; report card at {report}.",
                    {"course": record, "report_card_path": report})


@tool(name="get_due_reviews", description="Spaced reviews that are due now (by date), oldest first.",
      parameters={"type": "object", "properties": {}}, **{**_PHASE, "execution_kind": "read"})
@guarded("get_due_reviews")
def get_due_reviews_tool(args: Dict[str, Any], context) -> Dict[str, Any]:
    session = _session(context)
    course = _course(session, context)
    due = [asdict(r) for r in gates.due_reviews(course)]
    return envelope("get_due_reviews", True, f"{len(due)} review(s) due.", {"due_reviews": due})


@tool(name="complete_review", description=(
    "Close a due review with the graded review exercise (assign_exercise(review_id=...), then grade it); the "
    "grade updates mastery and schedules the next review."),
    parameters={"type": "object", "properties": {"review_id": {"type": "string"}, "assignment_id": {"type": "string"}},
                "required": ["review_id", "assignment_id"]}, **_PHASE)
@guarded("complete_review")
def complete_review_tool(args: Dict[str, Any], context) -> Dict[str, Any]:
    session = _session(context)
    course = _course(session, context)
    review = gates.complete_review(course, str(args.get("review_id") or ""),
                                   assignment_id=_storage.slugify(str(args.get("assignment_id") or "")))
    _persist(session, course)
    nxt = course.scheduled_reviews[-1]
    return envelope("complete_review", True, f"Review {review.review_id} done at {review.score_pct}%; next due in "
                    f"{nxt.interval_days:g} day(s).", {"review": asdict(review), "next": asdict(nxt)})


@register_preview("grade_assignment")
def _preview_grade(args, session):
    path = gates.active_course_path(session)
    from mu.teacher.engine import read_course

    course = read_course(path)
    assignment = find_assignment(course, _storage.slugify(str(args.get("assignment_id") or ""))) if course else None
    if assignment is not None and assignment.verification.verify_cmd and assignment.kind in gates.EXEC_KINDS:
        return {"will_run": assignment.verification.verify_cmd, "in": gates.work_dir(course, assignment)}
    return {}


@register_preview("assign_exercise")
def _preview_assign(args, session):
    cmd = (args.get("verification") or {}).get("verify_cmd")
    return {"will_run": f"{cmd} (on the starting files; it must fail there)"} if cmd else {}


__all__ = [
    "assess_response_tool", "assign_exercise_tool", "close_dialog_tool", "complete_module_tool",
    "complete_review_tool", "create_course_tool", "decide_next_tool", "finalize_course_tool",
    "get_course_state_tool", "get_due_reviews_tool", "grade_assignment_tool", "propose_curriculum_tool",
    "record_diagnostic_tool", "record_dialog_turn_tool", "register_exercise_file_tool", "skip_lecture_tool",
    "start_lesson_tool", "submit_assignment_tool", "teach_check_tool", "teach_sources_tool",
    "update_learner_profile_tool", "write_lecture_transcript_tool",
]
