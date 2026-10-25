"""Teacher mode through its tool handlers: the course lifecycle, lecture tracking, gates
with user overrides, verifier integrity, weighted rubrics, mastery and dated reviews
(modes v2 §4.6, T1–T5)."""

import time

import pytest

from mu.modes.events import GateError
from mu.teacher import gates
from mu.teacher.engine import load_course, read_course

from mode_helpers import agent_session, call, request_texts

MODULES = [
    {"module_id": "m1", "title": "Basics", "lessons": [
        {"lesson_id": "l1", "title": "Return values", "learning_objectives": ["return a value"]},
        {"lesson_id": "l2", "title": "Arguments", "learning_objectives": ["use arguments"]},
    ]},
    {"module_id": "m2", "title": "More", "lessons": [
        {"lesson_id": "l3", "title": "Loops", "learning_objectives": ["write a loop"]},
    ]},
]


def _course(session):
    return load_course(gates.active_course_path(session))


def _start(tmp_path, steps=()):
    session = agent_session(tmp_path, mode="teacher", steps=steps)
    assert call(session, "create_course", subject="Python functions")["ok"]
    return session


def _diagnose_and_approve(session):
    assert call(session, "record_diagnostic", background=["JavaScript"], strengths=["variables"],
                pace="fast", modality=["hands-on"])["ok"]
    assert call(session, "propose_curriculum", modules=MODULES)["ok"]
    course = _course(session)
    gates.approve_curriculum(course, actor="user:gui")
    from mu.teacher.engine import save_course
    save_course(course)


def _lecture(session, lesson_id):
    """Teach l<n> through checks the learner answers, then write the transcript."""
    for n in range(2):
        check = call(session, "teach_check", question=f"What does return do? ({n})", kind="open")
        assert check["ok"]
        course = _course(session)
        gates.attach_learner_message(course, "it hands a value back to the caller")
        from mu.teacher.engine import save_course
        save_course(course)
        assessed = call(session, "assess_response", check_id=check["data"]["check_id"], signal="on_track")
        assert assessed["ok"]
    assert assessed["data"]["concluded"]
    assert call(session, "write_lecture_transcript", lesson_id=lesson_id, content="# Returns\n...")["ok"]


EXEC = {"kind": "fix-broken-code", "prompt": "Make add() return the sum",
        "verification": {"verify_cmd": "python3 check.py", "expected_markers": ["OK"]},
        "artifact_files": [{"path": "add.py", "content": "def add(a, b):\n    pass\n"},
                           {"path": "check.py", "content": "from add import add\nprint('OK' if add(2, 3) == 5 else 'NO')\n"}]}


def test_curriculum_needs_a_diagnostic_and_only_the_learner_approves(tmp_path):
    session = _start(tmp_path)
    refused = call(session, "propose_curriculum", modules=MODULES)
    assert not refused["ok"] and refused["error_code"] == "diagnostic_required"
    call(session, "record_diagnostic", background=["JavaScript"])
    assert call(session, "propose_curriculum", modules=MODULES)["ok"]
    from mu.tools.descriptors import TOOLS
    assert "approve_curriculum" not in {t.name for t in TOOLS}
    with pytest.raises(GateError):
        gates.approve_curriculum(_course(session), actor="agent")
    early = call(session, "start_lesson", lesson_id="l1")
    assert not early["ok"] and early["error_code"] == "not_approved"
    from mu.commands import dispatch
    assert dispatch(session, "/teach approve", allow_prompt=False).ok
    course = _course(session)
    assert course.curriculum_approval["actor"] == "user:cli" and course.status == "in_progress"


def test_the_profile_and_lesson_state_ride_in_the_request_not_history(tmp_path):
    session = _start(tmp_path, steps=["Let's begin."])
    call(session, "record_diagnostic", background=["JavaScript"], pace="fast")
    session.send_message("hi, I want to learn")
    text = "\n".join(request_texts(session.provider.calls[0]))
    assert "LEARNER PROFILE" in text and "JavaScript" in text
    history = "\n".join(str(m) for m in session.session_manager.history)
    assert "LEARNER PROFILE" not in history


