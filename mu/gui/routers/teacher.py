"""Teacher-mode introspection.

Exposes the live course state (curriculum, learner profile, current
lesson, grades) so the GUI can render a side-panel showing what the
agent has been working on. Read-only — no mutation endpoints. State is
mutated through the existing teacher tools via the chat send path.

Returns `null`-shaped payloads when no session or course is active so
the panel can mount empty without erroring.

Data sources: the SessionManager only persists a lightweight metadata
stub for each course (course_id, subject, directory, status, metrics).
The rich data — modules, lessons, learner_profile, assignments — lives
in ``<course_directory>/course.json``, written by the teacher engine.
This router hydrates from that file when the stub points to a directory
we can read, so the GUI sees what's actually on disk rather than only
the in-memory metadata.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request

router = APIRouter()
_logger = logging.getLogger(__name__)


def _hydrate_from_disk(stub: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """If `stub` carries a `directory` and `<directory>/course.json`
    exists, return the merged disk view. Falls back to the stub unchanged.

    The disk file is the source of truth — modules/lessons/learner_profile
    are only stored there. We merge so any fields present in the in-memory
    stub but not the file (rare) are preserved.
    """
    if not isinstance(stub, dict):
        return stub
    directory = stub.get("directory")
    if not directory:
        return stub
    path = os.path.join(directory, "course.json")
    if not os.path.exists(path):
        return stub
    try:
        with open(path, encoding="utf-8") as fh:
            disk = json.load(fh)
    except (OSError, ValueError) as exc:
        _logger.warning("teacher: could not read %s: %s", path, exc)
        return stub
    if not isinstance(disk, dict):
        return stub
    merged = {**stub, **disk}
    return merged


def _summarize_assignments(course: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Compact assignment list with the bits the panel actually shows:
    id, lesson, kind, status, grade summary."""
    out: List[Dict[str, Any]] = []
    for a in course.get("assignments", []) or []:
        if not isinstance(a, dict):
            continue
        grade = a.get("grade") or {}
        out.append(
            {
                "assignment_id": a.get("assignment_id"),
                "lesson_id": a.get("lesson_id"),
                "kind": a.get("kind"),
                "status": a.get("status"),
                "pass_threshold": a.get("pass_threshold"),
                "grade": {
                    "score_pct": grade.get("score_pct"),
                    "passed": grade.get("passed"),
                    "feedback": grade.get("feedback"),
                }
                if grade
                else None,
            }
        )
    return out


