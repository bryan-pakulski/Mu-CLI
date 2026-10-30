"""Teacher-mode state and the learner's actions (``mu.teacher``).

``GET /api/teacher/state`` reads the active course through the engine's cached
loader: curriculum with each lesson's gate state (lecture checks,
comprehension, transcript, skip), assignments with grades (including
"awaiting judgement" and per-criterion rubric scores), comprehension checks,
objective mastery, due and upcoming reviews, and the learner profile.

The learner's decisions go through the engine with the surface as the actor:
``POST /approve`` (curriculum consent), ``POST /lessons/{id}/start`` (with an
``override_reason`` to start out of order), ``POST /finish-early``.
"""

from __future__ import annotations

import os
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..mode_session import mode_session, mode_session_lock
from .mode_state import save, states_for, surface_actor

router = APIRouter()


def course_payload(course) -> Dict[str, Any]:
    from mu.teacher import gates
    from mu.teacher.engine import course_metrics

    lessons = []
    for lesson in gates.ordered_lessons(course):
        data = asdict(lesson)
        data.pop("lecture_turns", None)
        data["gate"] = gates.lecture_state(course, lesson)
        data["order_blockers"] = gates.order_blockers(course, lesson) if lesson.status == "pending" else []
        data["mastery_pct"] = gates.lesson_mastery(course, lesson)
        lessons.append(data)
    modules = []
    for module in sorted(course.modules, key=lambda m: m.order):
        data = asdict(module)
        data["mastery_pct"] = gates.module_mastery(course, module)
        modules.append(data)
    assignments = []
    for assignment in course.assignments:
        data = asdict(assignment)
        data.pop("submission", None)
        assignments.append(data)
    profile = {k: v for k, v in (course.learner_profile or {}).items() if k != "calibration_history"}
    return {
        "course_id": course.course_id,
        "subject": course.subject,
        "status": course.status,
        "target_level": course.target_level,
        "revision": course.curriculum_revision,
        "curriculum_approval": course.curriculum_approval,
        "early_exit": course.early_exit,
        "current_lesson_id": course.current_lesson_id,
        "current_assignment_id": course.current_assignment_id,
        "learner_profile": profile,
        "diagnostic_recorded": gates.has_diagnostic(course),
        "modules": modules,
        "lessons": lessons,
        "assignments": assignments,
        "checks": [asdict(c) for c in course.checks[-60:]],
        "mastery": gates.mastery_grid(course),
        "course_mastery": gates.course_mastery(course),
        "due_reviews": [asdict(r) for r in gates.due_reviews(course)],
        "upcoming_reviews": [asdict(r) for r in gates.upcoming_reviews(course)[:12]],
        "metrics": course_metrics(course),
        "directory": course.directory,
        "events": [asdict(e) for e in course.event_log[-40:]],
    }


def _courses(sm) -> List[Dict[str, Any]]:
    out = []
    for cid, record in (sm.teacher_registry or {}).items():
        if not isinstance(record, dict):
            continue
        out.append({"course_id": cid, "subject": record.get("subject"), "status": record.get("status"),
                    "is_active": cid == sm.active_course_id, "lessons": record.get("lessons", 0),
                    "lessons_completed": record.get("lessons_completed", 0), "directory": record.get("directory")})
    out.sort(key=lambda c: (not c["is_active"], str(c["course_id"] or "")))
    return out


def teacher_payload(session) -> Dict[str, Any]:
    from mu.teacher import gates

    sm = session.session_manager
    course = gates.active_course(session)
    return {
        "active": str(sm.variables.get("agent_mode", "default")) == "teacher",
        "active_course_id": sm.active_course_id,
        "course": course_payload(course) if course is not None else None,
        "courses": _courses(sm),
        "course_path": gates.active_course_path(session) or None,
        "blockers": [b.to_dict() for b in sm.mode_state.get("blockers").open_blockers()]
        if getattr(sm, "mode_state", None) is not None else [],
    }


@router.get("/state")
def get_teacher_state(request: Request) -> Dict[str, Any]:
    session = mode_session(request)
    if session is None:
        return {"active": False, "active_course_id": None, "course": None, "courses": [], "course_path": None}
    return teacher_payload(session)


