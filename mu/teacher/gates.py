"""Teacher gates, lecture tracking, mastery and spaced repetition (modes v2 §4.6).

The engine (``mu.teacher.engine``) holds the course model; this module holds
the rules every surface goes through:

* **Curriculum** — ``propose_curriculum`` needs a recorded diagnostic; on a
  course already in progress it makes a *revision* that keeps graded lessons,
  their assignments and history. ``approve_curriculum`` is the learner's
  decision (user actors only: the panel, ``/teach approve``, mobile).
* **Order** — ``start_lesson`` needs an approved curriculum and respects the
  order: earlier lessons completed, earlier modules completed with
  ``complete_module``. A user override is recorded with its reason.
* **Lecture** — ``teach_check`` records a comprehension check (choice checks
  capture the pick exactly; open checks take the learner's next message);
  ``assess_response`` records the tutor's signal. The lecture concludes from
  the engine once enough checks are assessed and comprehension reaches the
  threshold. ``assign_gate`` refuses an assignment before that (or before a
  recorded known-concept skip) and before the lecture transcript exists.
* **Verifiers** — an exercise's verifier runs against the starting artifact
  and must fail there; it runs in the assignment's ``work/`` directory via
  ``run_check`` (in the sandbox container when Docker is available).
* **Mastery** — per learning objective: the best of the recent attempts, with
  decay. Lessons, modules, the course and the report card use the same rule.
* **Reviews** — an SM-2 schedule per objective with dates, set by the engine
  after each grade and review.
"""

from __future__ import annotations

import math
import os
import re
import time
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Tuple

from mu.modes.events import AGENT, GateError, is_user, normalize_actor, require_user

from . import storage as _storage
from .engine import (
    COURSE_CURRICULUM_PROPOSED, COURSE_DIAGNOSING, COURSE_IN_PROGRESS,
    LESSON_ASSIGNED, LESSON_COMPLETED, LESSON_LECTURING, LESSON_PENDING, LESSON_PRESENTING,
    LESSON_REMEDIATING, REVIEW_DONE, REVIEW_PENDING, Assignment, ComprehensionCheck, Course, Lesson,
    Module, ScheduledReview, add_event, advance_lesson_status, find_assignment, find_lesson, find_module,
    read_course,
)

SIGNALS = {"on_track": 100, "partial": 50, "confused": 0}
RECENT_ATTEMPTS = 5
DECAY_HALF_LIFE_DAYS = 45.0
DAY = 86400.0
EXEC_KINDS = {"fix-broken-code", "implement-from-scratch", "command-output"}


# ------------------------------------------------------------------ session pointer


def course_record(course: Course, folder_context: Any = None) -> Dict[str, Any]:
    """The session's pointer to a course (the one place it is built)."""
    lesson = find_lesson(course, course.current_lesson_id) if course.current_lesson_id else None
    path = os.path.join(course.directory, "course.json") if course.directory else \
        _storage.course_state_path(course.course_id, folder_context)
    return {
        "type": "course",
        "course_id": course.course_id,
        "subject": course.subject,
        "directory": course.directory,
        "course_path": path,
        "status": course.status,
        "current_lesson_id": course.current_lesson_id,
        "current_lesson_status": lesson.status if lesson else None,
        "revision": course.curriculum_revision,
        "lessons": len(course.lessons),
        "lessons_completed": sum(1 for l in course.lessons if l.status == LESSON_COMPLETED),
        "updated_at": course.updated_at,
        "created_at": course.created_at,
    }


def active_course_path(session: Any) -> str:
    manager = getattr(session, "session_manager", None)
    state = getattr(manager, "teacher_state", None)
    return str((state or {}).get("course_path") or "") if isinstance(state, dict) else ""


def active_course(session: Any) -> Optional[Course]:
    """Read-only active course through the cached loader."""
    return read_course(active_course_path(session))


# ------------------------------------------------------------------ curriculum


def has_diagnostic(course: Course) -> bool:
    return bool((course.learner_profile or {}).get("recorded_at"))


def _parse_modules(raw_modules: List[Dict[str, Any]]) -> Tuple[List[Module], List[Lesson]]:
    modules: List[Module] = []
    lessons: List[Lesson] = []
    for idx, raw in enumerate(raw_modules or [], start=1):
        module_id = _storage.slugify(str(raw.get("module_id") or f"module_{idx}"))
        module = Module(module_id=module_id, title=str(raw.get("title") or module_id),
                        goal=str(raw.get("goal", "") or ""), order=int(raw.get("order", idx) or idx),
                        mastery_threshold=int(raw.get("mastery_threshold", 70) or 70))
        for raw_lesson in raw.get("lessons") or []:
            lesson_id = _storage.slugify(str(raw_lesson.get("lesson_id") or raw_lesson.get("title") or ""))
            if not lesson_id:
                continue
            objectives = [str(x).strip() for x in raw_lesson.get("learning_objectives") or [] if str(x).strip()]
            if not 1 <= len(objectives) <= 4:
                raise ValueError(f"lesson {lesson_id!r} needs 1–4 learning_objectives (has {len(objectives)})")
            lessons.append(Lesson(
                lesson_id=lesson_id, module_id=module_id, title=str(raw_lesson.get("title") or lesson_id),
                learning_objectives=objectives, concept_brief=str(raw_lesson.get("concept_brief", "") or ""),
                sources=[{str(k): str(v) for k, v in src.items() if k in {"title", "url", "kind", "note"}}
                         for src in raw_lesson.get("sources") or []
                         if isinstance(src, dict) and str(src.get("url") or "").strip()],
            ))
            module.lesson_ids.append(lesson_id)
        modules.append(module)
    if not lessons:
        raise ValueError("a curriculum needs modules with lessons")
    return modules, lessons