def test_order_is_enforced_and_a_user_override_is_recorded(tmp_path):
    session = _start(tmp_path)
    _diagnose_and_approve(session)
    refused = call(session, "start_lesson", lesson_id="l2")
    assert not refused["ok"] and refused["error_code"] == "out_of_order"
    from mu.commands import dispatch
    assert not dispatch(session, "/teach start l2", allow_prompt=False).ok
    assert dispatch(session, "/teach start l2 --override already knows arguments", allow_prompt=False).ok
    course = _course(session)
    lesson = next(l for l in course.lessons if l.lesson_id == "l2")
    assert lesson.override["actor"] == "user:cli"
    assert any(e.kind == "override" for e in course.event_log)


def test_lecture_tracking_gates_the_assignment(tmp_path):
    session = _start(tmp_path)
    _diagnose_and_approve(session)
    call(session, "start_lesson", lesson_id="l1")
    refused = call(session, "assign_exercise", lesson_id="l1", **EXEC)
    assert not refused["ok"] and refused["error_code"] == "lecture_incomplete"
    check = call(session, "teach_check", question="What does return do?", kind="open")["data"]
    early = call(session, "assess_response", check_id=check["check_id"], signal="on_track")
    assert not early["ok"] and early["error_code"] == "no_answer"
    # The learner's next chat message is attached to the open check.
    session.provider.push("Good.")
    session.send_message("it gives the value back")
    assert _course(session).checks[0].answer == "it gives the value back"
    _lecture(session, "l1")
    assert call(session, "assign_exercise", lesson_id="l1", **EXEC)["ok"]


def test_a_known_concept_skip_needs_the_diagnostic_and_is_recorded(tmp_path):
    session = _start(tmp_path)
    _diagnose_and_approve(session)
    call(session, "start_lesson", lesson_id="l1")
    bad = call(session, "skip_lecture", lesson_id="l1", concept="quantum", reason="seems fine")
    assert not bad["ok"] and bad["error_code"] == "not_known"
    ok = call(session, "skip_lecture", lesson_id="l1", concept="variables", reason="diagnostic: strong on variables")
    assert ok["ok"] and ok["data"]["concept"] == "variables"
    assert call(session, "assign_exercise", lesson_id="l1", **EXEC)["ok"]


def test_a_verifier_that_passes_on_the_starting_files_is_refused(tmp_path):
    session = _start(tmp_path)
    _diagnose_and_approve(session)
    call(session, "start_lesson", lesson_id="l1")
    _lecture(session, "l1")
    trivial = dict(EXEC, artifact_files=[{"path": "check.py", "content": "print('OK')\n"}])
    refused = call(session, "assign_exercise", lesson_id="l1", **trivial)
    assert not refused["ok"] and refused["error_code"] == "verifier_trivial"


def test_full_course_lifecycle_with_mastery_reviews_and_report(tmp_path):
    session = _start(tmp_path)
    _diagnose_and_approve(session)
    for lesson_id in ("l1", "l2"):
        assert call(session, "start_lesson", lesson_id=lesson_id)["ok"]
        _lecture(session, lesson_id)
        made = call(session, "assign_exercise", lesson_id=lesson_id, assignment_id=f"{lesson_id}_a1", **EXEC)
        assert made["ok"] and made["data"]["precheck_run"]
        work = gates.work_dir(_course(session), next(a for a in _course(session).assignments
                                                        if a.assignment_id == f"{lesson_id}_a1"))
        with open(f"{work}/add.py", "w") as fh:
            fh.write("def add(a, b):\n    return a + b\n")
        graded = call(session, "grade_assignment", assignment_id=f"{lesson_id}_a1")
        assert graded["ok"] and graded["data"]["grade"]["passed"]
        assert call(session, "decide_next", lesson_id=lesson_id, action="advance")["ok"]
    course = _course(session)
    review = next(r for r in course.scheduled_reviews if r.objective == "return a value")
    assert review.due_at > time.time()  # scheduled by date, after the grade
    # Module 2 can't start before module 1 is completed.
    assert call(session, "start_lesson", lesson_id="l3")["error_code"] == "out_of_order"
    assert call(session, "complete_module", module_id="m1")["ok"]
    early_finish = call(session, "finalize_course")
    assert not early_finish["ok"] and early_finish["error_code"] == "lessons_incomplete"
    from mu.commands import dispatch
    assert dispatch(session, "/teach finish --early time is up", allow_prompt=False).ok
    done = call(session, "finalize_course")
    assert done["ok"]
    report = open(done["data"]["report_card_path"]).read()
    grid = gates.mastery_grid(_course(session))
    row = next(r for r in grid if r["objective"] == "return a value")
    assert f"return a value: {row['mastery_pct']}%" in report
    from mu.gui.routers.teacher import course_payload
    panel = course_payload(read_course(gates.active_course_path(session)))
    assert next(r for r in panel["mastery"] if r["objective"] == "return a value")["mastery_pct"] == row["mastery_pct"]


