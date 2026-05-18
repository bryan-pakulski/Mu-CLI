"""Teacher-mode `@tool` handlers.

The engine (`mu.teacher.engine`) is UI-free; these handlers perform the
session sync + side effects (writing artifact files, launching the
optional live quiz UI, persisting graded artifacts to disk).

Each handler returns a JSON string envelope so the agent gets a
structured result. State mutations save via
`session.session_manager.upsert_teacher_course()` then
`session.session_manager.save_history()` so courses survive across
session restarts.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from typing import Any

from mu.teacher import storage as _storage
from mu.teacher.engine import (
    ASSIGNMENT_PRESENTED,
    ASSIGNMENT_SUBMITTED,
    COURSE_COMPLETED,
    COURSE_CURRICULUM_PROPOSED,
    COURSE_DIAGNOSING,
    COURSE_IN_PROGRESS,
    LESSON_ASSIGNED,
    LESSON_COMPLETED,
    LESSON_GRADED,
    LESSON_LECTURING,
    LESSON_PENDING,
    LESSON_PRESENTING,
    Assignment,
    Course,
    Lesson,
    Module,
    RubricItem,
    VerificationSpec,
    add_event,
    advance_lesson_status,
    close_socratic_dialog,
    conclude_lecture,
    course_metrics,
    create_course,
    decide_next,
    find_assignment,
    find_lesson,
    find_module,
    load_course,
    next_pending_lesson,
    record_dialog_turn,
    record_lecture_turn,
    save_course,
    start_lecture,
)
from mu.teacher.grading import grade as grade_assignment_payload
from mu.tools import tool


# ----------------------------------------------------------- helpers


def _ok(payload: dict[str, Any]) -> str:
    return json.dumps({"ok": True, **payload}, indent=2, sort_keys=True, default=str)


def _err(message: str, **extra: Any) -> str:
    return json.dumps(
        {"ok": False, "error": message, **extra}, indent=2, sort_keys=True, default=str
    )


def _not_found_err(kind: str, requested: str, known: list[str]) -> str:
    """Friendly not-found error that lists the IDs that DO exist so the
    agent can self-correct instead of concluding state is lost."""
    sample = ", ".join(f"`{k}`" for k in known[:10]) if known else "(none)"
    hint = (
        f"{kind} {requested!r} not found. "
        f"Known {kind}s on this course: {sample}. "
        "If you just created one, use the id returned by the create call "
        "verbatim — do not re-slugify it."
    )
    return _err(hint, known_ids=known)


def _session_from_context(context: Any):
    session = getattr(context, "session", None)
    if session is None:
        raise RuntimeError("teacher tool requires an active session in the context")
    return session


def _folder_context(context: Any):
    fc = getattr(context, "folder_context", None)
    if fc is not None:
        return fc
    session = getattr(context, "session", None)
    return getattr(session, "folder_context", None) if session else None


def _persist(session, course: Course) -> dict[str, Any]:
    """Save course state to disk AND mirror summary into session registry."""
    save_course(course)
    fc = getattr(session, "folder_context", None)
    record = _summary_record(course, fc)
    session.session_manager.upsert_teacher_course(record)
    if session.session_manager.active_course_id == course.course_id:
        session.session_manager.teacher_state = dict(record)
    session.session_manager.save_history(fc)
    return record


def _summary_record(course: Course, folder_context: Any) -> dict[str, Any]:
    metrics = course_metrics(course)
    state_path = _storage.course_state_path(course.course_id, folder_context)
    return {
        "type": "course",
        "course_id": course.course_id,
        "subject": course.subject,
        "directory": course.directory,
        "course_path": state_path,
        "status": course.status,
        "metrics": metrics,
        "updated_at": course.updated_at,
        "created_at": course.created_at,
    }


def _load_active_course(session, context: Any, course_id: str | None = None) -> Course:
    """Find and load the active course, trying every persistence layer.

    Resolution order:
      1. Caller-supplied `course_id` arg (slugified).
      2. SessionManager.active_course_id.
      3. SessionManager.teacher_state['course_id'] (set by /teach load).

    Once a course_id is in hand we try paths in this order so old layouts
    keep working: session_manager.get_course(...)['course_path'],
    storage.course_state_path(workspace), then a direct on-disk search.
    """
    fc = _folder_context(context)
    target_id = course_id
    if target_id:
        target_id = _storage.slugify(str(target_id))
    if not target_id:
        target_id = session.session_manager.active_course_id
    if not target_id:
        state = session.session_manager.get_teacher_state() or {}
        target_id = state.get("course_id")
    if not target_id:
        raise ValueError(
            "no active course — call create_course (for a new course) or "
            "tell the user to run '/teach load <course_id>' to resume one."
        )

    # Candidate paths to try, in order.
    candidates: list[str] = []
    record = session.session_manager.get_course(target_id) or {}
    persisted = str(record.get("course_path") or "").strip()
    if persisted:
        candidates.append(persisted)
    candidates.append(_storage.course_state_path(target_id, fc))

    for path in candidates:
        if path and os.path.exists(path):
            course = load_course(path, fc)
            if course is not None:
                return course

    # Final fallback: load_course with just the id will also recompute
    # the path. If it returns None, we genuinely can't find it.
    course = load_course(target_id, fc)
    if course is not None:
        return course

    # If a course_id was supplied but didn't resolve, surface the active
    # course (if any) so the agent can recover without thinking state was
    # lost.
    active = session.session_manager.active_course_id
    if course_id and active and active != target_id:
        raise ValueError(
            f"course {target_id!r} not found, but active course is "
            f"{active!r}. Drop the course_id arg or pass course_id={active!r} "
            "instead of guessing from the subject."
        )

    tried = "\n  ".join(candidates) if candidates else "(none)"
    raise ValueError(
        f"could not load course {target_id!r}. Tried:\n  {tried}\n"
        "If you have an existing course directory, move it under "
        "<workspace>/courses/<course_id>/ or run /teach load with the "
        "correct id."
    )


def _activate(session, course: Course, folder_context: Any) -> None:
    record = _summary_record(course, folder_context)
    session.session_manager.upsert_teacher_course(record)
    session.session_manager.active_course_id = course.course_id
    session.session_manager.teacher_state = dict(record)
    session.session_manager.save_history(folder_context)


def _write_assignment_artifacts(
    course: Course,
    assignment: Assignment,
    artifact_files: list[dict[str, str]],
) -> list[str]:
    """Write engine-supplied artifact files into the assignment's work/ dir.

    Each entry is `{"path": "lesson_03.pl", "content": "..."}`. Absolute
    paths are rejected — artifacts live inside `work/`.
    """
    written: list[str] = []
    if not artifact_files:
        return written
    work_dir = os.path.join(
        course.directory,
        "assignments",
        _storage.slugify(assignment.assignment_id),
        "work",
    )
    os.makedirs(work_dir, exist_ok=True)
    for entry in artifact_files:
        rel = str(entry.get("path", "") or "").strip()
        if not rel or rel.startswith("/") or ".." in rel.split(os.sep):
            continue
        target = os.path.join(work_dir, rel)
        os.makedirs(os.path.dirname(target) or work_dir, exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(str(entry.get("content", "") or ""))
        written.append(target)
    return written


def _write_curriculum_md(course: Course) -> None:
    if not course.directory:
        return
    path = os.path.join(course.directory, "curriculum.md")
    lines = [f"# {course.subject} — Curriculum", ""]
    lines.append(f"- **Target level**: {course.target_level}")
    lines.append(f"- **Status**: {course.status}")
    lines.append("")
    ordered = sorted(course.modules, key=lambda m: m.order)
    for module in ordered:
        lines.append(f"## Module {module.order}: {module.title}")
        if module.goal:
            lines.append(f"_Goal_: {module.goal}")
        lines.append("")
        for lesson_id in module.lesson_ids:
            lesson = find_lesson(course, lesson_id)
            if lesson is None:
                continue
            lines.append(f"- **{lesson.title}** ({lesson.status})")
            for objective in lesson.learning_objectives:
                lines.append(f"  - {objective}")
        lines.append("")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def _write_report_card(course: Course) -> str:
    path = os.path.join(course.directory, "report_card.md")
    metrics = course_metrics(course)
    lines = [
        f"# Report Card — {course.subject}",
        "",
        f"- **Course ID**: `{course.course_id}`",
        f"- **Lessons completed**: {metrics['lessons_completed']} / {metrics['total_lessons']}",
        f"- **Overall progress**: {metrics['overall_pct']}%",
        f"- **Average graded score**: {metrics['average_score_pct']}%",
        "",
        "## Per-lesson detail",
        "",
    ]
    ordered = sorted(course.modules, key=lambda m: m.order)
    for module in ordered:
        lines.append(f"### {module.title}")
        for lesson_id in module.lesson_ids:
            lesson = find_lesson(course, lesson_id)
            if lesson is None:
                continue
            assignments = [
                a for a in course.assignments if a.lesson_id == lesson.lesson_id
            ]
            best = None
            for a in assignments:
                if a.grade is None:
                    continue
                if best is None or a.grade.score_pct > best.grade.score_pct:
                    best = a
            score = best.grade.score_pct if best and best.grade else 0
            lines.append(
                f"- {lesson.title}: status `{lesson.status}`, best score {score}%"
            )
        lines.append("")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    return path


# ============================================================ tool handlers
# ----- course lifecycle ------------------------------------------------


@tool(
    name="create_course",
    description=(
        "Open a new teacher-mode course. Returns the course record. "
        "The course starts in 'diagnosing' state — call record_diagnostic "
        "after probing the learner."
    ),
    parameters={
        "type": "object",
        "properties": {
            "subject": {"type": "string", "description": "Subject to teach, e.g. 'Perl', 'Kubernetes'."},
            "target_level": {
                "type": "string",
                "enum": ["beginner", "intermediate", "advanced"],
                "default": "beginner",
            },
            "learner_summary": {
                "type": "string",
                "description": "One-line summary of what the learner said when starting.",
            },
            "course_id": {"type": "string", "description": "Optional explicit slug."},
        },
        "required": ["subject"],
    },
    requires_approval=False,
)
def create_course_tool(args: dict[str, Any], context) -> str:
    try:
        session = _session_from_context(context)
        fc = _folder_context(context)
        course = create_course(
            subject=str(args.get("subject", "")).strip(),
            target_level=str(args.get("target_level", "beginner") or "beginner").strip(),
            learner_summary=str(args.get("learner_summary", "") or "").strip(),
            folder_context=fc,
            course_id=(str(args.get("course_id", "")).strip() or None),
        )
        _activate(session, course, fc)
        return _ok({"course": _summary_record(course, fc)})
    except Exception as exc:
        return _err(str(exc))


@tool(
    name="record_diagnostic",
    description=(
        "Save the learner's diagnostic profile (prior experience, gaps, "
        "goals) after a short Q&A. Required before propose_curriculum."
    ),
    parameters={
        "type": "object",
        "properties": {
            "course_id": {"type": "string"},
            "strengths": {"type": "array", "items": {"type": "string"}},
            "gaps": {"type": "array", "items": {"type": "string"}},
            "goals": {"type": "array", "items": {"type": "string"}},
            "notes": {"type": "string"},
        },
    },
    requires_approval=False,
)
def record_diagnostic_tool(args: dict[str, Any], context) -> str:
    try:
        session = _session_from_context(context)
        course = _load_active_course(session, context, args.get("course_id"))
        course.learner_profile = {
            "strengths": [str(x) for x in (args.get("strengths") or [])],
            "gaps": [str(x) for x in (args.get("gaps") or [])],
            "goals": [str(x) for x in (args.get("goals") or [])],
            "notes": str(args.get("notes", "") or ""),
            "recorded_at": time.time(),
        }
        add_event(
            course,
            kind="diagnostic_recorded",
            entity="course",
            entity_id=course.course_id,
            payload=dict(course.learner_profile),
        )
        record = _persist(session, course)
        return _ok({"course": record, "learner_profile": course.learner_profile})
    except Exception as exc:
        return _err(str(exc))


@tool(
    name="propose_curriculum",
    description=(
        "Replace the course's modules + lessons with a proposed curriculum. "
        "Sets course status to 'curriculum_proposed'. The learner must call "
        "approve_curriculum before the lesson loop can begin."
    ),
    parameters={
        "type": "object",
        "properties": {
            "course_id": {"type": "string"},
            "modules": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "module_id": {"type": "string"},
                        "title": {"type": "string"},
                        "goal": {"type": "string"},
                        "order": {"type": "integer"},
                        "mastery_threshold": {"type": "integer", "default": 70},
                        "lessons": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "lesson_id": {"type": "string"},
                                    "title": {"type": "string"},
                                    "learning_objectives": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "concept_brief": {"type": "string"},
                                },
                                "required": ["lesson_id", "title"],
                            },
                        },
                    },
                    "required": ["module_id", "title", "lessons"],
                },
            },
        },
        "required": ["modules"],
    },
    requires_approval=False,
)
def propose_curriculum_tool(args: dict[str, Any], context) -> str:
    try:
        session = _session_from_context(context)
        course = _load_active_course(session, context, args.get("course_id"))
        course.modules = []
        course.lessons = []
        for idx, raw_module in enumerate(args.get("modules") or [], start=1):
            module_id = _storage.slugify(str(raw_module.get("module_id") or f"module_{idx}"))
            module = Module(
                module_id=module_id,
                title=str(raw_module.get("title") or module_id),
                goal=str(raw_module.get("goal", "") or ""),
                order=int(raw_module.get("order", idx) or idx),
                mastery_threshold=int(raw_module.get("mastery_threshold", 70) or 70),
            )
            course.modules.append(module)
            for raw_lesson in raw_module.get("lessons") or []:
                lesson_id = _storage.slugify(
                    str(raw_lesson.get("lesson_id") or raw_lesson.get("title") or "")
                )
                if not lesson_id:
                    continue
                lesson = Lesson(
                    lesson_id=lesson_id,
                    module_id=module_id,
                    title=str(raw_lesson.get("title") or lesson_id),
                    learning_objectives=[
                        str(x) for x in (raw_lesson.get("learning_objectives") or [])
                    ],
                    concept_brief=str(raw_lesson.get("concept_brief", "") or ""),
                )
                course.lessons.append(lesson)
                module.lesson_ids.append(lesson_id)
        course.status = COURSE_CURRICULUM_PROPOSED
        add_event(
            course,
            kind="curriculum_proposed",
            entity="course",
            entity_id=course.course_id,
            payload={"modules": len(course.modules), "lessons": len(course.lessons)},
        )
        _write_curriculum_md(course)
        record = _persist(session, course)
        return _ok(
            {
                "course": record,
                "module_count": len(course.modules),
                "lesson_count": len(course.lessons),
                "next_step": "ask the learner to call `/teach approve` or run approve_curriculum",
            }
        )
    except Exception as exc:
        return _err(str(exc))


@tool(
    name="approve_curriculum",
    description=(
        "Mark the proposed curriculum as approved by the learner. Flips the "
        "course to 'in_progress' and unlocks the lesson loop. Refuses unless "
        "the course is currently in 'curriculum_proposed'."
    ),
    parameters={
        "type": "object",
        "properties": {"course_id": {"type": "string"}},
    },
    requires_approval=True,
)
def approve_curriculum_tool(args: dict[str, Any], context) -> str:
    try:
        session = _session_from_context(context)
        course = _load_active_course(session, context, args.get("course_id"))
        if course.status != COURSE_CURRICULUM_PROPOSED:
            return _err(
                f"approve_curriculum refused: course status is "
                f"{course.status!r}, expected 'curriculum_proposed'"
            )
        course.status = COURSE_IN_PROGRESS
        first = next_pending_lesson(course)
        if first is not None:
            course.current_module_id = first.module_id
            course.current_lesson_id = first.lesson_id
        add_event(
            course,
            kind="curriculum_approved",
            entity="course",
            entity_id=course.course_id,
            payload={},
            actor="learner",
        )
        record = _persist(session, course)
        return _ok({"course": record, "first_lesson_id": first.lesson_id if first else None})
    except Exception as exc:
        return _err(str(exc))


# ----- lesson loop -----------------------------------------------------


@tool(
    name="start_lesson",
    description=(
        "Begin a lesson — flips its status from 'pending' to 'presenting' "
        "and marks it the current lesson on the course."
    ),
    parameters={
        "type": "object",
        "properties": {
            "course_id": {"type": "string"},
            "lesson_id": {"type": "string"},
        },
        "required": ["lesson_id"],
    },
    requires_approval=False,
)
def start_lesson_tool(args: dict[str, Any], context) -> str:
    try:
        session = _session_from_context(context)
        course = _load_active_course(session, context, args.get("course_id"))
        lesson_id = _storage.slugify(str(args.get("lesson_id", "")).strip())
        lesson = find_lesson(course, lesson_id)
        if lesson is None:
            return _not_found_err("lesson", lesson_id, [l.lesson_id for l in course.lessons])
        advance_lesson_status(course, lesson_id, LESSON_PRESENTING)
        course.current_module_id = lesson.module_id
        course.current_lesson_id = lesson.lesson_id
        record = _persist(session, course)
        return _ok({"course": record, "lesson": asdict(lesson)})
    except Exception as exc:
        return _err(str(exc))


@tool(
    name="present_concept",
    description=(
        "Record the agent's ≤3-sentence concept brief for the active lesson "
        "and advance lesson status from 'presenting' to 'assigned'-ready. "
        "Pair with assign_exercise next."
    ),
    parameters={
        "type": "object",
        "properties": {
            "lesson_id": {"type": "string"},
            "brief": {"type": "string"},
        },
        "required": ["lesson_id", "brief"],
    },
    requires_approval=False,
)
def present_concept_tool(args: dict[str, Any], context) -> str:
    try:
        session = _session_from_context(context)
        course = _load_active_course(session, context)
        lesson_id = _storage.slugify(str(args.get("lesson_id", "")).strip())
        lesson = find_lesson(course, lesson_id)
        if lesson is None:
            return _not_found_err("lesson", lesson_id, [l.lesson_id for l in course.lessons])
        lesson.concept_brief = str(args.get("brief", "")).strip()
        add_event(
            course,
            kind="concept_presented",
            entity="lesson",
            entity_id=lesson.lesson_id,
            payload={"length": len(lesson.concept_brief)},
        )
        _persist(session, course)
        return _ok({"lesson": asdict(lesson)})
    except Exception as exc:
        return _err(str(exc))


# ----- lecture phase ----------------------------------------------------


@tool(
    name="start_lecture",
    description=(
        "Begin the back-and-forth lecture phase for the active lesson. "
        "Optionally include a `plan` string outlining what you'll cover. "
        "Follow up with record_lecture_turn for each agent_explanation, "
        "agent_check, and learner_response. Conclude with conclude_lecture."
    ),
    parameters={
        "type": "object",
        "properties": {
            "lesson_id": {"type": "string"},
            "plan": {
                "type": "string",
                "description": "Optional outline of the topics you'll cover.",
            },
            "min_lecture_checks": {
                "type": "integer",
                "description": (
                    "Minimum `agent_check` turns required before "
                    "conclude_lecture is allowed. Defaults to 2."
                ),
            },
            "lecture_comprehension_threshold": {
                "type": "integer",
                "description": (
                    "Minimum comprehension_pct required to advance to "
                    "the assignment phase. Defaults to 60."
                ),
            },
        },
        "required": ["lesson_id"],
    },
    requires_approval=False,
)
def start_lecture_tool(args: dict[str, Any], context) -> str:
    try:
        session = _session_from_context(context)
        course = _load_active_course(session, context)
        lesson_id = _storage.slugify(str(args.get("lesson_id", "")).strip())
        lesson = find_lesson(course, lesson_id)
        if lesson is None:
            return _not_found_err("lesson", lesson_id, [l.lesson_id for l in course.lessons])
        if "min_lecture_checks" in args:
            lesson.min_lecture_checks = max(0, int(args["min_lecture_checks"]))
        if "lecture_comprehension_threshold" in args:
            lesson.lecture_comprehension_threshold = max(
                0, min(100, int(args["lecture_comprehension_threshold"]))
            )
        start_lecture(course, lesson_id, plan=str(args.get("plan", "") or ""))
        _persist(session, course)
        return _ok({"lesson": asdict(find_lesson(course, lesson_id))})
    except Exception as exc:
        return _err(str(exc))


@tool(
    name="record_lecture_turn",
    description=(
        "Append one turn (agent_explanation | agent_check | learner_response) "
        "to the active lesson's lecture. Use `agent_explanation` when "
        "you're presenting material, `agent_check` when you pause to ask a "
        "comprehension question, and `learner_response` for what the "
        "learner said. The engine counts `agent_check` turns against "
        "`min_lecture_checks` so monologuing isn't allowed."
    ),
    parameters={
        "type": "object",
        "properties": {
            "lesson_id": {"type": "string"},
            "role": {
                "type": "string",
                "enum": ["agent_explanation", "agent_check", "learner_response"],
            },
            "content": {"type": "string"},
            "comprehension_signal": {
                "type": "string",
                "description": (
                    "Optional per-turn note: 'on track' | 'confused' | "
                    "'partial' | etc. Influences gap analysis."
                ),
            },
        },
        "required": ["lesson_id", "role", "content"],
    },
    requires_approval=False,
)
def record_lecture_turn_tool(args: dict[str, Any], context) -> str:
    try:
        session = _session_from_context(context)
        course = _load_active_course(session, context)
        turn = record_lecture_turn(
            course,
            _storage.slugify(str(args.get("lesson_id", "")).strip()),
            role=str(args.get("role", "")).strip(),
            content=str(args.get("content", "")).strip(),
            comprehension_signal=(
                str(args.get("comprehension_signal")).strip()
                if args.get("comprehension_signal")
                else None
            ),
        )
        _persist(session, course)
        return _ok({"turn": asdict(turn)})
    except Exception as exc:
        return _err(str(exc))


@tool(
    name="conclude_lecture",
    description=(
        "Close out the lecture phase with an honest comprehension score "
        "and a list of gaps. Refuses unless the lesson has met its "
        "min_lecture_checks and comprehension_pct >= the configured "
        "threshold. Set ready_for_assignment=false to keep lecturing "
        "without advancing (e.g. comprehension was weak; you want "
        "another pass)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "lesson_id": {"type": "string"},
            "comprehension_pct": {"type": "integer"},
            "summary": {"type": "string"},
            "gaps": {"type": "array", "items": {"type": "string"}},
            "ready_for_assignment": {"type": "boolean", "default": True},
        },
        "required": ["lesson_id", "comprehension_pct"],
    },
    requires_approval=True,
)
def conclude_lecture_tool(args: dict[str, Any], context) -> str:
    try:
        session = _session_from_context(context)
        course = _load_active_course(session, context)
        lesson_id = _storage.slugify(str(args.get("lesson_id", "")).strip())
        lesson = conclude_lecture(
            course,
            lesson_id,
            comprehension_pct=int(args.get("comprehension_pct", 0)),
            summary=str(args.get("summary", "") or ""),
            gaps=[str(g) for g in (args.get("gaps") or [])],
            ready_for_assignment=bool(args.get("ready_for_assignment", True)),
        )
        _persist(session, course)
        return _ok(
            {
                "lesson_id": lesson_id,
                "status": lesson.status,
                "comprehension_pct": lesson.lecture_comprehension_pct,
                "gaps": lesson.lecture_gaps,
                "ready_for_assignment": lesson.status == LESSON_ASSIGNED,
            }
        )
    except Exception as exc:
        return _err(str(exc))


@tool(
    name="assign_exercise",
    description=(
        "Create an assignment for the active lesson. The engine writes any "
        "artifact_files into the assignment's work/ directory and persists "
        "the verification spec. For socratic-dialog kinds, supply min_turns "
        "and required_concepts inside verification; for code kinds, supply "
        "verify_cmd + expected_markers."
    ),
    parameters={
        "type": "object",
        "properties": {
            "lesson_id": {"type": "string"},
            "assignment_id": {"type": "string"},
            "kind": {
                "type": "string",
                "enum": [
                    "fix-broken-code",
                    "implement-from-scratch",
                    "predict-output",
                    "multiple-choice",
                    "fill-blank",
                    "command-output",
                    "short-answer",
                    "explain-trace",
                    "socratic-dialog",
                ],
            },
            "prompt": {"type": "string"},
            "rubric": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "criterion": {"type": "string"},
                        "weight": {"type": "integer"},
                        "description": {"type": "string"},
                    },
                    "required": ["criterion"],
                },
            },
            "verification": {
                "type": "object",
                "properties": {
                    "method": {"type": "string"},
                    "verify_cmd": {"type": "string"},
                    "expected_markers": {"type": "array", "items": {"type": "string"}},
                    "forbidden_markers": {"type": "array", "items": {"type": "string"}},
                    "expected_answer": {"type": "string"},
                    "case_sensitive": {"type": "boolean"},
                    "rubric_keywords": {"type": "array", "items": {"type": "string"}},
                    "min_turns": {"type": "integer"},
                    "required_concepts": {"type": "array", "items": {"type": "string"}},
                    "timeout_seconds": {"type": "integer"},
                    "working_dir": {"type": "string"},
                    "use_live_quiz_ui": {"type": "boolean"},
                },
            },
            "artifact_files": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path"],
                },
            },
            "pass_threshold": {"type": "integer", "default": 70},
            "quiz_questions": {
                "type": "array",
                "description": (
                    "For multiple-choice / fill-blank assignments: the "
                    "per-question payload that drives the live quiz UI."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "qid": {"type": "string"},
                        "prompt": {"type": "string"},
                        "kind": {
                            "type": "string",
                            "enum": ["multiple_choice", "fill_blank"],
                        },
                        "options": {"type": "array", "items": {"type": "string"}},
                        "correct_index": {"type": "integer"},
                        "expected_pattern": {"type": "string"},
                        "case_sensitive": {"type": "boolean"},
                        "explanation": {"type": "string"},
                    },
                    "required": ["qid", "prompt", "kind"],
                },
            },
        },
        "required": ["lesson_id", "kind", "prompt"],
    },
    requires_approval=False,
)
def assign_exercise_tool(args: dict[str, Any], context) -> str:
    try:
        session = _session_from_context(context)
        course = _load_active_course(session, context)
        lesson_id = _storage.slugify(str(args.get("lesson_id", "")).strip())
        lesson = find_lesson(course, lesson_id)
        if lesson is None:
            return _not_found_err("lesson", lesson_id, [l.lesson_id for l in course.lessons])
        assignment_id = _storage.slugify(
            str(args.get("assignment_id", "") or f"{lesson_id}_a{len(lesson.assignment_ids) + 1}")
        )
        verification_raw = args.get("verification") or {}
        rubric = [
            RubricItem(
                criterion=str(item.get("criterion", "")).strip(),
                weight=int(item.get("weight", 1) or 1),
                description=str(item.get("description", "")).strip(),
            )
            for item in (args.get("rubric") or [])
        ]
        spec = VerificationSpec(
            method=str(verification_raw.get("method", "")).strip() or _default_method(args["kind"]),
            verify_cmd=verification_raw.get("verify_cmd"),
            expected_markers=[str(m) for m in (verification_raw.get("expected_markers") or [])],
            forbidden_markers=[str(m) for m in (verification_raw.get("forbidden_markers") or [])],
            expected_answer=verification_raw.get("expected_answer"),
            case_sensitive=bool(verification_raw.get("case_sensitive", False)),
            rubric_keywords=[str(k) for k in (verification_raw.get("rubric_keywords") or [])],
            min_turns=int(verification_raw.get("min_turns", 0) or 0),
            required_concepts=[
                str(c) for c in (verification_raw.get("required_concepts") or [])
            ],
            timeout_seconds=int(verification_raw.get("timeout_seconds", 30) or 30),
            working_dir=str(verification_raw.get("working_dir", "") or ""),
            use_live_quiz_ui=bool(verification_raw.get("use_live_quiz_ui", False)),
        )
        # Default to live quiz UI for MC/fill-blank when the agent didn't opt out.
        if args["kind"] in {"multiple-choice", "fill-blank"} and "use_live_quiz_ui" not in verification_raw:
            spec.use_live_quiz_ui = True

        assignment = Assignment(
            assignment_id=assignment_id,
            lesson_id=lesson_id,
            kind=str(args["kind"]),
            prompt=str(args.get("prompt", "")).strip(),
            rubric=rubric,
            verification=spec,
            pass_threshold=int(args.get("pass_threshold", 70) or 70),
        )
        # Persist quiz questions onto the assignment submission scaffold so
        # grade_assignment can later compute correctness without re-receiving
        # them from the agent.
        quiz_questions = args.get("quiz_questions") or []
        if quiz_questions:
            quiz_keys: dict[str, Any] = {}
            for q in quiz_questions:
                qid = str(q.get("qid"))
                if not qid:
                    continue
                kind = q.get("kind", "multiple_choice")
                if kind == "multiple_choice":
                    options = q.get("options") or []
                    idx = int(q.get("correct_index", -1) or -1)
                    quiz_keys[qid] = options[idx] if 0 <= idx < len(options) else ""
                    quiz_keys[f"{qid}__method"] = "exact_match"
                else:
                    quiz_keys[qid] = str(q.get("expected_pattern") or "")
                    quiz_keys[f"{qid}__method"] = "regex_match" if q.get("expected_pattern") else "exact_match"
            assignment.submission = {
                "quiz_questions": quiz_questions,
                "quiz_keys": quiz_keys,
            }

        course.assignments.append(assignment)
        lesson.assignment_ids.append(assignment_id)
        course.current_assignment_id = assignment_id
        artifacts = _write_assignment_artifacts(course, assignment, args.get("artifact_files") or [])
        assignment.artifact_paths = artifacts

        # Persist the prompt for human inspection.
        prompt_dir = os.path.join(
            course.directory, "assignments", _storage.slugify(assignment_id)
        )
        os.makedirs(prompt_dir, exist_ok=True)
        with open(os.path.join(prompt_dir, "prompt.md"), "w", encoding="utf-8") as handle:
            handle.write(f"# Assignment {assignment_id}\n\n{assignment.prompt}\n")

        if lesson.status in {LESSON_PRESENTING, LESSON_LECTURING}:
            advance_lesson_status(course, lesson_id, LESSON_ASSIGNED)
        elif lesson.status == "remediating":
            # Re-assignment during remediation flips back to 'assigned'.
            advance_lesson_status(course, lesson_id, LESSON_ASSIGNED)

        add_event(
            course,
            kind="assignment_created",
            entity="assignment",
            entity_id=assignment_id,
            payload={"kind": assignment.kind, "artifact_count": len(artifacts)},
        )
        _persist(session, course)
        return _ok(
            {
                "assignment_id": assignment_id,
                "lesson_id": lesson_id,
                "artifact_paths": artifacts,
                "live_quiz": spec.use_live_quiz_ui and assignment.kind in {"multiple-choice", "fill-blank"},
            }
        )
    except Exception as exc:
        return _err(str(exc))


def _default_method(kind: str) -> str:
    return {
        "fix-broken-code": "exec_markers",
        "implement-from-scratch": "exec_markers",
        "command-output": "exec_markers",
        "predict-output": "exact_match",
        "multiple-choice": "exact_match",
        "fill-blank": "regex_match",
        "short-answer": "rubric_judge",
        "explain-trace": "rubric_judge",
        "socratic-dialog": "dialog_close",
    }.get(kind, "exec_markers")


@tool(
    name="submit_assignment",
    description=(
        "Record the learner's submission for an assignment. For code kinds "
        "the submission may carry an inline payload or simply confirm the "
        "learner edited the artifact files in work/."
    ),
    parameters={
        "type": "object",
        "properties": {
            "assignment_id": {"type": "string"},
            "submission": {
                "type": "object",
                "description": (
                    "Free-form submission payload. Conventions: "
                    "{answer: str} for predict-output / short-answer; "
                    "{answers: {qid: value}} for multi-question quizzes; "
                    "{notes: str} for code fixes done in-place on the work/ files."
                ),
            },
        },
        "required": ["assignment_id"],
    },
    requires_approval=False,
)
def submit_assignment_tool(args: dict[str, Any], context) -> str:
    try:
        session = _session_from_context(context)
        course = _load_active_course(session, context)
        assignment_id = _storage.slugify(str(args.get("assignment_id", "")).strip())
        assignment = find_assignment(course, assignment_id)
        if assignment is None:
            return _not_found_err(
                "assignment",
                assignment_id,
                [a.assignment_id for a in course.assignments],
            )
        submission = args.get("submission") or {}
        existing = assignment.submission or {}
        merged: dict[str, Any] = {**existing, **submission}
        assignment.submission = merged
        assignment.status = ASSIGNMENT_SUBMITTED
        sub_dir = os.path.join(
            course.directory,
            "assignments",
            _storage.slugify(assignment_id),
            "submission",
        )
        os.makedirs(sub_dir, exist_ok=True)
        with open(os.path.join(sub_dir, "submission.json"), "w", encoding="utf-8") as handle:
            json.dump(merged, handle, indent=2, default=str)
        add_event(
            course,
            kind="assignment_submitted",
            entity="assignment",
            entity_id=assignment_id,
            payload={"submission_keys": sorted(merged.keys())},
            actor="learner",
        )
        _persist(session, course)
        return _ok({"assignment_id": assignment_id, "status": assignment.status})
    except Exception as exc:
        return _err(str(exc))


@tool(
    name="grade_assignment",
    description=(
        "Grade a previously submitted assignment. For exec_markers kinds, "
        "runs the verify_cmd and checks the markers. For multiple-choice / "
        "fill-blank with live UI enabled and no submission yet recorded, "
        "launches the live quiz Application via session.ui.run_quiz. For "
        "rubric kinds, pass llm_rubric_score and feedback after evaluating "
        "the prose answer against the rubric."
    ),
    parameters={
        "type": "object",
        "properties": {
            "assignment_id": {"type": "string"},
            "llm_rubric_score": {"type": "integer"},
            "feedback": {"type": "string"},
        },
        "required": ["assignment_id"],
    },
    requires_approval=True,
)
def grade_assignment_tool(args: dict[str, Any], context) -> str:
    try:
        session = _session_from_context(context)
        course = _load_active_course(session, context)
        assignment_id = _storage.slugify(str(args.get("assignment_id", "")).strip())
        assignment = find_assignment(course, assignment_id)
        if assignment is None:
            return _not_found_err(
                "assignment",
                assignment_id,
                [a.assignment_id for a in course.assignments],
            )
        if assignment.kind == "socratic-dialog":
            return _err(
                "socratic-dialog assignments are graded via close_dialog, not grade_assignment"
            )

        # Live quiz UI launch path.
        needs_quiz = (
            assignment.kind in {"multiple-choice", "fill-blank"}
            and assignment.verification.use_live_quiz_ui
            and not (assignment.submission or {}).get("answers")
        )
        if needs_quiz:
            ui = getattr(session, "ui", None) or getattr(context, "ui", None)
            quiz_questions = (assignment.submission or {}).get("quiz_questions") or []
            if ui is not None and hasattr(ui, "run_quiz") and quiz_questions:
                try:
                    submitted = ui.run_quiz(quiz_questions)
                    if isinstance(submitted, dict):
                        existing = assignment.submission or {}
                        assignment.submission = {**existing, "answers": submitted}
                        assignment.status = ASSIGNMENT_SUBMITTED
                except Exception:
                    pass  # fall through to chat-flow grading

        grade_obj = grade_assignment_payload(
            assignment,
            submission=assignment.submission,
            feedback_override=str(args.get("feedback") or "") or None,
            llm_rubric_score=(
                int(args["llm_rubric_score"]) if "llm_rubric_score" in args else None
            ),
        )
        lesson = find_lesson(course, assignment.lesson_id)
        if lesson is not None and lesson.status == LESSON_ASSIGNED:
            advance_lesson_status(course, lesson.lesson_id, LESSON_GRADED)
        add_event(
            course,
            kind="assignment_graded",
            entity="assignment",
            entity_id=assignment.assignment_id,
            payload={"score_pct": grade_obj.score_pct, "passed": grade_obj.passed},
        )
        # Persist the grade record.
        grade_path = os.path.join(
            course.directory,
            "assignments",
            _storage.slugify(assignment.assignment_id),
            "grade.json",
        )
        os.makedirs(os.path.dirname(grade_path), exist_ok=True)
        with open(grade_path, "w", encoding="utf-8") as handle:
            json.dump(asdict(grade_obj), handle, indent=2, default=str)
        _persist(session, course)
        return _ok(
            {
                "assignment_id": assignment.assignment_id,
                "grade": asdict(grade_obj),
            }
        )
    except Exception as exc:
        return _err(str(exc))


@tool(
    name="decide_next",
    description=(
        "After a lesson is graded, advance to the next lesson or trigger "
        "remediation. Refuses advance unless the most recent assignment "
        "passed."
    ),
    parameters={
        "type": "object",
        "properties": {
            "lesson_id": {"type": "string"},
            "action": {"type": "string", "enum": ["advance", "remediate"]},
        },
        "required": ["lesson_id", "action"],
    },
    requires_approval=True,
)
def decide_next_tool(args: dict[str, Any], context) -> str:
    try:
        session = _session_from_context(context)
        course = _load_active_course(session, context)
        lesson_id = _storage.slugify(str(args.get("lesson_id", "")).strip())
        action = str(args.get("action", "")).strip()
        decide_next(course, lesson_id, action)
        if action == "advance":
            nxt = next_pending_lesson(course)
            if nxt is not None:
                course.current_module_id = nxt.module_id
                course.current_lesson_id = nxt.lesson_id
            else:
                course.current_lesson_id = None
        record = _persist(session, course)
        return _ok(
            {
                "course": record,
                "action": action,
                "next_lesson_id": course.current_lesson_id,
            }
        )
    except Exception as exc:
        return _err(str(exc))


# ----- socratic dialog -------------------------------------------------


@tool(
    name="record_dialog_turn",
    description=(
        "Append one turn (agent question OR learner answer) to a "
        "socratic-dialog assignment. Call once per turn so the engine "
        "can later enforce min_turns and required_concepts coverage."
    ),
    parameters={
        "type": "object",
        "properties": {
            "assignment_id": {"type": "string"},
            "role": {"type": "string", "enum": ["agent_question", "learner_answer"]},
            "content": {"type": "string"},
            "quality_signal": {"type": "string"},
        },
        "required": ["assignment_id", "role", "content"],
    },
    requires_approval=False,
)
def record_dialog_turn_tool(args: dict[str, Any], context) -> str:
    try:
        session = _session_from_context(context)
        course = _load_active_course(session, context)
        turn = record_dialog_turn(
            course,
            _storage.slugify(str(args.get("assignment_id", "")).strip()),
            role=str(args.get("role", "")).strip(),
            content=str(args.get("content", "")).strip(),
            quality_signal=(
                str(args.get("quality_signal")).strip()
                if args.get("quality_signal")
                else None
            ),
        )
        _persist(session, course)
        return _ok({"turn": asdict(turn)})
    except Exception as exc:
        return _err(str(exc))


@tool(
    name="close_dialog",
    description=(
        "Finalize a socratic-dialog assignment with an honest mastery "
        "score, a summary, and a list of gaps. Refuses unless min_turns "
        "and required_concepts coverage thresholds are met."
    ),
    parameters={
        "type": "object",
        "properties": {
            "assignment_id": {"type": "string"},
            "mastery_pct": {"type": "integer"},
            "summary": {"type": "string"},
            "gaps": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["assignment_id", "mastery_pct", "summary"],
    },
    requires_approval=True,
)
def close_dialog_tool(args: dict[str, Any], context) -> str:
    try:
        session = _session_from_context(context)
        course = _load_active_course(session, context)
        assignment_id = _storage.slugify(str(args.get("assignment_id", "")).strip())
        grade_obj = close_socratic_dialog(
            course,
            assignment_id,
            mastery_pct=int(args.get("mastery_pct", 0)),
            summary=str(args.get("summary", "")).strip(),
            gaps=[str(x) for x in (args.get("gaps") or [])],
        )
        assignment = find_assignment(course, assignment_id)
        lesson = find_lesson(course, assignment.lesson_id) if assignment else None
        if lesson is not None and lesson.status == LESSON_ASSIGNED:
            advance_lesson_status(course, lesson.lesson_id, LESSON_GRADED)
        _persist(session, course)
        return _ok(
            {
                "assignment_id": assignment_id,
                "grade": asdict(grade_obj),
            }
        )
    except Exception as exc:
        return _err(str(exc))


# ----- inspection / control -------------------------------------------


@tool(
    name="get_course_state",
    description=(
        "Return a snapshot of the active course: status, current "
        "module/lesson/assignment, completion %, average score, and "
        "the most recently graded assignment."
    ),
    parameters={
        "type": "object",
        "properties": {"course_id": {"type": "string"}},
    },
    requires_approval=False,
)
def get_course_state_tool(args: dict[str, Any], context) -> str:
    try:
        session = _session_from_context(context)
        course = _load_active_course(session, context, args.get("course_id"))
        metrics = course_metrics(course)
        next_lesson = next_pending_lesson(course)
        latest_graded = None
        for a in reversed(course.assignments):
            if a.grade is not None:
                latest_graded = {
                    "assignment_id": a.assignment_id,
                    "lesson_id": a.lesson_id,
                    "score_pct": a.grade.score_pct,
                    "passed": a.grade.passed,
                }
                break
        return _ok(
            {
                "course_id": course.course_id,
                "subject": course.subject,
                "status": course.status,
                "metrics": metrics,
                "current_module_id": course.current_module_id,
                "current_lesson_id": course.current_lesson_id,
                "current_assignment_id": course.current_assignment_id,
                "next_pending_lesson_id": next_lesson.lesson_id if next_lesson else None,
                "latest_graded": latest_graded,
            }
        )
    except Exception as exc:
        return _err(str(exc))


@tool(
    name="complete_module",
    description=(
        "Mark a module complete. Refuses unless every lesson in the module "
        "is in 'completed' status AND the aggregate score across the "
        "module's graded assignments meets the module's mastery_threshold."
    ),
    parameters={
        "type": "object",
        "properties": {"module_id": {"type": "string"}},
        "required": ["module_id"],
    },
    requires_approval=True,
)
def complete_module_tool(args: dict[str, Any], context) -> str:
    try:
        session = _session_from_context(context)
        course = _load_active_course(session, context)
        module_id = _storage.slugify(str(args.get("module_id", "")).strip())
        module = find_module(course, module_id)
        if module is None:
            return _not_found_err("module", module_id, [m.module_id for m in course.modules])
        incomplete = []
        for lesson_id in module.lesson_ids:
            lesson = find_lesson(course, lesson_id)
            if lesson is None or lesson.status != LESSON_COMPLETED:
                incomplete.append(lesson_id)
        if incomplete:
            return _err(
                f"complete_module refused: lessons not yet completed: "
                + ", ".join(incomplete)
            )
        # Aggregate score across passed assignments in this module.
        lesson_ids = set(module.lesson_ids)
        graded = [
            a
            for a in course.assignments
            if a.lesson_id in lesson_ids and a.grade is not None
        ]
        if not graded:
            agg = 0
        else:
            agg = int(round(sum(a.grade.score_pct for a in graded) / len(graded)))
        if agg < module.mastery_threshold:
            return _err(
                f"complete_module refused: aggregate score {agg}% < "
                f"mastery_threshold {module.mastery_threshold}%"
            )
        module.status = LESSON_COMPLETED
        add_event(
            course,
            kind="module_completed",
            entity="module",
            entity_id=module_id,
            payload={"aggregate_score_pct": agg},
        )
        _persist(session, course)
        return _ok({"module_id": module_id, "aggregate_score_pct": agg})
    except Exception as exc:
        return _err(str(exc))


@tool(
    name="finalize_course",
    description=(
        "Mark the course completed, write the report card, and save a "
        "durable `user_skill:<subject>` memory so future courses can recall "
        "what the learner already knows."
    ),
    parameters={
        "type": "object",
        "properties": {"course_id": {"type": "string"}},
    },
    requires_approval=True,
)
def finalize_course_tool(args: dict[str, Any], context) -> str:
    try:
        session = _session_from_context(context)
        course = _load_active_course(session, context, args.get("course_id"))
        course.status = COURSE_COMPLETED
        add_event(
            course,
            kind="course_completed",
            entity="course",
            entity_id=course.course_id,
            payload=course_metrics(course),
        )
        report_path = _write_report_card(course)
        # Persist a durable memory tag for cross-session recall.
        try:
            memory = getattr(session.session_manager, "task_memory", None)
            if memory is not None and hasattr(memory, "save"):
                metrics = course_metrics(course)
                memory.save(
                    content=(
                        f"Completed course '{course.subject}' "
                        f"(level: {course.target_level}, "
                        f"avg score: {metrics['average_score_pct']}%, "
                        f"lessons: {metrics['lessons_completed']}/{metrics['total_lessons']})"
                    ),
                    tags=[f"user_skill:{_storage.slugify(course.subject)}", "course_complete"],
                    source="teacher_mode",
                )
        except Exception:
            pass
        record = _persist(session, course)
        return _ok({"course": record, "report_card_path": report_path})
    except Exception as exc:
        return _err(str(exc))


@tool(
    name="raise_teacher_blocker",
    description=(
        "Signal that the agent needs clarification from the learner before "
        "proceeding (e.g. unclear goal, missing background, environment "
        "issue). Mirror of feature mode's raise_blocker."
    ),
    parameters={
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "details": {"type": "string"},
            "questions": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["summary"],
    },
    requires_approval=False,
)
def raise_teacher_blocker_tool(args: dict[str, Any], context) -> str:
    payload = {
        "kind": "teacher_blocker",
        "summary": str(args.get("summary", "")).strip(),
        "details": str(args.get("details", "")).strip(),
        "questions": [
            str(item).strip() for item in (args.get("questions") or []) if str(item).strip()
        ],
    }
    return _ok(payload)


__all__ = [
    "approve_curriculum_tool",
    "assign_exercise_tool",
    "close_dialog_tool",
    "complete_module_tool",
    "conclude_lecture_tool",
    "create_course_tool",
    "decide_next_tool",
    "finalize_course_tool",
    "get_course_state_tool",
    "grade_assignment_tool",
    "present_concept_tool",
    "propose_curriculum_tool",
    "raise_teacher_blocker_tool",
    "record_diagnostic_tool",
    "record_dialog_turn_tool",
    "record_lecture_turn_tool",
    "start_lecture_tool",
    "start_lesson_tool",
    "submit_assignment_tool",
]