def _has_history(course: Course, lesson: Lesson) -> bool:
    return lesson.status != LESSON_PENDING or any(a.lesson_id == lesson.lesson_id for a in course.assignments)


def propose_curriculum(course: Course, raw_modules: List[Dict[str, Any]], *, actor: str = AGENT) -> Dict[str, Any]:
    """Replace the proposed curriculum, or revise an in-progress one keeping every
    lesson with history (status beyond pending, or assignments)."""
    actor = normalize_actor(actor)
    if not has_diagnostic(course):
        raise GateError("Record the diagnostic first (record_diagnostic) — the curriculum is built on it.",
                        code="diagnostic_required")
    modules, lessons = _parse_modules(raw_modules)
    kept: List[str] = []
    if course.status in (COURSE_IN_PROGRESS, COURSE_CURRICULUM_PROPOSED) and course.lessons:
        new_ids = {l.lesson_id for l in lessons}
        for old in course.lessons:
            if not _has_history(course, old):
                continue
            kept.append(old.lesson_id)
            replacement = next((l for l in lessons if l.lesson_id == old.lesson_id), None)
            if replacement is not None:
                lessons[lessons.index(replacement)] = old
                old.module_id = replacement.module_id
            else:
                # A graded lesson the new plan dropped stays in its old module.
                lessons.append(old)
                module = next((m for m in modules if m.module_id == old.module_id), None)
                if module is None:
                    module = Module(module_id=old.module_id, title="Earlier lessons", order=0)
                    modules.insert(0, module)
                module.lesson_ids.append(old.lesson_id)
                new_ids.add(old.lesson_id)
    course.modules, course.lessons = modules, lessons
    previous_approval = course.curriculum_approval
    course.curriculum_revision += 1
    course.curriculum_approval = None
    course.status = COURSE_CURRICULUM_PROPOSED
    add_event(course, kind="curriculum_proposed", entity="course", entity_id=course.course_id,
              payload={"modules": len(modules), "lessons": len(lessons), "kept": kept,
                       "revision": course.curriculum_revision,
                       "replaces_approved": bool(previous_approval)}, actor=actor)
    return {"kept": kept, "revision": course.curriculum_revision}


def approve_curriculum(course: Course, *, actor: str) -> Course:
    actor = normalize_actor(actor)
    require_user(actor, "Approving the curriculum")
    if course.status != COURSE_CURRICULUM_PROPOSED:
        raise GateError(f"The course is {course.status}; there is no proposed curriculum to approve.",
                        code="wrong_status")
    course.curriculum_approval = {"actor": actor, "at": time.time(), "revision": course.curriculum_revision}
    course.status = COURSE_IN_PROGRESS
    nxt = next_lesson(course)
    if nxt is not None and not course.current_lesson_id:
        course.current_module_id, course.current_lesson_id = nxt.module_id, nxt.lesson_id
    add_event(course, kind="curriculum_approved", entity="course", entity_id=course.course_id,
              payload={"revision": course.curriculum_revision}, actor=actor)
    return course


# ------------------------------------------------------------------ order


def ordered_lessons(course: Course) -> List[Lesson]:
    out: List[Lesson] = []
    for module in sorted(course.modules, key=lambda m: m.order):
        for lesson_id in module.lesson_ids:
            lesson = find_lesson(course, lesson_id)
            if lesson is not None:
                out.append(lesson)
    return out


def next_lesson(course: Course) -> Optional[Lesson]:
    return next((l for l in ordered_lessons(course) if l.status != LESSON_COMPLETED), None)


def order_blockers(course: Course, lesson: Lesson) -> List[str]:
    blockers: List[str] = []
    modules = sorted(course.modules, key=lambda m: m.order)
    module = find_module(course, lesson.module_id)
    for earlier in modules:
        if module is not None and earlier.order >= module.order:
            break
        if earlier.status != LESSON_COMPLETED:
            blockers.append(f"module {earlier.module_id} is not completed (complete_module)")
    if module is not None:
        for lesson_id in module.lesson_ids:
            if lesson_id == lesson.lesson_id:
                break
            earlier_lesson = find_lesson(course, lesson_id)
            if earlier_lesson is not None and earlier_lesson.status != LESSON_COMPLETED:
                blockers.append(f"lesson {lesson_id} is not completed")
    return blockers


