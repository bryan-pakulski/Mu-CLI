"""Teacher-mode transcript watchers, extracted from mu/agent/loop_body.py.

These hooks observe user/assistant turns during an active teacher lesson
and persist course state. loop_body imports them; no other module does.
"""

from __future__ import annotations

import json

from utils.logger import logger


def _active_teacher_lesson(session):
    """Return ``(course, lesson_id)`` for the legacy LLM watcher (only used when
    ``teacher_watcher`` is on) when the current lesson is presenting/lecturing."""
    try:
        from mu.teacher.engine import load_course
        from mu.teacher.gates import active_course_path
        from mu.teacher.watcher import is_watcher_eligible

        path = active_course_path(session)
        course = load_course(path) if path else None
    except Exception as exc:
        logger.debug("watcher: could not load course: %s", exc)
        return None, None
    if course is None or not course.current_lesson_id:
        return None, None
    if not is_watcher_eligible(course, course.current_lesson_id):
        return None, None
    return course, course.current_lesson_id


def _run_teacher_watcher_user(session, user_text: str) -> None:
    course, lesson_id = _active_teacher_lesson(session)
    if course is None:
        return
    try:
        from mu.teacher.engine import find_lesson
        from mu.teacher.watcher import (
            apply_learner_classification,
            classify_user_message,
        )

        lesson = find_lesson(course, lesson_id)
        classification = classify_user_message(
            session.provider, user_text, lesson=lesson
        )
        if not classification:
            return
        result = apply_learner_classification(course, lesson_id, classification)
        _persist_teacher_course(session, course)
        if result.events_applied and session.ui:
            session.ui.show_info(
                f"teach · watcher: recorded {len(result.events_applied)} "
                f"learner turn{'s' if len(result.events_applied) != 1 else ''}"
            )
    except Exception as exc:
        logger.warning("watcher (user): %s", exc)


def _run_teacher_watcher_assistant(session, assistant_text: str) -> None:
    course, lesson_id = _active_teacher_lesson(session)
    if course is None:
        return
    try:
        from mu.teacher.engine import find_lesson
        from mu.teacher.watcher import (
            apply_assistant_classification,
            classify_assistant_message,
        )

        lesson = find_lesson(course, lesson_id)
        classification = classify_assistant_message(
            session.provider, assistant_text, lesson=lesson
        )
        if not classification:
            return
        result = apply_assistant_classification(course, lesson_id, classification)
        _persist_teacher_course(session, course)
        if result.agent_feedback and session.ui:
            session.ui.show_info(f"teach · {result.agent_feedback}")
        if result.events_applied and session.ui:
            kinds = ", ".join(e["kind"] for e in result.events_applied)
            session.ui.show_info(f"teach · watcher: recorded {kinds}")
        if result.auto_concluded_lecture and session.ui:
            session.ui.show_info("teach · lecture auto-concluded by watcher")
    except Exception as exc:
        logger.warning("watcher (assistant): %s", exc)


def _persist_teacher_course(session, course) -> None:
    """Save the course and refresh the session's pointer record."""
    try:
        from mu.teacher.engine import save_course
        from mu.teacher.gates import course_record

        save_course(course)
        record = course_record(course, getattr(session, "folder_context", None))
        session.session_manager.upsert_teacher_course(record)
        session.session_manager.teacher_state = record
        session.session_manager.active_course_id = course.course_id
        session.session_manager.save_history(session.folder_context)
    except Exception as exc:
        logger.warning("watcher: persist course failed: %s", exc)


