"""Job notes (core/notes.py): the human-readable story of a job, and the agent-written
headlines it is built from (plan/review ``headline``/``notes``, the implement hand-off)."""

from __future__ import annotations

import pytest

from mu.jobs import JobService, JobStore
from mu.jobs.core import vm
from mu.jobs.core.changes import ChangeBus, collect_durable_changes
from mu.jobs.core.notes import (HANDOFF_INSTRUCTIONS, clean, first_sentence, job_notes, parse_handoff,
                                tool_summary)
from mu.jobs.core.review_report import ReviewReport
from mu.jobs.harness_cli import Translation, translate_claude, translate_codex
from mu.jobs.models import JobSpec
from mu.jobs.stages import build_plan_prompt, build_stage_review_prompt, parse_plan


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))


def _service(tmp_path):
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    job = service.create(JobSpec(title="Remove the map header", repository=str(tmp_path)))
    return service, job


# ------------------------------------------------------------------ text helpers

def test_parse_handoff_reads_the_last_headline_and_its_bullets():
    text = ("I quoted the brief: HEADLINE: not this one\n\nDid the work.\n\n"
            "**HEADLINE:** Removed the stats header from the map.\n**NOTES:**\n"
            "- Toolbar now sits at `top: 16px`\n- Lint not run: node_modules missing\n\nThanks.")
    headline, notes = parse_handoff(text)
    assert headline == "Removed the stats header from the map."
    assert notes == ["Toolbar now sits at top: 16px", "Lint not run: node_modules missing"]


def test_parse_handoff_without_one_is_empty():
    assert parse_handoff("Just a final message.") == ("", [])


def test_clean_keeps_underscores_and_stars_inside_words():
    assert clean("`tsc` comes from node_modules, **not** the image") == "tsc comes from node_modules, not the image"
    assert clean("a" * 300).endswith("…") and len(clean("a" * 300)) == 240


def test_first_sentence_of_prose_and_of_a_structured_reply():
    assert first_sentence("The diff does what the plan asks. More detail here.") == "The diff does what the plan asks."
    assert first_sentence('{"summary": "Plan it. Then more.", "steps": []}') == "Plan it."
    assert first_sentence('{"headline": "Short.", "summary": "Long."}') == "Short."


def test_tool_summary_is_one_readable_line():
    assert tool_summary("bash", {"command": "sed -i '435s/.*/}/' a.css\nls"}) == "$ sed -i '435s/.*/}/' a.css …"
    assert tool_summary("edit_file", {"filename": "/workspace/src/a.py"}) == "edit src/a.py"
    assert tool_summary("search_for_string", {"query": "foo", "path": "src"}) == 'grep "foo" src'
    assert tool_summary("apply_patch", {"files": ["a.py", "b.py"]}) == "patch a.py, b.py"


# ------------------------------------------------------------------ contracts

def test_plan_and_review_prompts_ask_for_headline_and_notes(tmp_path):
    service, job = _service(tmp_path)
    assert '"headline"' in build_plan_prompt(job) and '"notes"' in build_plan_prompt(job)
    assert '"headline"' in build_stage_review_prompt(job, "", "", 1)


def test_implement_prompt_asks_for_the_handoff(tmp_path):
    from mu.jobs.runner import implement_prompt
    service, job = _service(tmp_path)
    assert implement_prompt(service, job).rstrip().endswith(HANDOFF_INSTRUCTIONS)


def test_parse_plan_keeps_headline_and_notes():
    plan = parse_plan('{"summary": "S.", "steps": ["a"], "headline": "Drop the **header**.", "notes": ["one", "", "two"]}')
    assert plan["headline"] == "Drop the header."
    assert plan["notes"] == ["one", "two"]


def test_review_report_round_trips_headline_and_notes():
    report = ReviewReport.from_dict({"summary": "Long summary.", "headline": "Right change, unverified.",
                                     "notes": ["Checks never ran"], "findings": []}, job_id="j")
    again = ReviewReport.from_dict(report.to_dict())
    assert again.headline == "Right change, unverified." and again.notes == ["Checks never ran"]