def start_lesson(course: Course, lesson_id: str, *, actor: str = AGENT, override_reason: str = "") -> Lesson:
    actor = normalize_actor(actor)
    if course.status != COURSE_IN_PROGRESS or course.curriculum_approval is None:
        raise GateError("The curriculum is not approved yet — the learner approves it (/teach approve or the "
                        "panel).", code="not_approved")
    lesson = find_lesson(course, lesson_id)
    if lesson is None:
        raise GateError(f"Lesson {lesson_id!r} not found.", code="not_found")
    if lesson.status not in (LESSON_PENDING, LESSON_REMEDIATING, LESSON_PRESENTING):
        raise GateError(f"Lesson {lesson_id} is {lesson.status}.", code="wrong_status")
    blockers = order_blockers(course, lesson)
    if blockers:
        if not is_user(actor):
            raise GateError("Out of order: " + "; ".join(blockers) + ".", code="out_of_order", blockers=blockers)
        if not str(override_reason or "").strip():
            raise GateError("Starting out of order needs an override reason.", code="override_reason_required")
        lesson.override = {"actor": actor, "reason": override_reason, "at": time.time(), "skipped": blockers}
        add_event(course, kind="override", entity="lesson", entity_id=lesson_id,
                  payload={"reason": override_reason, "blockers": blockers}, actor=actor)
    if lesson.status == LESSON_PENDING:
        advance_lesson_status(course, lesson_id, LESSON_PRESENTING)
    course.current_module_id, course.current_lesson_id = lesson.module_id, lesson.lesson_id
    return lesson


# ------------------------------------------------------------------ lecture tracking


def checks_for(course: Course, lesson_id: str) -> List[ComprehensionCheck]:
    return [c for c in course.checks if c.lesson_id == lesson_id]


def lecture_state(course: Course, lesson: Lesson) -> Dict[str, Any]:
    checks = checks_for(course, lesson.lesson_id)
    assessed = [c for c in checks if c.signal in SIGNALS]
    pct = int(round(sum(SIGNALS[c.signal] for c in assessed) / len(assessed))) if assessed else None
    return {"checks": len(checks), "answered": sum(1 for c in checks if c.answer is not None),
            "assessed": len(assessed), "comprehension_pct": pct, "required": lesson.min_lecture_checks,
            "threshold": lesson.lecture_comprehension_threshold, "concluded": lesson.lecture_concluded,
            "transcript": bool(lesson.lecture_transcript_path), "skip": lesson.known_concept_skip}


def _lecturing_lesson(course: Course, lesson_id: str) -> Lesson:
    lesson = find_lesson(course, lesson_id or course.current_lesson_id or "")
    if lesson is None:
        raise GateError("No current lesson — start_lesson first.", code="not_found")
    if lesson.status == LESSON_PRESENTING or lesson.status == LESSON_REMEDIATING:
        advance_lesson_status(course, lesson.lesson_id, LESSON_LECTURING)
    if lesson.status != LESSON_LECTURING:
        raise GateError(f"Lesson {lesson.lesson_id} is {lesson.status}; checks belong to the lecture.",
                        code="wrong_status")
    return lesson


def teach_check(course: Course, *, question: str, kind: str = "open", options: Optional[List[str]] = None,
                lesson_id: str = "", objective: str = "") -> ComprehensionCheck:
    if not str(question or "").strip():
        raise ValueError("A check needs a question.")
    kind = str(kind or "open").lower()
    if kind not in ("open", "choice"):
        raise ValueError("kind is open or choice")
    opts = [str(o).strip() for o in options or [] if str(o).strip()]
    if kind == "choice" and len(opts) < 2:
        raise ValueError("A choice check needs at least two options.")
    lesson = _lecturing_lesson(course, lesson_id)
    if objective and objective not in lesson.learning_objectives:
        raise ValueError(f"objective must be one of the lesson's: {lesson.learning_objectives}")
    check = ComprehensionCheck(check_id=f"k{len(course.checks) + 1}", lesson_id=lesson.lesson_id,
                               question=str(question).strip(), kind=kind, options=opts, objective=objective)
    course.checks.append(check)
    add_event(course, kind="check_asked", entity="lesson", entity_id=lesson.lesson_id,
              payload={"check": check.check_id, "kind": kind})
    return check


def record_answer(course: Course, check_id: str, answer: str, *, actor: str = "user:chat") -> ComprehensionCheck:
    check = next((c for c in course.checks if c.check_id == check_id), None)
    if check is None:
        raise GateError(f"No check {check_id!r}.", code="not_found")
    check.answer, check.answered_at = str(answer or "").strip(), time.time()
    add_event(course, kind="check_answered", entity="lesson", entity_id=check.lesson_id,
              payload={"check": check.check_id}, actor=normalize_actor(actor))
    return check


