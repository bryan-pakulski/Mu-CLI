"""Teacher mode engine — dataclasses, lifecycle, persistence.

The engine is the single source of truth for course state. It is
intentionally UI-free; `mu.tools.teacher.handlers` performs session
sync and the optional live quiz UI launch.

Status transitions for lessons are enforced (mirrors the feature
engine's `ALLOWED_TASK_TRANSITIONS`) so the agent cannot quietly skip
the grade step. Likewise `decide_next("advance")` refuses unless the
current assignment has a passing grade — anti-hallucination
contract borrowed from the security engine.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from mu.teacher import storage as _storage

# --- status constants ----------------------------------------------------

COURSE_DIAGNOSING = "diagnosing"
COURSE_CURRICULUM_PROPOSED = "curriculum_proposed"
COURSE_IN_PROGRESS = "in_progress"
COURSE_COMPLETED = "completed"
COURSE_ARCHIVED = "archived"

LESSON_PENDING = "pending"
LESSON_PRESENTING = "presenting"
LESSON_ASSIGNED = "assigned"
LESSON_GRADED = "graded"
LESSON_COMPLETED = "completed"
LESSON_REMEDIATING = "remediating"

ALLOWED_LESSON_TRANSITIONS: dict[str, set[str]] = {
    LESSON_PENDING: {LESSON_PRESENTING},
    LESSON_PRESENTING: {LESSON_ASSIGNED},
    LESSON_ASSIGNED: {LESSON_GRADED},
    LESSON_GRADED: {LESSON_COMPLETED, LESSON_REMEDIATING},
    LESSON_REMEDIATING: {LESSON_ASSIGNED},
    LESSON_COMPLETED: set(),
}

ASSIGNMENT_PRESENTED = "presented"
ASSIGNMENT_SUBMITTED = "submitted"
ASSIGNMENT_GRADED = "graded"
ASSIGNMENT_PASSED = "passed"
ASSIGNMENT_FAILED = "failed"


# --- dataclasses ---------------------------------------------------------


@dataclass
class RubricItem:
    criterion: str
    weight: int = 1
    description: str = ""


@dataclass
class VerificationSpec:
    """How an assignment is graded.

    Method values:
    - `exec_markers`: run `verify_cmd`; require every `expected_markers`
      string to appear literally in stdout+stderr; reject if any
      `forbidden_markers` appears.
    - `exact_match`: compare submission to `expected_answer` literally.
    - `regex_match`: compile `expected_answer` as a regex and search
      against the submission.
    - `rubric_judge`: LLM grades against rubric; engine enforces
      `rubric_keywords` must literally appear before the LLM is
      consulted (a cheap honesty gate).
    - `dialog_close`: socratic dialog flow; engine requires `min_turns`
      and `required_concepts` coverage before `close_dialog` succeeds.
    """

    method: str = "exec_markers"
    verify_cmd: str | None = None
    expected_markers: list[str] = field(default_factory=list)
    forbidden_markers: list[str] = field(default_factory=list)
    expected_answer: str | None = None
    case_sensitive: bool = False
    rubric_keywords: list[str] = field(default_factory=list)
    min_turns: int = 0
    required_concepts: list[str] = field(default_factory=list)
    timeout_seconds: int = 30
    working_dir: str = ""
    use_live_quiz_ui: bool = False


@dataclass
class Grade:
    score_pct: int = 0
    passed: bool = False
    rubric_breakdown: list[dict[str, Any]] = field(default_factory=list)
    verification_result: dict[str, Any] = field(default_factory=dict)
    feedback: str = ""
    graded_at: float = field(default_factory=time.time)


@dataclass
class DialogTurn:
    turn_index: int
    role: str
    content: str
    quality_signal: str | None = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class Assignment:
    assignment_id: str
    lesson_id: str
    kind: str
    prompt: str
    artifact_paths: list[str] = field(default_factory=list)
    rubric: list[RubricItem] = field(default_factory=list)
    verification: VerificationSpec = field(default_factory=VerificationSpec)
    status: str = ASSIGNMENT_PRESENTED
    submission: dict[str, Any] | None = None
    grade: Grade | None = None
    dialog_turns: list[DialogTurn] = field(default_factory=list)
    pass_threshold: int = 70
    created_at: float = field(default_factory=time.time)


@dataclass
class Lesson:
    lesson_id: str
    module_id: str
    title: str
    learning_objectives: list[str] = field(default_factory=list)
    concept_brief: str = ""
    status: str = LESSON_PENDING
    assignment_ids: list[str] = field(default_factory=list)
    remediation_count: int = 0


@dataclass
class Module:
    module_id: str
    title: str
    goal: str = ""
    order: int = 0
    status: str = LESSON_PENDING
    lesson_ids: list[str] = field(default_factory=list)
    mastery_threshold: int = 70


@dataclass
class CourseEvent:
    kind: str
    entity: str
    entity_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    actor: str = "system"
    created_at: float = field(default_factory=time.time)


@dataclass
class Course:
    course_id: str
    subject: str
    target_level: str = "beginner"
    learner_profile: dict[str, Any] = field(default_factory=dict)
    directory: str = ""
    status: str = COURSE_DIAGNOSING
    modules: list[Module] = field(default_factory=list)
    lessons: list[Lesson] = field(default_factory=list)
    assignments: list[Assignment] = field(default_factory=list)
    current_module_id: str | None = None
    current_lesson_id: str | None = None
    current_assignment_id: str | None = None
    event_log: list[CourseEvent] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


# --- lifecycle -----------------------------------------------------------


def add_event(
    course: Course,
    *,
    kind: str,
    entity: str,
    entity_id: str,
    payload: dict[str, Any] | None = None,
    actor: str = "system",
) -> None:
    course.event_log.append(
        CourseEvent(
            kind=kind,
            entity=entity,
            entity_id=str(entity_id),
            payload=payload or {},
            actor=actor,
        )
    )
    course.updated_at = time.time()


def create_course(
    *,
    subject: str,
    target_level: str = "beginner",
    learner_summary: str = "",
    folder_context: Any = None,
    course_id: str | None = None,
) -> Course:
    slug = _storage.slugify(course_id or subject)
    directory = _storage.ensure_course_directory(slug, folder_context)
    course = Course(
        course_id=slug,
        subject=str(subject or "").strip() or slug,
        target_level=target_level,
        learner_profile=(
            {"summary": learner_summary.strip()} if learner_summary else {}
        ),
        directory=directory,
        status=COURSE_DIAGNOSING,
    )
    add_event(
        course,
        kind="course_created",
        entity="course",
        entity_id=course.course_id,
        payload={"subject": course.subject, "target_level": target_level},
    )
    save_course(course)
    return course


def save_course(course: Course) -> Course:
    course.updated_at = time.time()
    path = os.path.join(course.directory, "course.json")
    os.makedirs(course.directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(asdict(course), handle, indent=2)
    return course


def load_course(course_id_or_path: str, folder_context: Any = None) -> Course | None:
    if os.path.isabs(course_id_or_path) and os.path.exists(course_id_or_path):
        path = course_id_or_path
    else:
        path = _storage.course_state_path(course_id_or_path, folder_context)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    return _course_from_dict(data)


def _course_from_dict(data: dict[str, Any]) -> Course:
    course = Course(
        course_id=data["course_id"],
        subject=data["subject"],
        target_level=data.get("target_level", "beginner"),
        learner_profile=data.get("learner_profile", {}),
        directory=data.get("directory", ""),
        status=data.get("status", COURSE_DIAGNOSING),
        current_module_id=data.get("current_module_id"),
        current_lesson_id=data.get("current_lesson_id"),
        current_assignment_id=data.get("current_assignment_id"),
        created_at=data.get("created_at", time.time()),
        updated_at=data.get("updated_at", time.time()),
    )
    for raw in data.get("modules", []):
        course.modules.append(Module(**raw))
    for raw in data.get("lessons", []):
        course.lessons.append(Lesson(**raw))
    for raw in data.get("assignments", []):
        course.assignments.append(_assignment_from_dict(raw))
    for raw in data.get("event_log", []):
        course.event_log.append(CourseEvent(**raw))
    return course


def _assignment_from_dict(raw: dict[str, Any]) -> Assignment:
    verification = VerificationSpec(**raw.get("verification", {}))
    rubric = [RubricItem(**item) for item in raw.get("rubric", [])]
    dialog_turns = [DialogTurn(**item) for item in raw.get("dialog_turns", [])]
    grade_data = raw.get("grade")
    grade = Grade(**grade_data) if isinstance(grade_data, dict) else None
    return Assignment(
        assignment_id=raw["assignment_id"],
        lesson_id=raw["lesson_id"],
        kind=raw["kind"],
        prompt=raw.get("prompt", ""),
        artifact_paths=list(raw.get("artifact_paths", [])),
        rubric=rubric,
        verification=verification,
        status=raw.get("status", ASSIGNMENT_PRESENTED),
        submission=raw.get("submission"),
        grade=grade,
        dialog_turns=dialog_turns,
        pass_threshold=int(raw.get("pass_threshold", 70)),
        created_at=raw.get("created_at", time.time()),
    )


# --- finders -------------------------------------------------------------


def find_module(course: Course, module_id: str) -> Module | None:
    for module in course.modules:
        if module.module_id == module_id:
            return module
    return None


def find_lesson(course: Course, lesson_id: str) -> Lesson | None:
    for lesson in course.lessons:
        if lesson.lesson_id == lesson_id:
            return lesson
    return None


def find_assignment(course: Course, assignment_id: str) -> Assignment | None:
    for assignment in course.assignments:
        if assignment.assignment_id == assignment_id:
            return assignment
    return None


def next_pending_lesson(course: Course) -> Lesson | None:
    ordered_modules = sorted(course.modules, key=lambda m: m.order)
    for module in ordered_modules:
        for lesson_id in module.lesson_ids:
            lesson = find_lesson(course, lesson_id)
            if lesson is None:
                continue
            if lesson.status != LESSON_COMPLETED:
                return lesson
    return None


# --- state transitions ---------------------------------------------------


def is_valid_lesson_transition(from_status: str, to_status: str) -> bool:
    return to_status in ALLOWED_LESSON_TRANSITIONS.get(from_status, set())


def advance_lesson_status(course: Course, lesson_id: str, to_status: str) -> Lesson:
    lesson = find_lesson(course, lesson_id)
    if lesson is None:
        raise ValueError(f"Lesson {lesson_id!r} not found")
    if not is_valid_lesson_transition(lesson.status, to_status):
        raise ValueError(
            f"Invalid lesson transition: {lesson.status} -> {to_status}"
        )
    add_event(
        course,
        kind="lesson_transition",
        entity="lesson",
        entity_id=lesson.lesson_id,
        payload={"from": lesson.status, "to": to_status},
    )
    lesson.status = to_status
    if to_status == LESSON_REMEDIATING:
        lesson.remediation_count += 1
    return lesson


def decide_next(course: Course, lesson_id: str, action: str) -> Lesson:
    """Advance or remediate a graded lesson.

    Refuses `advance` unless the current assignment has a passing grade.
    """
    lesson = find_lesson(course, lesson_id)
    if lesson is None:
        raise ValueError(f"Lesson {lesson_id!r} not found")
    if action not in {"advance", "remediate"}:
        raise ValueError(f"decide_next action must be 'advance' or 'remediate', got {action!r}")
    if lesson.status != LESSON_GRADED:
        raise ValueError(
            f"decide_next requires lesson status 'graded' (lesson is {lesson.status!r}); "
            "call grade_assignment first"
        )
    if action == "advance":
        latest = _latest_graded_assignment(course, lesson)
        if latest is None or latest.grade is None or not latest.grade.passed:
            raise ValueError(
                "decide_next('advance') refused: the lesson's most recent graded "
                "assignment did not pass. Remediate first."
            )
        advance_lesson_status(course, lesson_id, LESSON_COMPLETED)
    else:
        advance_lesson_status(course, lesson_id, LESSON_REMEDIATING)
    return lesson


def _latest_graded_assignment(course: Course, lesson: Lesson) -> Assignment | None:
    candidates = [
        assignment
        for assignment in course.assignments
        if assignment.lesson_id == lesson.lesson_id and assignment.grade is not None
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda a: a.grade.graded_at if a.grade else 0.0)


# --- socratic dialog -----------------------------------------------------


def record_dialog_turn(
    course: Course,
    assignment_id: str,
    *,
    role: str,
    content: str,
    quality_signal: str | None = None,
) -> DialogTurn:
    assignment = find_assignment(course, assignment_id)
    if assignment is None:
        raise ValueError(f"Assignment {assignment_id!r} not found")
    if assignment.kind != "socratic-dialog":
        raise ValueError(
            f"record_dialog_turn is only valid for socratic-dialog assignments "
            f"(assignment {assignment_id!r} is {assignment.kind!r})"
        )
    if role not in {"agent_question", "learner_answer"}:
        raise ValueError(
            f"dialog turn role must be 'agent_question' or 'learner_answer', got {role!r}"
        )
    turn = DialogTurn(
        turn_index=len(assignment.dialog_turns),
        role=role,
        content=str(content or "").strip(),
        quality_signal=quality_signal,
    )
    assignment.dialog_turns.append(turn)
    add_event(
        course,
        kind="dialog_turn",
        entity="assignment",
        entity_id=assignment_id,
        payload={"role": role, "turn_index": turn.turn_index},
    )
    return turn


def close_socratic_dialog(
    course: Course,
    assignment_id: str,
    *,
    mastery_pct: int,
    summary: str,
    gaps: list[str],
) -> Grade:
    """Finalize a socratic-dialog assignment.

    Refuses unless: (a) the verification spec's `min_turns` is met counted
    against learner answers, and (b) every `required_concepts` entry appears
    as a substring (case-insensitive) of at least one agent question.
    """
    assignment = find_assignment(course, assignment_id)
    if assignment is None:
        raise ValueError(f"Assignment {assignment_id!r} not found")
    if assignment.kind != "socratic-dialog":
        raise ValueError(
            f"close_dialog is only valid for socratic-dialog assignments "
            f"(assignment {assignment_id!r} is {assignment.kind!r})"
        )
    spec = assignment.verification
    learner_turns = [t for t in assignment.dialog_turns if t.role == "learner_answer"]
    if len(learner_turns) < max(0, int(spec.min_turns or 0)):
        raise ValueError(
            f"close_dialog refused: need at least {spec.min_turns} learner answers, "
            f"have {len(learner_turns)}"
        )
    agent_questions_blob = "\n".join(
        t.content for t in assignment.dialog_turns if t.role == "agent_question"
    ).lower()
    missing_concepts = [
        concept
        for concept in (spec.required_concepts or [])
        if concept.strip() and concept.strip().lower() not in agent_questions_blob
    ]
    if missing_concepts:
        raise ValueError(
            "close_dialog refused: agent never probed required concepts: "
            + ", ".join(missing_concepts)
        )
    mastery = max(0, min(100, int(mastery_pct)))
    passed = mastery >= assignment.pass_threshold
    grade = Grade(
        score_pct=mastery,
        passed=passed,
        rubric_breakdown=[],
        verification_result={
            "dialog_complete": True,
            "turn_count": len(assignment.dialog_turns),
            "learner_turn_count": len(learner_turns),
            "concepts_covered": list(spec.required_concepts or []),
            "gaps": list(gaps or []),
        },
        feedback=summary.strip(),
    )
    assignment.grade = grade
    assignment.status = ASSIGNMENT_PASSED if passed else ASSIGNMENT_FAILED
    add_event(
        course,
        kind="dialog_closed",
        entity="assignment",
        entity_id=assignment_id,
        payload={"mastery_pct": mastery, "passed": passed, "gaps": list(gaps or [])},
    )
    _write_dialog_transcript(course, assignment)
    return grade


def _write_dialog_transcript(course: Course, assignment: Assignment) -> None:
    if not course.directory:
        return
    target_dir = os.path.join(
        course.directory, "assignments", _storage.slugify(assignment.assignment_id)
    )
    os.makedirs(target_dir, exist_ok=True)
    lines = [f"# Dialog transcript — {assignment.assignment_id}", ""]
    for turn in assignment.dialog_turns:
        speaker = "Agent" if turn.role == "agent_question" else "Learner"
        lines.append(f"**{speaker}** ({turn.turn_index}): {turn.content}")
        if turn.quality_signal:
            lines.append(f"  _signal_: {turn.quality_signal}")
        lines.append("")
    if assignment.grade is not None:
        lines.append("---")
        lines.append(
            f"**Mastery**: {assignment.grade.score_pct}% "
            f"({'passed' if assignment.grade.passed else 'failed'})"
        )
        lines.append("")
        lines.append(assignment.grade.feedback)
    with open(os.path.join(target_dir, "dialog.md"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


# --- metrics for HUD ------------------------------------------------------


def course_metrics(course: Course) -> dict[str, Any]:
    total_lessons = len(course.lessons)
    completed = sum(1 for lesson in course.lessons if lesson.status == LESSON_COMPLETED)
    overall_pct = int(100 * completed / total_lessons) if total_lessons else 0

    graded = [a for a in course.assignments if a.grade is not None]
    if graded:
        avg = int(round(sum(a.grade.score_pct for a in graded) / len(graded)))
    else:
        avg = 0

    module_idx = 0
    lesson_idx = 0
    if course.current_lesson_id:
        lesson = find_lesson(course, course.current_lesson_id)
        if lesson is not None:
            module = find_module(course, lesson.module_id)
            if module is not None:
                ordered = sorted(course.modules, key=lambda m: m.order)
                for i, candidate in enumerate(ordered, start=1):
                    if candidate.module_id == module.module_id:
                        module_idx = i
                        break
                if lesson.lesson_id in module.lesson_ids:
                    lesson_idx = module.lesson_ids.index(lesson.lesson_id) + 1

    return {
        "course_id": course.course_id,
        "subject": course.subject,
        "status": course.status,
        "total_modules": len(course.modules),
        "total_lessons": total_lessons,
        "lessons_completed": completed,
        "overall_pct": overall_pct,
        "average_score_pct": avg,
        "current_module_index": module_idx,
        "current_lesson_index_in_module": lesson_idx,
    }


__all__ = [
    "ALLOWED_LESSON_TRANSITIONS",
    "ASSIGNMENT_FAILED",
    "ASSIGNMENT_GRADED",
    "ASSIGNMENT_PASSED",
    "ASSIGNMENT_PRESENTED",
    "ASSIGNMENT_SUBMITTED",
    "Assignment",
    "Course",
    "CourseEvent",
    "DialogTurn",
    "Grade",
    "LESSON_ASSIGNED",
    "LESSON_COMPLETED",
    "LESSON_GRADED",
    "LESSON_PENDING",
    "LESSON_PRESENTING",
    "LESSON_REMEDIATING",
    "Lesson",
    "Module",
    "RubricItem",
    "VerificationSpec",
    "add_event",
    "advance_lesson_status",
    "close_socratic_dialog",
    "course_metrics",
    "create_course",
    "decide_next",
    "find_assignment",
    "find_lesson",
    "find_module",
    "is_valid_lesson_transition",
    "load_course",
    "next_pending_lesson",
    "record_dialog_turn",
    "save_course",
]