def test_review_done_banner_uses_the_reviewer_headline(tmp_path):
    from mu.jobs.models import JobStatus
    from mu.jobs.stages import apply_review_verdict
    service, job = _service(tmp_path)
    for status in (JobStatus.PREPARING, JobStatus.RUNNING, JobStatus.VERIFYING, JobStatus.REVIEWING):
        service.transition(job.id, status)
    apply_review_verdict(service, job.id, {"summary": "A very long summary. " * 20, "headline": "Looks right; lint unverified.",
                                           "findings": []})
    data = vm.job_vm(service, job.id, parts=["state"])
    assert data["attention_detail"] == "Looks right; lint unverified."
    assert data["attention_headline"] == "Looks right; lint unverified."


def test_tool_call_events_carry_a_summary():
    state = Translation(agent="claude")
    out = translate_claude({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "git diff --stat"}}]}}, state)
    assert out[0]["event"] == "tool_call_ui" and out[0]["payload"]["summary"] == "$ git diff --stat"
    out = translate_codex({"type": "item.started", "item": {"type": "command_execution", "id": "c1",
                                                            "command": "pytest -q"}}, Translation(agent="codex"))
    assert out[0]["payload"]["summary"] == "$ pytest -q"


# ------------------------------------------------------------------ notes

def _attempt(service, job, status, text="", **meta):
    attempt = service.start_attempt(job.id, worker_id="w", metadata={"agent_result": {"text": text}, **meta})
    service.store.append_event(job.id, "attempt_finished", reason=status,
                               payload={"attempt_id": attempt.id, "cost_usd": 0.25, "error": ""})
    return attempt


def test_notes_tell_the_story_in_plain_english(tmp_path):
    service, job = _service(tmp_path)
    store = service.store
    store.append_event(job.id, "plan_created", reason="planning stage completed",
                       payload={"summary": "Remove the header. Then move the toolbar.", "steps": ["a", "b"],
                                "acceptance_criteria": ["x"], "headline": "Remove the stats header.", "notes": ["Toolbar moves up"]})
    store.append_event(job.id, "plan_version_approved", payload={"version": 1})
    for n in range(3):
        _attempt(service, job, "needs_human", attention={"tool_name": "Bash", "tool_args": {"command": f"sed -n {n}p f"}})
        store.append_event(job.id, "command", reason="approveTool", payload={"command": "approveTool", "args": {}})
    _attempt(service, job, "completed", "Done.\n\nHEADLINE: Removed the header.\nNOTES:\n- Lint not run")
    unrunnable = {"checks_total": 1, "unrunnable": 1, "verified": False, "passed": False,
                  "unrunnable_suites": [{"suite": "lint", "cause": "eslint isn't installed"}]}
    store.append_event(job.id, "checks_completed", payload={"run": 1, **unrunnable})
    store.append_event(job.id, "command", reason="resume", payload={"command": "resume", "args": {"guidance": "try again"}})
    store.append_event(job.id, "checks_completed", payload={"run": 2, **unrunnable})
    store.append_event(job.id, "review_completed", reason="needs_work",
                       payload={"verdict": "needs_work", "merge_readiness": 59, "summary": "Long. Detail.",
                                "counts": {"major": 1}, "findings": [{"severity": "major", "title": "Checks never ran"}]})
    store.append_event(job.id, "sandbox_event", payload={"event": "tool_call_ui", "payload": {"tool_name": "bash"}})

    notes = job_notes(service, job)
    titles = [n["title"] for n in notes]
    assert titles == ["Job created", "Plan ready", "You approved plan v1", "Paused for tool approval",
                      "Implementation done", "Checks couldn't run", "You resumed the job", "Checks couldn't run",
                      "Review: needs work"]
    plan, paused, done, checks1, resumed, checks2, review = (notes[i] for i in (1, 3, 4, 5, 6, 7, 8))
    assert plan["headline"] == "Remove the stats header." and plan["bullets"] == ["Toolbar moves up"]
    assert plan["meta"] == "2 steps · 1 criterion" and plan["tab"] == "plan"
    assert paused["count"] == 3 and paused["bullets"][-1] == "$ sed -n 2p f"
    assert done["headline"] == "Removed the header." and done["bullets"] == ["Lint not run"]
    assert checks1["bullets"] == ["eslint isn't installed"]
    assert checks2["bullets"] == [] and checks2["headline"].endswith("Same as before.")
    assert resumed["bullets"] == ["“try again”"]
    assert review["headline"] == "Long." and review["bullets"] == ["Major: Checks never ran"]
    assert "readiness 59/100" in review["meta"]
    assert all("fold" not in n for n in notes)