def attach_learner_message(course: Course, text: str) -> Optional[ComprehensionCheck]:
    """The learner's message answers the latest open, unanswered check of the current lesson."""
    lesson_id = course.current_lesson_id
    if not lesson_id or not str(text or "").strip():
        return None
    pending = [c for c in checks_for(course, lesson_id) if c.kind == "open" and c.answer is None]
    if not pending:
        return None
    return record_answer(course, pending[-1].check_id, text, actor="user:chat")


def assess_response(course: Course, check_id: str, signal: str, note: str = "") -> Dict[str, Any]:
    check = next((c for c in course.checks if c.check_id == check_id), None)
    if check is None:
        raise GateError(f"No check {check_id!r}.", code="not_found")
    if check.answer is None:
        raise GateError(f"{check_id} has no learner answer yet; wait for their reply.", code="no_answer")
    if signal not in SIGNALS:
        raise ValueError(f"signal must be one of {', '.join(SIGNALS)}")
    check.signal, check.note = signal, str(note or "").strip()[:500]
    lesson = find_lesson(course, check.lesson_id)
    concluded_now = False
    if lesson is not None and not lesson.lecture_concluded:
        state = lecture_state(course, lesson)
        if state["assessed"] >= lesson.min_lecture_checks and (state["comprehension_pct"] or 0) >= \
                lesson.lecture_comprehension_threshold:
            lesson.lecture_concluded = True
            lesson.lecture_comprehension_pct = state["comprehension_pct"]
            lesson.lecture_gaps = [c.note or c.question for c in checks_for(course, lesson.lesson_id)
                                   if c.signal in ("partial", "confused")][:6]
            concluded_now = True
            add_event(course, kind="lecture_concluded", entity="lesson", entity_id=lesson.lesson_id,
                      payload={"comprehension_pct": state["comprehension_pct"], "checks": state["assessed"]})
    add_event(course, kind="check_assessed", entity="lesson", entity_id=check.lesson_id,
              payload={"check": check_id, "signal": signal})
    return {"check": asdict(check), "concluded": concluded_now,
            "lecture": lecture_state(course, lesson) if lesson else {}}


def known_concepts(course: Course) -> List[str]:
    profile = course.learner_profile or {}
    return [str(x) for key in ("strengths", "anchors", "background") for x in profile.get(key) or []]


def skip_lecture(course: Course, lesson_id: str, *, concept: str, reason: str, actor: str = AGENT) -> Lesson:
    lesson = find_lesson(course, lesson_id)
    if lesson is None:
        raise GateError(f"Lesson {lesson_id!r} not found.", code="not_found")
    concept = str(concept or "").strip()
    known = known_concepts(course)
    if not concept or not any(concept.lower() in k.lower() or k.lower() in concept.lower() for k in known):
        raise GateError("A lecture is skipped only for a concept the diagnostic marks known "
                        f"(strengths / anchors / background: {known[:8]}).", code="not_known")
    if not str(reason or "").strip():
        raise ValueError("Say why the learner already knows this.")
    lesson.known_concept_skip = {"concept": concept, "reason": str(reason).strip(),
                                 "actor": normalize_actor(actor), "at": time.time()}
    add_event(course, kind="lecture_skipped", entity="lesson", entity_id=lesson_id,
              payload=dict(lesson.known_concept_skip), actor=normalize_actor(actor))
    return lesson


def assign_gate(course: Course, lesson: Lesson) -> None:
    if lesson.status in (LESSON_ASSIGNED,) or lesson.status == LESSON_REMEDIATING:
        return
    if lesson.known_concept_skip:
        return
    problems = []
    state = lecture_state(course, lesson)
    if not lesson.lecture_concluded:
        problems.append(f"the lecture has not concluded (assessed checks {state['assessed']} of "
                        f"{lesson.min_lecture_checks}, comprehension {state['comprehension_pct']}% vs "
                        f"{lesson.lecture_comprehension_threshold}%) — teach, teach_check, assess_response")
    if not lesson.lecture_transcript_path:
        problems.append("the lecture transcript is missing (write_lecture_transcript)")
    if problems:
        raise GateError("Not ready to assign: " + "; ".join(problems) + ".", code="lecture_incomplete")


# ------------------------------------------------------------------ verifier runs


def work_dir(course: Course, assignment: Assignment) -> str:
    return os.path.join(course.directory, "assignments", _storage.slugify(assignment.assignment_id), "work")