def _summarize_lessons(course: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for l in course.get("lessons", []) or []:
        if not isinstance(l, dict):
            continue
        out.append(
            {
                "lesson_id": l.get("lesson_id"),
                "module_id": l.get("module_id"),
                "title": l.get("title"),
                "status": l.get("status"),
                "concept_brief": l.get("concept_brief", ""),
                "learning_objectives": list(l.get("learning_objectives") or []),
                "lecture_turn_count": len(l.get("lecture_turns") or []),
                "lecture_comprehension_pct": l.get("lecture_comprehension_pct"),
                "lecture_concluded": bool(l.get("lecture_concluded")),
                "assignment_ids": list(l.get("assignment_ids") or []),
                "remediation_count": l.get("remediation_count", 0),
            }
        )
    return out


def _summarize_modules(course: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Modules with their lessons embedded.

    The frontend renders the curriculum tree as `module → lessons`, so
    pre-joining here keeps the template trivial (avoids fragile
    nested-find expressions in Alpine).
    """
    summarized_lessons = _summarize_lessons(course)
    lessons_by_module: Dict[str, List[Dict[str, Any]]] = {}
    for l in summarized_lessons:
        mid = str(l.get("module_id") or "")
        lessons_by_module.setdefault(mid, []).append(l)

    out: List[Dict[str, Any]] = []
    for m in course.get("modules", []) or []:
        if not isinstance(m, dict):
            continue
        module_id = str(m.get("module_id") or "")
        lesson_ids = list(m.get("lesson_ids") or [])
        # Preserve the module's explicit lesson order when given; fall
        # back to lookup-order otherwise.
        module_lessons: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for lid in lesson_ids:
            for l in lessons_by_module.get(module_id, []):
                if l.get("lesson_id") == lid and l.get("lesson_id") not in seen:
                    module_lessons.append(l)
                    seen.add(l.get("lesson_id"))
                    break
        # Append any lessons claimed by this module but missing from
        # lesson_ids (shouldn't happen, but defensive).
        for l in lessons_by_module.get(module_id, []):
            if l.get("lesson_id") not in seen:
                module_lessons.append(l)
                seen.add(l.get("lesson_id"))
        out.append(
            {
                "module_id": m.get("module_id"),
                "title": m.get("title"),
                "goal": m.get("goal", ""),
                "order": m.get("order", 0),
                "status": m.get("status"),
                "lesson_ids": lesson_ids,
                "mastery_threshold": m.get("mastery_threshold"),
                "lessons": module_lessons,
            }
        )

    # Lessons not claimed by any module land in a synthetic "_loose"
    # module so they still render.
    placed_ids = {l.get("lesson_id") for m in out for l in m.get("lessons", [])}
    loose = [l for l in summarized_lessons if l.get("lesson_id") not in placed_ids]
    if loose:
        out.append(
            {
                "module_id": "_loose",
                "title": "(unassigned)",
                "goal": "",
                "order": 999,
                "status": None,
                "lesson_ids": [l.get("lesson_id") for l in loose],
                "mastery_threshold": None,
                "lessons": loose,
            }
        )

    out.sort(key=lambda m: (m.get("order") or 0, str(m.get("module_id") or "")))
    return out


def _course_payload(course: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(course, dict):
        return None
    lessons = _summarize_lessons(course)
    modules = _summarize_modules(course)
    assignments = _summarize_assignments(course)
    completed = sum(1 for l in lessons if (l.get("status") or "") == "completed")
    return {
        "course_id": course.get("course_id"),
        "subject": course.get("subject"),
        "target_level": course.get("target_level"),
        "status": course.get("status"),
        "directory": course.get("directory"),
        "learner_profile": course.get("learner_profile") or {},
        "current_module_id": course.get("current_module_id"),
        "current_lesson_id": course.get("current_lesson_id"),
        "current_assignment_id": course.get("current_assignment_id"),
        "lessons_completed_count": course.get("lessons_completed_count", completed),
        "lesson_total": len(lessons),
        "modules": modules,
        "lessons": lessons,
        "assignments": assignments,
        "scheduled_reviews": list(course.get("scheduled_reviews") or []),
    }


@router.get("/state")
async def get_teacher_state(request: Request) -> Dict[str, Any]:
    session = request.app.state.session_by_name()
    if session is None:
        return {
            "active": False,
            "active_course_id": None,
            "course": None,
            "courses": [],
            "raw_teacher_state_present": False,
            "registry_size": 0,
        }
    sm = session.session_manager

    # Pick the active course's metadata stub. SessionManager keeps a
    # registry; the active id points into it.
    teacher_state = sm.teacher_state
    if teacher_state is None and sm.active_course_id:
        registry_record = (sm.teacher_registry or {}).get(sm.active_course_id)
        if isinstance(registry_record, dict):
            teacher_state = registry_record

    # Stubs only carry course-level metadata. The actual curriculum +
    # learner data lives in course.json under the course directory —
    # hydrate from there so the panel sees the real picture.
    teacher_state = _hydrate_from_disk(teacher_state)
    course = _course_payload(teacher_state)

    courses = []
    for cid, record in (sm.teacher_registry or {}).items():
        if not isinstance(record, dict):
            continue
        hydrated = _hydrate_from_disk(record) or record
        courses.append(
            {
                "course_id": cid,
                "subject": hydrated.get("subject") or record.get("subject"),
                "status": hydrated.get("status") or record.get("status"),
                "is_active": cid == sm.active_course_id,
                "lesson_total": len(hydrated.get("lessons") or []),
                "lessons_completed_count": hydrated.get("lessons_completed_count", 0),
                "directory": hydrated.get("directory") or record.get("directory"),
            }
        )
    courses.sort(key=lambda c: (not c["is_active"], str(c["course_id"] or "")))

    return {
        "active": session is not None,
        "active_course_id": sm.active_course_id,
        "course": course,
        "courses": courses,
        # Diagnostics so the GUI can show a clear "saved but not loaded"
        # state when registry has data but no course is active.
        "raw_teacher_state_present": isinstance(sm.teacher_state, dict),
        "registry_size": len(sm.teacher_registry or {}),
        # Where the hydrated course data was read from, so we can debug
        # cases where the panel still looks empty.
        "course_path": (
            os.path.join(teacher_state["directory"], "course.json")
            if isinstance(teacher_state, dict) and teacher_state.get("directory")
            else None
        ),
    }