def test_rubric_grades_are_weighted_and_awaiting_judgement_is_not_a_fail(tmp_path):
    session = _start(tmp_path)
    _diagnose_and_approve(session)
    call(session, "start_lesson", lesson_id="l1")
    call(session, "skip_lecture", lesson_id="l1", concept="variables", reason="known")
    call(session, "assign_exercise", lesson_id="l1", assignment_id="essay", kind="short-answer",
         prompt="Explain return", rubric=[{"criterion": "accuracy", "weight": 3}, {"criterion": "clarity", "weight": 1}])
    call(session, "submit_assignment", assignment_id="essay", submission={"answer": "return sends a value back"})
    pending = call(session, "grade_assignment", assignment_id="essay")
    assert pending["ok"] and pending["data"]["grade"]["status"] == "awaiting_judgement"
    course = _course(session)
    lesson = next(l for l in course.lessons if l.lesson_id == "l1")
    assert lesson.status == "assigned"  # not graded as a 0% fail
    graded = call(session, "grade_assignment", assignment_id="essay", rubric_scores=[
        {"criterion": "accuracy", "score_pct": 100}, {"criterion": "clarity", "score_pct": 0}])
    assert graded["data"]["grade"]["score_pct"] == 75 and graded["data"]["grade"]["passed"]


def test_re_proposing_keeps_graded_lessons(tmp_path):
    session = _start(tmp_path)
    _diagnose_and_approve(session)
    call(session, "start_lesson", lesson_id="l1")
    _lecture(session, "l1")
    call(session, "assign_exercise", lesson_id="l1", assignment_id="l1_a1", **EXEC)
    result = call(session, "propose_curriculum", modules=[{"module_id": "m9", "title": "New", "lessons": [
        {"lesson_id": "lx", "title": "New lesson", "learning_objectives": ["something"]}]}])
    assert result["ok"] and result["data"]["kept"] == ["l1"]
    course = _course(session)
    assert {l.lesson_id for l in course.lessons} == {"l1", "lx"}
    assert course.status == "curriculum_proposed" and course.curriculum_approval is None
    assert any(a.assignment_id == "l1_a1" for a in course.assignments)


def test_reviews_are_due_by_date_and_update_mastery(tmp_path):
    session = _start(tmp_path)
    _diagnose_and_approve(session)
    call(session, "start_lesson", lesson_id="l1")
    _lecture(session, "l1")
    call(session, "assign_exercise", lesson_id="l1", assignment_id="l1_a1", **EXEC)
    call(session, "grade_assignment", assignment_id="l1_a1")  # fails: add() returns None
    course = _course(session)
    review = next(r for r in course.scheduled_reviews if r.objective == "return a value")
    assert review.interval_days == 1.0
    assert not gates.due_reviews(course)
    assert gates.due_reviews(course, now=time.time() + 2 * 86400)[0].review_id == review.review_id


def test_inline_options_get_a_picker_nudge(tmp_path):
    session = _start(tmp_path, steps=["Which is right?\nA) one\nB) two\nC) three", "ok, using the picker"])
    session.send_message("quiz me")
    nudges = [m for m in session.session_manager.history if m.get("synthetic") and "teach_check" in str(m)]
    assert len(nudges) == 1


def test_old_course_files_load_with_unknown_keys(tmp_path):
    import json

    path = tmp_path / "course.json"
    path.write_text(json.dumps({"course_id": "old", "subject": "Old", "future_field": 1, "lessons": [
        {"lesson_id": "a", "module_id": "m", "title": "A", "new_thing": True}],
        "assignments": [{"assignment_id": "x", "lesson_id": "a", "kind": "short-answer",
                         "verification": {"method": "rubric_judge", "mystery": 2}}]}))
    course = load_course(str(path))
    assert course.lessons[0].lesson_id == "a" and course.assignments[0].verification.method == "rubric_judge"