def run_verifier(course: Course, assignment: Assignment, owner: Any, *, purpose: str = "grader"):
    """Run the verify_cmd in the assignment's work/ dir through run_check."""
    from mu.modes.evidence import CheckSpec, run_check
    from mu.modes.sandbox import docker_available

    spec = assignment.verification
    if not spec.verify_cmd:
        raise ValueError(f"assignment {assignment.assignment_id} has no verify_cmd")
    cwd = work_dir(course, assignment)
    os.makedirs(cwd, exist_ok=True)
    predicate: Dict[str, Any] = {"all": [{"markers": list(spec.expected_markers)}]} if spec.expected_markers else {"exit": 0}
    if spec.forbidden_markers:
        predicate = {"all": [predicate, {"markers": list(spec.forbidden_markers), "absent": True}]}
    return run_check(CheckSpec(command=spec.verify_cmd, purpose=purpose, predicate=predicate, cwd=cwd,
                               timeout_s=int(spec.timeout_seconds or 30), ref=assignment.assignment_id,
                               runner="docker" if docker_available() else "host",
                               label="Verifier on the starting files" if purpose == "probe" else "Grader"),
                     owner)


def precheck_verifier(course: Course, assignment: Assignment, owner: Any) -> Any:
    """The verifier must FAIL on the starting artifact, or it proves nothing."""
    if assignment.kind not in EXEC_KINDS or not assignment.verification.verify_cmd:
        return None
    run = run_verifier(course, assignment, owner, purpose="probe")
    if run.passed:
        raise GateError("The verifier already passes on the starting files, so it cannot tell whether the "
                        "learner did anything. Make the starting artifact incomplete or tighten the markers.",
                        code="verifier_trivial", run=run.id)
    assignment.precheck_run = run.id
    return run


def grade_exec(course: Course, assignment: Assignment, owner: Any):
    from mu.modes.evidence import read_output

    from .engine import Grade

    run = run_verifier(course, assignment, owner)
    spec = assignment.verification
    output = read_output(run)
    missing = [m for m in spec.expected_markers or [] if m not in output]
    forbidden = [m for m in spec.forbidden_markers or [] if m in output]
    ratio = ((len(spec.expected_markers) - len(missing)) / len(spec.expected_markers)) if spec.expected_markers \
        else (1.0 if run.passed else 0.0)
    if forbidden or run.timed_out:
        ratio = 0.0
    score = 100 if run.passed else int(round(100 * ratio))
    feedback = "Verification produced every expected marker." if run.passed else "; ".join(filter(None, [
        ("Missing expected markers: " + ", ".join(f"`{m}`" for m in missing)) if missing else "",
        ("Forbidden markers were emitted: " + ", ".join(f"`{m}`" for m in forbidden)) if forbidden else "",
        f"Timed out after {spec.timeout_seconds}s." if run.timed_out else ""])) or run.detail
    return Grade(score_pct=score, passed=bool(run.passed), check_run=run.id,
                 verification_result={"method": "exec_markers", "verify_cmd": spec.verify_cmd, "cwd": run.cwd,
                                       "exit_code": run.exit_code, "timed_out": run.timed_out,
                                       "missing_markers": missing, "forbidden_markers_present": forbidden,
                                       "runner": run.runner, "excerpt": run.excerpt},
                 feedback=feedback)


def grade_rubric(assignment: Assignment, rubric_scores: List[Dict[str, Any]], feedback: str = ""):
    """Weighted rubric: per-criterion scores, weights from the assignment's rubric."""
    from .engine import Grade

    by_name = {str(r.get("criterion") or "").strip().lower(): r for r in rubric_scores or [] if isinstance(r, dict)}
    breakdown, total_weight, total = [], 0, 0.0
    for item in assignment.rubric:
        scored = by_name.get(item.criterion.strip().lower())
        if scored is None:
            raise ValueError(f"score every rubric criterion; missing {item.criterion!r}")
        value = max(0, min(100, int(scored.get("score_pct", 0))))
        weight = max(1, int(item.weight or 1))
        breakdown.append({"criterion": item.criterion, "weight": weight, "score_pct": value,
                          "note": str(scored.get("note") or "")})
        total += value * weight
        total_weight += weight
    score = int(round(total / total_weight)) if total_weight else 0
    passed = score >= assignment.pass_threshold
    return Grade(score_pct=score, passed=passed, rubric_breakdown=breakdown,
                 verification_result={"method": "rubric_judge", "stage": "judged", "weighted": True},
                 feedback=str(feedback or "").strip() or f"Rubric: {score}% ({'pass' if passed else 'fail'}).")


# ------------------------------------------------------------------ mastery


def _objectives_of(course: Course, assignment: Assignment) -> List[str]:
    if assignment.objectives:
        return list(assignment.objectives)
    lesson = find_lesson(course, assignment.lesson_id)
    return list(lesson.learning_objectives) if lesson else []


def objective_attempts(course: Course) -> Dict[str, List[Tuple[int, float]]]:
    out: Dict[str, List[Tuple[int, float]]] = {}
    for assignment in course.assignments:
        grade = assignment.grade
        if grade is None or getattr(grade, "status", "graded") != "graded":
            continue
        for objective in _objectives_of(course, assignment):
            out.setdefault(objective, []).append((int(grade.score_pct), float(grade.graded_at or 0)))
    for attempts in out.values():
        attempts.sort(key=lambda a: a[1])
    return out