def test_notes_fall_back_to_the_first_sentence_without_a_handoff(tmp_path):
    service, job = _service(tmp_path)
    _attempt(service, job, "completed", "I removed the header. Then I checked the CSS.")
    assert job_notes(service, job)[-1]["headline"] == "I removed the header."


def test_notes_part_and_slimmer_audit_trail(tmp_path):
    service, job = _service(tmp_path)
    service.store.append_event(job.id, "sandbox_event", payload={"event": "tool_result_ui", "payload": {"preview": "x" * 5000}})
    service.store.append_event(job.id, "checks_output", payload={"suite": "s", "text": "y" * 5000})
    data = vm.job_vm(service, job.id)
    assert [n["title"] for n in data["notes"]] == ["Job created"]
    kinds = {e["event_type"] for e in data["costs"]["events"]}
    assert "job_created" in kinds and not kinds & {"sandbox_event", "checks_output"}
    assert "approval" in data


def test_change_stream_parts(tmp_path):
    service, job = _service(tmp_path)
    bus = ChangeBus(window_s=0)
    cursor = collect_durable_changes(service, bus, 0)
    bus.flush(force=True)
    seq = bus.seq
    service.store.append_event(job.id, "sandbox_event", payload={"event": "tool_call_ui", "payload": {}})
    cursor = collect_durable_changes(service, bus, cursor)
    bus.flush(force=True)
    # Agent tool traffic refreshes the session only: not the audit trail, not the notes.
    assert [c.parts for c in bus.since(seq) if c.id == job.id] == [["session"]]
    seq = bus.seq
    service.store.append_event(job.id, "review_completed", reason="ready", payload={})
    collect_durable_changes(service, bus, cursor)
    bus.flush(force=True)
    parts = set().union(*(c.parts for c in bus.since(seq) if c.id == job.id))
    assert {"notes", "costs", "review"} <= parts


# ------------------------------------------------------------------ backfill (older jobs)

def test_backfill_writes_missing_headlines_once(tmp_path):
    from mu.jobs.core.artifacts import artifacts_for
    from mu.jobs.notes_backfill import backfill
    service, job = _service(tmp_path)
    store = service.store
    store.append_event(job.id, "plan_created", payload={"summary": "Long plan. More.", "steps": ["a"]})
    store.append_event(job.id, "plan_created", payload={"summary": "S.", "headline": "Already written."})
    _attempt(service, job, "completed", "I removed it. Then more.")
    _attempt(service, job, "completed", "Done.\nHEADLINE: Has one.")
    review = {"verdict": "needs_work", "summary": "Long review. More.", "findings": []}
    store.append_event(job.id, "review_completed", reason="needs_work", payload=review)
    artifacts_for(job).ensure().write_json("review.json", dict(review))

    prompts = []
    def generate(prompt):
        prompts.append(prompt)
        return '```json\n{"headline": "Written %d.", "notes": ["n"]}\n```' % len(prompts)

    assert backfill(service, generate, log=lambda _l: None, jobs=[job]) == 3
    assert backfill(service, generate, log=lambda _l: None, jobs=[job]) == 0  # idempotent
    assert "Job title: Remove the map header" in prompts[0]
    by_title = {n["title"]: n for n in job_notes(service, job)}
    assert by_title["Review: needs work"]["headline"] == "Written 3."
    assert by_title["Review: needs work"]["bullets"] == ["n"]
    assert artifacts_for(job).read_json("review.json")["headline"] == "Written 3."
    headlines = [n["headline"] for n in job_notes(service, job) if n["title"] in ("Plan ready", "Implementation done")]
    assert headlines == ["Written 1.", "Already written.", "Written 2.", "Has one."]


def test_backfill_dry_run_calls_no_model(tmp_path):
    from mu.jobs.notes_backfill import backfill
    service, job = _service(tmp_path)
    service.store.append_event(job.id, "plan_created", payload={"summary": "Plan."})
    def boom(_prompt):
        raise AssertionError("no model call in a dry run")
    lines = []
    assert backfill(service, boom, dry_run=True, log=lines.append, jobs=[job]) == 0
    assert len(lines) == 1 and "would write" in lines[0]
