"""Teacher mode engine.

Structured course-as-engine: a learner picks a subject, the engine runs a
diagnostic, the agent proposes a curriculum, and lessons advance only when
their assignment passes verification. Hard-evidence grading contract mirrors
the security mode — nothing is approved without a verifiable result.

Three modules:
- `engine`   — dataclasses, lifecycle, status transitions, persistence.
- `storage`  — directory layout and workspace_key resolution.
- `grading`  — verification dispatch by assignment kind (exec-and-marker,
               exact/regex match, rubric judge, socratic dialog close).
"""

from mu.teacher.engine import (
    ALLOWED_LESSON_TRANSITIONS,
    Assignment,
    Course,
    DialogTurn,
    Grade,
    LectureTurn,
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
    is_valid_lesson_transition,
    load_course,
    next_pending_lesson,
    record_dialog_turn,
    record_lecture_turn,
    save_course,
    start_lecture,
)
from mu.teacher.grading import grade as grade_assignment_payload
from mu.teacher.storage import (
    course_directory,
    workspace_root,
)

__all__ = [
    "ALLOWED_LESSON_TRANSITIONS",
    "Assignment",
    "Course",
    "DialogTurn",
    "Grade",
    "LectureTurn",
    "Lesson",
    "Module",
    "RubricItem",
    "VerificationSpec",
    "add_event",
    "advance_lesson_status",
    "close_socratic_dialog",
    "conclude_lecture",
    "course_directory",
    "course_metrics",
    "create_course",
    "decide_next",
    "find_assignment",
    "find_lesson",
    "find_module",
    "grade_assignment_payload",
    "is_valid_lesson_transition",
    "load_course",
    "next_pending_lesson",
    "record_dialog_turn",
    "record_lecture_turn",
    "save_course",
    "start_lecture",
    "workspace_root",
]