def mastery_from(attempts: List[Tuple[int, float]], now: Optional[float] = None) -> Optional[int]:
    """Best of the most recent attempts, each decayed by its age."""
    if not attempts:
        return None
    now = now or time.time()
    recent = attempts[-RECENT_ATTEMPTS:]
    best = max(score * math.pow(0.5, max(0.0, now - at) / DAY / DECAY_HALF_LIFE_DAYS) for score, at in recent)
    return int(round(best))


def mastery_grid(course: Course, now: Optional[float] = None) -> List[Dict[str, Any]]:
    attempts = objective_attempts(course)
    rows = []
    for lesson in ordered_lessons(course):
        for objective in lesson.learning_objectives:
            hist = attempts.get(objective, [])
            rows.append({"lesson_id": lesson.lesson_id, "module_id": lesson.module_id, "objective": objective,
                         "mastery_pct": mastery_from(hist, now), "attempts": len(hist)})
    return rows


def _mean(values: List[int]) -> Optional[int]:
    values = [v for v in values if v is not None]
    return int(round(sum(values) / len(values))) if values else None


def lesson_mastery(course: Course, lesson: Lesson, now: Optional[float] = None) -> Optional[int]:
    attempts = objective_attempts(course)
    return _mean([mastery_from(attempts.get(o, []), now) for o in lesson.learning_objectives])


def module_mastery(course: Course, module: Module, now: Optional[float] = None) -> Optional[int]:
    grid = [r for r in mastery_grid(course, now) if r["module_id"] == module.module_id]
    return _mean([r["mastery_pct"] for r in grid])


def course_mastery(course: Course, now: Optional[float] = None) -> Optional[int]:
    return _mean([r["mastery_pct"] for r in mastery_grid(course, now)])


def complete_module(course: Course, module_id: str, *, actor: str = AGENT) -> int:
    module = find_module(course, module_id)
    if module is None:
        raise GateError(f"Module {module_id!r} not found.", code="not_found")
    incomplete = [lid for lid in module.lesson_ids
                  if (find_lesson(course, lid) or Lesson(lesson_id="", module_id="", title="")).status != LESSON_COMPLETED]
    if incomplete:
        raise GateError("Lessons not yet completed: " + ", ".join(incomplete), code="lessons_incomplete")
    mastery = module_mastery(course, module) or 0
    if mastery < module.mastery_threshold:
        raise GateError(f"Module mastery {mastery}% is below its threshold {module.mastery_threshold}%; "
                        "remediate the weakest objective first.", code="mastery_low")
    module.status = LESSON_COMPLETED
    add_event(course, kind="module_completed", entity="module", entity_id=module_id,
              payload={"mastery_pct": mastery}, actor=normalize_actor(actor))
    return mastery


def early_exit(course: Course, *, reason: str, actor: str) -> Course:
    actor = normalize_actor(actor)
    require_user(actor, "Finishing the course early")
    if not str(reason or "").strip():
        raise ValueError("An early finish needs a reason.")
    course.early_exit = {"actor": actor, "reason": str(reason).strip(), "at": time.time()}
    add_event(course, kind="early_exit", entity="course", entity_id=course.course_id,
              payload={"reason": reason}, actor=actor)
    return course


def finalize_gate(course: Course) -> None:
    pending = [l.lesson_id for l in course.lessons if l.status != LESSON_COMPLETED]
    if pending and not course.early_exit:
        raise GateError("Lessons not passed: " + ", ".join(pending) + ". Finish them, or the learner confirms an "
                        "early finish (/teach finish --early <reason>).", code="lessons_incomplete")


# ------------------------------------------------------------------ spaced repetition (SM-2)


def _sm2(review: ScheduledReview, score_pct: int) -> None:
    quality = max(0, min(5, int(round(score_pct / 20))))
    if quality < 3:
        review.repetitions = 0
        review.interval_days = 1.0
    else:
        review.repetitions += 1
        if review.repetitions == 1:
            review.interval_days = 1.0
        elif review.repetitions == 2:
            review.interval_days = 6.0
        else:
            review.interval_days = round(review.interval_days * review.ease, 1)
    review.ease = max(1.3, review.ease + 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))


def schedule_after_grade(course: Course, assignment: Assignment, *, now: Optional[float] = None) -> List[ScheduledReview]:
    """After a graded attempt, (re)schedule a review per objective by date."""
    now = now or time.time()
    grade = assignment.grade
    if grade is None or getattr(grade, "status", "graded") != "graded":
        return []
    out = []
    for objective in _objectives_of(course, assignment):
        review = next((r for r in course.scheduled_reviews if r.objective == objective and r.status == REVIEW_PENDING), None)
        if review is None:
            prior = [r for r in course.scheduled_reviews if r.objective == objective]
            review = ScheduledReview(review_id=f"rv{len(course.scheduled_reviews) + 1}",
                                     source_lesson_id=assignment.lesson_id, objective=objective)
            if prior:
                review.ease, review.repetitions, review.interval_days = prior[-1].ease, prior[-1].repetitions, prior[-1].interval_days
            course.scheduled_reviews.append(review)
        _sm2(review, int(grade.score_pct))
        review.due_at = now + review.interval_days * DAY
        out.append(review)
    return out


