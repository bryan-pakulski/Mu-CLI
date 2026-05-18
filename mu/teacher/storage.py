"""Course directory layout.

State lives at `<workspace>/courses/<course_id>/` — a top-level
visible directory inside whatever folder the user attached via
`/workspace folder`. No hashing, no hidden directories: the learner
can `cd` into `courses/<id>/assignments/<id>/work/` and operate on
files normally. If no workspace is attached, the engine falls back
to the current working directory.
"""

from __future__ import annotations

import os
import re
from typing import Any


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return slug or "course"


def workspace_root(folder_context: Any = None) -> str:
    """Pick the active workspace root.

    Uses the first folder attached via `/workspace folder ...`; falls
    back to cwd when nothing is attached so the engine still has a
    coherent place to write state.
    """
    if folder_context is not None:
        folders = getattr(folder_context, "folders", None) or []
        for folder in folders:
            if folder:
                return os.path.abspath(folder)
    return os.getcwd()


def courses_workspace_dir(folder_context: Any = None) -> str:
    return os.path.join(workspace_root(folder_context), "courses")


def course_directory(course_id: str, folder_context: Any = None) -> str:
    return os.path.join(courses_workspace_dir(folder_context), slugify(course_id))


def course_state_path(course_id: str, folder_context: Any = None) -> str:
    return os.path.join(course_directory(course_id, folder_context), "course.json")


def assignment_directory(course_id: str, assignment_id: str, folder_context: Any = None) -> str:
    return os.path.join(
        course_directory(course_id, folder_context),
        "assignments",
        slugify(assignment_id),
    )


def assignment_work_dir(course_id: str, assignment_id: str, folder_context: Any = None) -> str:
    return os.path.join(assignment_directory(course_id, assignment_id, folder_context), "work")


def assignment_submission_dir(
    course_id: str, assignment_id: str, folder_context: Any = None
) -> str:
    return os.path.join(
        assignment_directory(course_id, assignment_id, folder_context),
        "submission",
    )


def list_courses(folder_context: Any = None) -> list[str]:
    root = courses_workspace_dir(folder_context)
    if not os.path.isdir(root):
        return []
    entries = []
    for name in sorted(os.listdir(root)):
        candidate = os.path.join(root, name, "course.json")
        if os.path.exists(candidate):
            entries.append(name)
    return entries


def ensure_course_directory(course_id: str, folder_context: Any = None) -> str:
    directory = course_directory(course_id, folder_context)
    os.makedirs(directory, exist_ok=True)
    os.makedirs(os.path.join(directory, "modules"), exist_ok=True)
    os.makedirs(os.path.join(directory, "lessons"), exist_ok=True)
    os.makedirs(os.path.join(directory, "assignments"), exist_ok=True)
    return directory


__all__ = [
    "assignment_directory",
    "assignment_submission_dir",
    "assignment_work_dir",
    "course_directory",
    "course_state_path",
    "courses_workspace_dir",
    "ensure_course_directory",
    "list_courses",
    "slugify",
    "workspace_root",
]