def _act(request: Request, fn) -> Dict[str, Any]:
    from mu.modes.events import GateError
    from mu.teacher.engine import load_course, save_course
    from mu.teacher.gates import active_course_path, course_record

    session, _states = states_for(request)
    with mode_session_lock(request, session):
        path = active_course_path(session)
        course = load_course(path) if path else None
        if course is None:
            raise HTTPException(status_code=404, detail="No active course.")
        try:
            fn(course, surface_actor(request))
        except GateError as exc:
            raise HTTPException(status_code=409, detail={"message": str(exc), "code": exc.code, **(exc.data or {})})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        save_course(course)
        record = course_record(course, getattr(session, "folder_context", None))
        session.session_manager.upsert_teacher_course(record)
        session.session_manager.teacher_state = dict(record)
        save(session)
    return teacher_payload(session)


@router.post("/approve")
def approve(request: Request) -> Dict[str, Any]:
    from mu.teacher.gates import approve_curriculum

    return _act(request, lambda course, actor: approve_curriculum(course, actor=actor))


class StartBody(BaseModel):
    override_reason: str = ""


@router.post("/lessons/{lesson_id}/start")
def start_lesson(request: Request, lesson_id: str, body: Optional[StartBody] = None) -> Dict[str, Any]:
    from mu.teacher.gates import start_lesson as start

    reason = body.override_reason if body else ""
    return _act(request, lambda course, actor: start(course, lesson_id, actor=actor, override_reason=reason))


class EarlyBody(BaseModel):
    reason: str


@router.post("/finish-early")
def finish_early(request: Request, body: EarlyBody) -> Dict[str, Any]:
    from mu.teacher.gates import early_exit

    return _act(request, lambda course, actor: early_exit(course, reason=body.reason, actor=actor))


# ── Dual presentation endpoints ─────────────────────────────────


def _resolve_course_dir(request: Request) -> Optional[str]:
    """The active course directory of the requested session."""
    from mu.teacher.gates import active_course_path

    session = mode_session(request)
    path = active_course_path(session) if session is not None else ""
    return os.path.dirname(path) if path else None


def _lesson_dir(course_dir: str, lesson_id: str) -> str:
    """Return the per-lesson directory path."""
    from mu.teacher.storage import slugify
    return os.path.join(course_dir, "lessons", slugify(lesson_id))


@router.get("/lessons/{lesson_id}/lecture")
def get_lecture_transcript(request: Request, lesson_id: str) -> Dict[str, Any]:
    """Return the authored lecture.md content for a lesson, or 404."""
    course_dir = _resolve_course_dir(request)
    if not course_dir:
        raise HTTPException(status_code=404, detail="No active course.")
    path = os.path.join(_lesson_dir(course_dir, lesson_id), "lecture.md")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="No lecture transcript for this lesson.")
    with open(path, "r", encoding="utf-8") as fh:
        content = fh.read()
    return {"lesson_id": lesson_id, "content": content}


@router.get("/lessons/{lesson_id}/exercises")
def get_exercises_listing(request: Request, lesson_id: str) -> Dict[str, Any]:
    """Return a listing of exercise file paths plus each file's contents."""
    course_dir = _resolve_course_dir(request)
    if not course_dir:
        raise HTTPException(status_code=404, detail="No active course.")
    exercises_dir = os.path.join(_lesson_dir(course_dir, lesson_id), "exercises")
    if not os.path.isdir(exercises_dir):
        return {"lesson_id": lesson_id, "files": []}
    files = []
    for root, dirs, filenames in os.walk(exercises_dir):
        dirs.sort()
        for fname in sorted(filenames):
            full = os.path.join(root, fname)
            rel = os.path.relpath(full, exercises_dir)
            try:
                with open(full, "r", encoding="utf-8") as fh:
                    content = fh.read()
            except Exception:
                content = ""
            files.append({"path": rel, "content": content})
    return {"lesson_id": lesson_id, "files": files}


@router.get("/lessons/{lesson_id}/exercises/{path:path}")
def get_exercise_file(request: Request, lesson_id: str, path: str) -> Dict[str, Any]:
    """Return a single exercise file's contents (path-validated)."""
    course_dir = _resolve_course_dir(request)
    if not course_dir:
        raise HTTPException(status_code=404, detail="No active course.")
    exercises_dir = os.path.join(_lesson_dir(course_dir, lesson_id), "exercises")
    target = os.path.normpath(os.path.join(exercises_dir, path))
    # Path traversal guard: resolved path must stay under exercises_dir.
    if not os.path.abspath(target).startswith(os.path.abspath(exercises_dir)):
        raise HTTPException(status_code=403, detail="Path traversal not allowed.")
    if not os.path.isfile(target):
        raise HTTPException(status_code=404, detail="Exercise file not found.")
    with open(target, "r", encoding="utf-8") as fh:
        content = fh.read()
    rel = os.path.relpath(target, exercises_dir)
    return {"lesson_id": lesson_id, "path": rel, "content": content}