def due_reviews(course: Course, now: Optional[float] = None) -> List[ScheduledReview]:
    now = now or time.time()
    due = [r for r in course.scheduled_reviews if r.status == REVIEW_PENDING and r.due_at and r.due_at <= now]
    due.sort(key=lambda r: r.due_at)
    return due


def upcoming_reviews(course: Course) -> List[ScheduledReview]:
    pending = [r for r in course.scheduled_reviews if r.status == REVIEW_PENDING and r.due_at]
    pending.sort(key=lambda r: r.due_at)
    return pending


def complete_review(course: Course, review_id: str, *, assignment_id: str, now: Optional[float] = None) -> ScheduledReview:
    """A review is a re-graded exercise: its grade updates the schedule and mastery."""
    now = now or time.time()
    review = next((r for r in course.scheduled_reviews if r.review_id == review_id), None)
    if review is None:
        raise GateError(f"No review {review_id!r}.", code="not_found")
    if review.status != REVIEW_PENDING:
        raise GateError(f"Review {review_id} is already {review.status}.", code="wrong_status")
    assignment = find_assignment(course, assignment_id)
    if assignment is None or assignment.grade is None or getattr(assignment.grade, "status", "graded") != "graded":
        raise GateError("Complete a review with a graded review exercise (assign_exercise(review_id=...), then "
                        "grade it).", code="not_graded")
    if not assignment.objectives:
        assignment.objectives = [review.objective] if review.objective else assignment.objectives
    review.status, review.score_pct, review.completed_at = REVIEW_DONE, int(assignment.grade.score_pct), now
    review.assignment_id = assignment.assignment_id
    nxt = ScheduledReview(review_id=f"rv{len(course.scheduled_reviews) + 1}", source_lesson_id=review.source_lesson_id,
                          objective=review.objective, ease=review.ease, repetitions=review.repetitions,
                          interval_days=review.interval_days)
    _sm2(nxt, review.score_pct)
    nxt.due_at = now + nxt.interval_days * DAY
    course.scheduled_reviews.append(nxt)
    add_event(course, kind="review_completed", entity="review", entity_id=review.review_id,
              payload={"score_pct": review.score_pct, "next_due": nxt.due_at})
    return review


# ------------------------------------------------------------------ learner model across courses


def remember_profile(session: Any, course: Course, note: str = "") -> str:
    getter = getattr(getattr(session, "session_manager", None), "get_durable_memory_service", None)
    profile = course.learner_profile or {}
    if getter is None or not profile:
        return ""
    parts = []
    for key in ("background", "anchors", "strengths", "modality", "stumbling_blocks"):
        values = profile.get(key) or []
        if values:
            parts.append(f"{key}: {', '.join(str(v) for v in values[:8])}")
    for key in ("pace", "jargon_tolerance", "personality"):
        if profile.get(key):
            parts.append(f"{key}: {profile[key]}")
    if not parts:
        return ""
    statement = f"Learner profile ({course.subject}): " + "; ".join(parts) + (f". {note}" if note else "")
    try:
        item, _ = getter().remember(session, statement, kind="preference", scope="personal",
                                    tags=["learner-profile", f"course:{course.course_id}"],
                                    source_refs=[{"type": "teacher_course", "course": course.course_id}],
                                    actor="model", trust_origin="model", confidence=0.8,
                                    metadata={"learner_profile": {k: v for k, v in profile.items()
                                                                  if k != "calibration_history"}})
        return str(getattr(item, "id", "") or "")
    except Exception:  # noqa: BLE001 — the course keeps the profile regardless
        return ""


def prior_learner_model(session: Any, limit: int = 3) -> List[str]:
    getter = getattr(getattr(session, "session_manager", None), "get_durable_memory_service", None)
    if getter is None:
        return []
    try:
        items = getter().search_for_session(session, "learner profile", limit=limit * 4)
    except Exception:  # noqa: BLE001
        return []
    return [str(getattr(i, "statement", "")) for i in items
            if "learner-profile" in [str(t).lower() for t in getattr(i, "tags", []) or []]][:limit]


def merge_profile(existing: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    """Re-running the diagnostic merges: lists union, scalars take the new value."""
    out = dict(existing or {})
    for key, value in (incoming or {}).items():
        if isinstance(value, list):
            merged = list(out.get(key) or [])
            for item in value:
                if item not in merged:
                    merged.append(item)
            out[key] = merged
        elif value not in (None, ""):
            out[key] = value
    return out


# ------------------------------------------------------------------ sources


def teach_sources(course: Course, lesson_id: str, citation_ids: List[int], research_state: Any) -> Lesson:
    lesson = find_lesson(course, lesson_id)
    if lesson is None:
        raise GateError(f"Lesson {lesson_id!r} not found.", code="not_found")
    numbered = {s.citation_id: s for s in research_state.numbered_sources()} if research_state is not None else {}
    unknown = [c for c in citation_ids if int(c) not in numbered]
    if unknown:
        raise GateError(f"Citation(s) {unknown} are not sources you have read (url_grounding / research_fetch "
                        "first).", code="not_read")
    for cid in citation_ids:
        cid = int(cid)
        if cid not in lesson.citations:
            lesson.citations.append(cid)
        src = numbered[cid]
        if not any(s.get("url") == src.url for s in lesson.sources):
            lesson.sources.append({"title": src.title, "url": src.url, "kind": src.source_type.value,
                                   "note": f"[^{cid}]"})
    add_event(course, kind="sources", entity="lesson", entity_id=lesson_id, payload={"citations": citation_ids})
    return lesson


# ------------------------------------------------------------------ chat signals


_INLINE_MC = re.compile(r"^\s*(?:\(?[A-Da-d][\).:]|[1-4][\).])\s+\S", re.M)


def inline_choices(text: str) -> bool:
    """True when the text lists answer options inline (A) … B) …)."""
    return len(_INLINE_MC.findall(str(text or ""))) >= 2


# ------------------------------------------------------------------ capsule


def _profile_lines(profile: Dict[str, Any]) -> List[str]:
    lines = []
    for key, label in (("background", "Background (analogy anchors)"), ("modality", "Learns best via"),
                       ("pace", "Pace"), ("jargon_tolerance", "Jargon tolerance"), ("gaps", "Gaps"),
                       ("stumbling_blocks", "Stumbling blocks"), ("personality", "Voice")):
        value = profile.get(key)
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value[:6])
        if value:
            lines.append(f"{label}: {str(value)[:160]}")
    return lines


def capsule(session: Any) -> str:
    course = active_course(session)
    if course is None:
        lines = ["No active course: create_course(subject), then the diagnostic."]
        prior = prior_learner_model(session)
        if prior:
            lines.append("Known about this learner from earlier courses: " + " | ".join(p[:200] for p in prior))
        return "\n".join(lines)
    done = sum(1 for l in course.lessons if l.status == LESSON_COMPLETED)
    lines = [f"Course {course.course_id} \"{course.subject}\" — {course.status}, {done} of {len(course.lessons)} lessons."]
    if course.status == COURSE_DIAGNOSING and not has_diagnostic(course):
        lines.append("Next: the diagnostic conversation, then record_diagnostic.")
    elif course.status == COURSE_CURRICULUM_PROPOSED:
        lines.append("Waiting for the learner to approve the curriculum (/teach approve or the panel).")
    lesson = find_lesson(course, course.current_lesson_id) if course.current_lesson_id else None
    if lesson is not None and lesson.status != LESSON_COMPLETED:
        state = lecture_state(course, lesson)
        lines.append(f"Lesson {lesson.lesson_id} \"{lesson.title}\" — {lesson.status}; checks assessed "
                     f"{state['assessed']}/{state['required']}, comprehension {state['comprehension_pct']}% "
                     f"(needs {state['threshold']}%); transcript {'yes' if state['transcript'] else 'no'}.")
        pending = [c for c in checks_for(course, lesson.lesson_id) if c.answer is not None and c.signal is None]
        if pending:
            lines.append("Answered, not yet assessed: " + ", ".join(f"{c.check_id} ({c.answer[:60]!r})" for c in pending[:3]))
    due = due_reviews(course)
    if due:
        lines.append("Reviews due: " + ", ".join(f"{r.review_id} ({r.objective[:40] or r.source_lesson_id})" for r in due[:4]))
    profile = _profile_lines(course.learner_profile or {})
    if profile:
        lines.append("LEARNER PROFILE — " + " · ".join(profile))
    return "\n".join(lines)


__all__ = [
    "SIGNALS", "active_course", "active_course_path", "approve_curriculum", "assess_response", "assign_gate",
    "attach_learner_message", "capsule", "checks_for", "complete_module", "complete_review", "course_mastery",
    "course_record", "due_reviews", "early_exit", "finalize_gate", "grade_exec", "grade_rubric", "has_diagnostic",
    "inline_choices", "known_concepts", "lecture_state", "lesson_mastery", "mastery_from", "mastery_grid",
    "merge_profile", "module_mastery", "next_lesson", "objective_attempts", "order_blockers", "ordered_lessons",
    "precheck_verifier", "prior_learner_model", "propose_curriculum", "record_answer", "remember_profile",
    "run_verifier", "schedule_after_grade", "skip_lecture", "start_lesson", "teach_check", "teach_sources",
    "upcoming_reviews", "work_dir",
]
