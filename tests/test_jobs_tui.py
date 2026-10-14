"""Terminal job dashboard (/jobs): render snapshots, breakpoints, key → command mapping.

Views render to plain lines, so everything here runs without a TTY over real
jobs created in a temporary MUCLI_HOME (one job per pipeline state, mirroring
/tmp/planos2/fixtures.py).
"""

from __future__ import annotations

import ast
import json
import os
import pathlib
import time
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from mu.ui.jobs import app as tui_app
from mu.ui.jobs import content, primitives, theme, views
from mu.ui.jobs.app import JobsApp, fuzzy, rank_palette
from mu.ui.jobs.data import DispatchError, JobsData, normalize_action
from mu.ui.jobs.primitives import line_width, to_ansi, to_plain

ROOT = pathlib.Path(__file__).resolve().parents[1]

PATCH = """diff --git a/mu/jobs/sandbox/worker.py b/mu/jobs/sandbox/worker.py
index 2f11b3b..3f4f55e 100644
--- a/mu/jobs/sandbox/worker.py
+++ b/mu/jobs/sandbox/worker.py
@@ -19,6 +19,9 @@ class StageUI(JobUI):
     def _event(self, event_type, *, payload=None, reason=""):
         print(json.dumps({"event": event_type}), flush=True)

+    def request_tool_approval(self, **kwargs):
+        self._approval_args = kwargs.get("tool_args") or {}
+        return super().request_tool_approval(**kwargs)

 def main():
     import mucli
diff --git a/mu/jobs/worker.py b/mu/jobs/worker.py
index 13b8632..5d18806 100644
--- a/mu/jobs/worker.py
+++ b/mu/jobs/worker.py
@@ -359,4 +359,4 @@ def run():
     a = 1
-    b = 2
+    b = 3
     c = 4
"""

TRANSCRIPT = [
    {"role": "user", "parts": [{"type": "text", "text": "DURABLE ENGINEERING JOB\nTitle: x\n\nline 3\nline 4"}]},
    {"role": "assistant", "parts": [{"type": "text", "text": "Reading `loader.rs` first."},
                                    {"type": "tool_call", "tool_name": "bash", "tool_args": "{'command': 'ls src'}"},
                                    {"type": "tool_call", "tool_name": "read_file", "tool_args": {"path": "src/a.rs"}}]},
    {"role": "tool", "parts": [{"type": "tool_result", "tool_name": "bash",
                                "tool_result": "{'tool_name': 'bash', 'ok': True, 'summary': 'STDOUT:\\nmain.rs\\nlib.rs'}"},
                               {"type": "tool_result", "tool_name": "read_file",
                                "tool_result": "{'ok': False, 'error': 'no such file'}"}]},
]

REVIEW = {
    "summary": "Implements the hook. **Two issues** remain.", "verdict": "needs_work", "merge_readiness": 64,
    "plan_coverage": [{"criterion_id": "AC-1", "status": "met", "evidence": "hook runs"},
                      {"criterion_id": "AC-2", "status": "partial", "evidence": "flag untested"}],
    "findings": [
        {"id": "F1", "severity": "major", "category": "correctness", "file": "mu/jobs/sandbox/worker.py",
         "line_start": 23, "title": "Retry loop has no upper bound", "detail": "Deploy hangs.",
         "suggestion": "Cap retries.", "confidence": 0.86, "disposition": "open"},
        {"id": "F2", "severity": "minor", "category": "tests", "file": "mu/jobs/worker.py", "line_start": 360,
         "title": "No test for WARMUP=0", "detail": "Disabled path.", "suggestion": "", "confidence": 0.7,
         "disposition": "open"},
        {"id": "F3", "severity": "nit", "category": "style", "file": "README.md", "line_start": 4,
         "title": "Typo", "detail": "warm-up", "suggestion": "", "confidence": 0.9, "disposition": "accepted"},
    ],
}


def _suite(name, passed, failed=0, total=10):
    cases = [{"id": f"t::{name}_{i}", "suite": f"tests.{name}", "name": f"test_{i}", "status": "passed", "time": "0.1"}
             for i in range(total - failed)]
    cases += [{"id": f"t::{name}_bad{i}", "suite": f"tests.{name}", "name": f"test_bad{i}", "status": "failed",
               "time": "1", "message": "AssertionError: boom"} for i in range(failed)]
    return {"suite": name, "command": f"pytest -q tests/{name}", "passed": passed, "duration_ms": 61000,
            "stdout": f"{total - failed} passed", "stderr": "", "preexisting_failures": [],
            "junit": {"passed": total - failed, "failed": failed, "skipped": 1, "total": total + 1, "cases": cases}}


def _build(home: pathlib.Path) -> Dict[str, str]:
    from mu.jobs import JobService, JobStore
    from mu.jobs.core.artifacts import artifacts_for, jobs_root
    from mu.jobs.core.plan_ops import record_planner_plan
    from mu.jobs.core.states import JobState as S
    from mu.jobs.models import AttentionReason

    os.makedirs(jobs_root(), exist_ok=True)
    service = JobService(JobStore(os.path.join(jobs_root(), "jobs.sqlite3")))
    ws = home / "ws"
    ws.mkdir(parents=True, exist_ok=True)

    def make(key, title, path):
        job = service.create_from_payload({
            "title": f"{key}: {title}", "description": title, "workspace_root": str(ws),
            "acceptance_criteria": ["works"], "validation_commands": ["pytest -q"],
            "execution": {"provider": "anthropic", "model": "claude-sonnet-5-5"}, "metadata": {"jira": {"key": key}}})
        for st in path:
            if st == "PLAN":
                record_planner_plan(service.get(job.id), {"summary": f"{title}: do it.", "steps": ["Read", "Change"],
                                                          "acceptance_criteria": ["Works"], "validation_commands": ["pytest -q"]})
                continue
            service.store.transition_core(job.id, st, reason=f"fixture → {st.value}")
        return service.get(job.id)

    def files(job, *, review=None, checks=None, costs=()):
        art = artifacts_for(job)
        os.makedirs(art.path("logs"), exist_ok=True)
        open(art.path("diff.patch"), "w").write(PATCH)
        open(art.path("logs", "implement-transcript.json"), "w").write(json.dumps(TRANSCRIPT))
        if review:
            open(art.path("review.json"), "w").write(json.dumps(review))
        for n, summary in enumerate(checks or [], 1):
            os.makedirs(art.path("checks", f"run-{n}"), exist_ok=True)
            open(art.path("checks", f"run-{n}", "summary.json"), "w").write(json.dumps(dict(summary, run=n)))
        with open(art.path("costs.jsonl"), "a") as fh:
            for stage, cost in costs:
                fh.write(json.dumps({"stage": stage, "at": time.time(), "cost_usd": cost}) + "\n")

    pre = [S.PROVISIONING, S.PLANNING, "PLAN", S.PLAN_REVIEW]
    ids = {}
    j = make("SCRUM-41", "Stream processor back-pressure", pre)
    files(j, costs=[("plan", 0.31)])
    ids["plan_review"] = j.id
    j = make("SCRUM-44", "Rotate signing keys", pre + [S.IMPLEMENTING])
    service.require_human(j.id, AttentionReason.APPROVAL_REQUIRED, "The agent wants to run a command that needs approval",
                          payload={"tool_name": "bash", "tool_args": {"command": "git stash && git checkout main"},
                                   "can_approve": True})
    files(j, costs=[("plan", 0.22), ("implement", 0.58)])
    ids["needs_input"] = j.id
    j = make("SCRUM-40", "SAM Smart Backend", pre + [S.IMPLEMENTING])
    files(j, costs=[("plan", 0.41), ("implement", 1.84)])
    service.store.append_event(j.id, "sandbox_event", payload={"stage": "implement", "event": "runtime_status",
                                                                "payload": {"text": "Generating it 28/1000"}})
    ids["implementing"] = j.id
    j = make("SCRUM-38", "Paginate audit export", pre + [S.IMPLEMENTING, S.CHECKING])
    files(j)
    for t in ("collected 13 items\n", "tests/test_export.py ........\n", "tests/test_pagination.py ....F\n"):
        service.store.append_event(j.id, "checks_output", payload={"suite": "unit", "baseline": False, "text": t})
    ids["checking"] = j.id
    j = make("SCRUM-36", "Cache warm-up on deploy",
             pre + [S.IMPLEMENTING, S.CHECKING, S.REVIEWING, S.REVIEW_DONE])
    now = time.time()
    files(j, review=dict(REVIEW, job_id=j.id), costs=[("plan", 0.27), ("implement", 2.31), ("review", 0.66)],
          checks=[{"passed": False, "head_sha": "a1b2c3d4e5f6", "started_at": now - 7200, "duration_ms": 95000,
                   "trigger": "auto", "suites": [_suite("unit", False, 1)]},
                  {"passed": True, "head_sha": "9f8e7d6c5b4a", "started_at": now - 3600, "duration_ms": 121000,
                   "trigger": "fix", "suites": [_suite("unit", True), _suite("integration", True)]}])
    ids["review_done"] = j.id
    j = make("SCRUM-33", "Migrate webhooks to v2", pre + [S.IMPLEMENTING])
    service.store.transition_core(j.id, S.FAILED, reason="implement stage crashed",
                                  attention_detail="Sandbox exited with code 137 (out of memory)")
    files(j, costs=[("plan", 0.15)])
    ids["failed"] = j.id
    ids["_service"] = service
    return ids


@pytest.fixture(scope="module")
def fixture_home(tmp_path_factory):
    home = tmp_path_factory.mktemp("jobs-tui-home")
    old = os.environ.get("MUCLI_HOME")
    os.environ["MUCLI_HOME"] = str(home)
    try:
        ids = _build(home)
    finally:
        if old is None:
            os.environ.pop("MUCLI_HOME", None)
        else:
            os.environ["MUCLI_HOME"] = old
    return home, ids


@pytest.fixture
def jobs(fixture_home, monkeypatch):
    home, ids = fixture_home
    monkeypatch.setenv("MUCLI_HOME", str(home))
    return ids


class RecordingData(JobsData):
    """Real reads; dispatches recorded (and optionally rejected) instead of applied."""

    def __init__(self, service, *, push_enabled: bool = False):
        super().__init__(service)
        self.sent: List[tuple] = []
        self.push_enabled = push_enabled

    def _patch(self, vm):
        if self.push_enabled:
            for a in vm.get("actions", []):
                if a["id"] == "push":
                    a.update(enabled=True, reason="")
        return vm

    def job(self, job_id, parts=None):
        return self._patch(super().job(job_id, parts))

    def push_preview(self, job):
        return {"title": "SCRUM-36: Cache warm-up on deploy", "body": "Summary\n- warm-up hook", "branch": "mucli/scrum-36",
                "base": "main", "mode": "gh", "remote": "github.com/acme/app", "draft": True,
                "requires_override": False, "verdict": "needs_work", "head_sha": "9f8e7d6c", "reason": ""}

    def dispatch(self, job_id, kind, args=None):
        self.sent.append((job_id, kind, dict(args or {})))
        return {"ok": True}


def _app(jobs, **kw) -> JobsApp:
    data = RecordingData(jobs["_service"], **kw)
    app = JobsApp(data)
    app.refresh()
    return app


def _text(app, w, h):
    lines = views.render(app, w, h)
    assert len(lines) == h
    assert all(line_width(l) == w for l in lines), [line_width(l) for l in lines]
    return to_plain(lines)


# ---------------------------------------------------------------- render snapshots

@pytest.mark.parametrize("width", [80, 120, 200])
def test_dashboard_renders_shell_needs_running_and_jobs(jobs, width):
    app = _app(jobs)
    text = _text(app, width, 30)
    lines = text.splitlines()
    assert lines[0].startswith(" mucli jobs")
    assert "◆ 4 need you" in lines[0] and "● 1 agent" in lines[0] and "● 1 check" in lines[0]
    assert "╭ Needs you 4 " in text and "╭ Running now 2 " in text and "╭ Jobs 6 " in text
    assert "SCRUM-41" in text and "plan to approve" in text
    assert "failed in implementing" in text
    # checks progress bar for the running check run
    assert "▕" in text and "12 ok 1 ✗" in text
    # agent last line from the durable stream
    assert "› Generating it 28/1000" in text
    assert "✓─✓─◆─○─○─○" in text  # mini pipeline
    assert lines[-1].startswith(" enter open") and lines[-1].rstrip().endswith("? help")
    if width < 100:
        # single column: needs-you stacked above running
        assert text.index("Needs you") < text.index("Running now")
        assert all("Needs you" not in l or "Running now" not in l for l in lines)
    else:
        assert any("Needs you" in l and "Running now" in l for l in lines)
        assert "SUMMARY" in text and "Cache warm-up on deploy" in text


@pytest.mark.parametrize("width,sidebar", [(80, False), (120, True), (160, True), (200, True)])
def test_job_view_header_stepper_tabs_and_breakpoints(jobs, width, sidebar):
    app = _app(jobs)
    app.open_job(jobs["review_done"])
    text = _text(app, width, 34)
    lines = text.splitlines()
    assert lines[1].lstrip().startswith("SCRUM-36 · Cache warm-up on deploy")
    assert "◆ review done" in lines[1] and "$3.24" in lines[1]
    assert "Plan ✓" in lines[2] and "Review ◆" in lines[2] and "Push ○" in lines[2]
    assert "Session  Plan  Diff +4 −1  Review 3  Checks ✓" in text
    # review is the default tab at review_done: verdict, coverage and finding cards
    assert "Verdict needs work" in text and "64/100" in text
    assert "MAJOR (1)" in text and "F1 · correctness" in text
    assert ("╭ Checks 2 " in text) is sidebar
    assert ("╭ Approvals " in text) is sidebar
    if width >= 160:
        assert "╭ Cost $3.24 " in text


def test_diff_tab_file_list_viewed_marks_and_side_by_side(jobs):
    app = _app(jobs)
    app.open_job(jobs["review_done"])
    app.handle_key("3")
    narrow = _text(app, 80, 34)
    assert "0/2 viewed" in narrow and "○ +3 −0" in narrow and "mu/jobs/sandbox/worker.py" in narrow
    assert "+    def request_tool_approval" in narrow
    wide = _text(app, 170, 34)
    assert "│" in wide and "⚑" in wide  # side-by-side hunks with the pinned finding
    assert "╭ Checks" not in wide  # the diff takes the full width


def test_session_tab_folds_tool_calls_and_expands_on_enter(jobs):
    app = _app(jobs)
    app.open_job(jobs["implementing"])
    text = _text(app, 120, 40)
    assert "⏺ bash(ls src)  ⎿ STDOUT:" in text
    assert "⏺ read_file(src/a.rs)  ⎿ ✗ no such file" in text
    assert "lib.rs" not in text
    st = app.job_state(jobs["implementing"])
    items = content.session_items(app.jobs[jobs["implementing"]]["session"])
    bash = next(i for i, it in enumerate(items) if it.get("name") == "bash")
    st.item = bash
    app.handle_key("enter")
    assert "lib.rs" in _text(app, 120, 40)
    app.handle_key("enter")
    assert "lib.rs" not in _text(app, 120, 40)
    app.handle_key("f")
    assert "lib.rs" in _text(app, 120, 40)


def test_checks_tab_runs_bar_failed_cases_and_live_tail(jobs):
    app = _app(jobs)
    app.open_job(jobs["review_done"])
    app.handle_key("5")
    text = _text(app, 120, 50)
    assert "✓ 2   passed    fix" in text and "✗ 1   failed    auto" in text
    assert "Run 2  20 passed · 0 failed · 2 skipped" in text and "█" in text
    app.handle_key("j")  # older run
    text = _text(app, 120, 50)
    assert "Failed cases (1)" in text and "test_bad0" in text and "AssertionError: boom" in text
    app.pop()
    app.open_job(jobs["checking"])
    text = _text(app, 120, 40)
    assert "running" in text and "tests/test_pagination.py ....F" in text


def test_log_and_errors_tabs(jobs):
    app = _app(jobs)
    app.open_job(jobs["failed"])
    text = _text(app, 120, 40)  # failed → errors tab by default
    assert "✗ Failed in implementing" in text and "out of memory" in text
    app.handle_key("7")
    text = _text(app, 120, 40)
    assert "Cost $0.15" in text and "Pipeline timings" in text and "Audit timeline" in text
    assert "● implementing → failed" in text or "→ failed" in text


def test_help_and_palette_overlays(jobs):
    app = _app(jobs)
    app.open_job(jobs["plan_review"])
    app.handle_key("?")
    text = _text(app, 100, 30)
    assert "╭ Keys ? or esc to close" in text and "command palette" in text and "Approve plan" in text
    app.handle_key("escape")
    app.handle_key(":")
    for ch in "approve":
        app.handle_key(ch)
    text = _text(app, 100, 30)
    assert "╭ Commands" in text and "SCRUM-41 › Approve plan" in text
    app.handle_key("enter")
    assert app.overlay is None
    assert app.data.sent[-1][1] == "approvePlan"


def test_ansi_and_no_color_rendering(jobs):
    app = _app(jobs)
    lines = views.render(app, 100, 20)
    tc = to_ansi(lines, "truecolor")
    assert "\x1b[1;38;2;122;162;247m mucli jobs" in tc  # accent #7aa2f7, bold logo
    assert "38;2;224;175;104" in tc  # warn #e0af68 for needs-you
    c16 = to_ansi(lines, "16")
    assert "\x1b[1;36m mucli jobs" in c16 and "38;2" not in c16
    plain = to_ansi(lines, "none")
    assert "38;2" not in plain and "\x1b[33m" not in plain
    assert "◆" in plain  # status never by colour alone


# ---------------------------------------------------------------- key → command

def test_approve_plan_key_dispatches_vm_args(jobs):
    app = _app(jobs)
    app.open_job(jobs["plan_review"])
    app.handle_key("a")
    assert app.data.sent == [(jobs["plan_review"], "approvePlan", {"version": 1})]


def test_disabled_key_flashes_reason_without_dispatch(jobs):
    app = _app(jobs)
    app.open_job(jobs["implementing"])
    app.handle_key("y")
    assert app.data.sent == []
    assert app.current_flash()[0] == "Retry: only failed jobs can be retried"
    assert "Retry: only failed jobs can be retried" in _text(app, 100, 30)


def test_tool_approval_and_deny_with_optional_reason(jobs):
    app = _app(jobs)
    app.open_job(jobs["needs_input"])
    text = _text(app, 100, 34)
    assert "◆ Approval needed: bash" in text and "git stash && git checkout main" in text
    event_id = next(a for a in app.jobs[jobs["needs_input"]]["actions"] if a["id"] == "denyTool")["args"]["event_id"]
    app.handle_key("d")
    assert app.mode["kind"] == "prompt" and app.mode["optional"]
    for ch in "use a branch":
        app.handle_key("space" if ch == " " else ch)
    assert "› use a branch" in _text(app, 100, 34)
    app.handle_key("enter")
    assert app.data.sent[-1] == (jobs["needs_input"], "denyTool", {"event_id": event_id, "message": "use a branch"})
    app.handle_key("d")
    app.handle_key("enter")  # optional: empty reason still denies
    assert app.data.sent[-1] == (jobs["needs_input"], "denyTool", {"event_id": event_id})
    app.handle_key("a")
    assert app.data.sent[-1] == (jobs["needs_input"], "approveTool", {"event_id": event_id})


def test_required_input_blocks_empty_submit(jobs):
    app = _app(jobs)
    app.open_job(jobs["plan_review"])
    app.handle_key("x")
    app.handle_key("enter")
    assert app.data.sent == [] and app.mode is not None
    for ch in "smaller":
        app.handle_key(ch)
    app.handle_key("enter")
    assert app.data.sent == [(jobs["plan_review"], "rejectPlan", {"feedback": "smaller"})]


def test_retry_from_dashboard_and_confirm_delete(jobs):
    app = _app(jobs)
    assert app.region == "needs"
    rows = app.home_rows("needs")
    app.cursors["needs"] = next(i for i, r in enumerate(rows) if r["id"] == jobs["failed"])
    app.handle_key("y")
    assert app.data.sent == [(jobs["failed"], "retry", {})]
    app.open_job(jobs["failed"])
    delete = next(a for a in app.jobs[jobs["failed"]]["actions"] if a["id"] == "delete")
    app.trigger(delete, app.jobs[jobs["failed"]])
    assert app.mode["kind"] == "confirm"
    assert "(y/N)" in _text(app, 100, 30)
    app.handle_key("n")
    assert len(app.data.sent) == 1 and app.mode is None
    app.trigger(delete, app.jobs[jobs["failed"]])
    app.handle_key("y")
    assert app.data.sent[-1] == (jobs["failed"], "delete", {})
    assert app.route["name"] == "home"


def test_plan_edit_opens_editor_then_saves_draft(jobs):
    app = _app(jobs)
    seen = {}

    def editor(text, suffix, done):
        seen["text"], seen["suffix"] = text, suffix
        done(text.replace("do it.", "do it carefully."))
    app.editor = editor
    app.open_job(jobs["plan_review"])
    app.handle_key("e")
    assert seen["suffix"] == ".md" and "## Summary" in seen["text"]
    job_id, kind, args = app.data.sent[-1]
    assert (job_id, kind) == (jobs["plan_review"], "savePlanDraft")
    assert args["version"] == 1 and "do it carefully." in args["plan"]


def test_push_form_uses_preview_and_sends_overrides(jobs):
    app = _app(jobs, push_enabled=True)
    app.open_job(jobs["review_done"])
    app.handle_key("p")
    assert app.mode["kind"] == "push"
    text = _text(app, 100, 40)
    assert "mucli/scrum-36 → main · gh" in text and "Title    SCRUM-36: Cache warm-up on deploy" in text
    assert "[x] open as draft" in text
    for ch in "!":
        app.handle_key(ch)
    app.handle_key("tab")
    app.handle_key("tab")
    app.handle_key("space")  # draft off → ready for review
    assert "[ ] ready for review" in _text(app, 100, 40)
    app.handle_key("enter")
    assert app.data.sent[-1] == (jobs["review_done"], "push", {
        "pr_title": "SCRUM-36: Cache warm-up on deploy!", "pr_body": "Summary\n- warm-up hook", "draft": False})


def test_push_form_requires_override_reason_for_do_not_merge(jobs):
    app = _app(jobs, push_enabled=True)
    app.data.push_preview = lambda job: {"title": "t", "body": "", "branch": "b", "base": "main", "mode": "none",
                                         "remote": "", "draft": True, "requires_override": True,
                                         "verdict": "do_not_merge", "head_sha": "", "reason": ""}
    app.open_job(jobs["review_done"])
    app.handle_key("p")
    app.handle_key("enter")
    assert app.data.sent == [] and app.mode["fields"][app.mode["field"]] == "override"
    for ch in "hotfix":
        app.handle_key(ch)
    app.handle_key("enter")
    assert app.data.sent[-1][2]["override_reason"] == "hotfix"


def test_reprompt_uses_marked_findings_and_enter_jumps_to_diff(jobs):
    app = _app(jobs)
    app.open_job(jobs["review_done"])
    st = app.job_state(jobs["review_done"])
    assert st.tab == "review"
    app.handle_key("space")
    app.handle_key("j")
    app.handle_key("space")
    assert st.marked == {"F1", "F2"}
    app.handle_key("F")
    assert app.mode["kind"] == "prompt" and "F1, F2" in app.mode["label"]
    app.handle_key("enter")
    assert app.data.sent[-1] == (jobs["review_done"], "reprompt", {"finding_ids": ["F1", "F2"], "guidance": ""})
    st.finding = 1  # F2 → mu/jobs/worker.py
    app.handle_key("enter")
    assert st.tab == "diff" and st.diff_file == 1


def test_mark_viewed_dispatches_hidden_action(jobs):
    app = _app(jobs)
    app.open_job(jobs["review_done"])
    app.handle_key("3")
    action = next((a for a in app.jobs[jobs["review_done"]]["actions"] if a["id"] == "markViewed"), None)
    app.handle_key("v")
    if action is None:  # backend without markViewed: session-local mark
        assert app.data.sent == []
    else:
        assert action["hidden"]
        assert app.data.sent[-1] == (jobs["review_done"], "markViewed",
                                     {"path": "mu/jobs/sandbox/worker.py", "viewed": True})
    assert "markViewed" not in [a["id"] for a in app.visible_actions(app.jobs[jobs["review_done"]]["actions"])]


def test_nav_keys_never_trigger_actions(jobs):
    app = _app(jobs)
    app.open_job(jobs["review_done"])
    for key in ("j", "k", "g", "G", "n", "N", "f", "l"):
        app.handle_key(key)
    assert app.data.sent == []


def test_real_dispatch_round_trip_changes_state(jobs, tmp_path, monkeypatch):
    from mu.jobs import JobService, JobStore
    from mu.jobs.core.plan_ops import record_planner_plan
    from mu.jobs.core.states import JobState as S

    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    ws = tmp_path / "ws"
    ws.mkdir()
    job = service.create_from_payload({"title": "Ticket", "workspace_root": str(ws),
                                       "execution": {"provider": "ollama", "model": "small"}})
    for st in (S.PROVISIONING, S.PLANNING):
        service.store.transition_core(job.id, st)
    record_planner_plan(service.get(job.id), {"summary": "s", "steps": ["a"], "acceptance_criteria": ["b"],
                                              "validation_commands": ["pytest"]})
    service.store.transition_core(job.id, S.PLAN_REVIEW)
    data = JobsData(service)
    monkeypatch.setattr("mu.jobs.core.commands.ensure_controller_daemon", lambda: None)
    app = JobsApp(data)
    app.refresh()
    assert data.changes() == []  # cursor initialised
    app.open_job(job.id)
    app.handle_key("a")
    assert service.store.core_state(job.id).state == S.IMPLEMENTING
    assert app.jobs[job.id]["state"] == "implementing"
    assert app.current_flash() == ("✓ approvePlan sent", "ok")
    changes = data.changes()
    assert any(c["scope"] == "job" and c["id"] == job.id for c in changes)
    assert app.apply_changes(changes) is True
    with pytest.raises(DispatchError):
        data.dispatch(job.id, "approvePlan", {"version": 1})  # disabled now → rejected


# ---------------------------------------------------------------- activity

class ActivityData(RecordingData):
    def __init__(self, service):
        super().__init__(service)
        self.n = 0

    def activity(self, home=None):
        self.n += 1
        return {"generated_at": time.time(), "engine": "up",
                "counts": {"agents": 1, "checks": 0, "containers": 2, "needs_you": 0},
                "jobs": [{"id": "abc123", "key": "SCRUM-40", "title": "SAM", "state": "implementing",
                          "stage": "implement", "since": time.time() - 600, "cost_usd": 2.25, "kind": "agent",
                          "last": "Generating it 28/1000",
                          "containers": [{"name": "mucli-job-abc123abc123-implement", "id": "c1", "image": "mucli:latest",
                                          "status": "running", "stage": "implement", "started_at": time.time() - 600,
                                          "cpu_percent": None if self.n == 1 else 10.0 * self.n,
                                          "mem_bytes": None if self.n == 1 else 2 ** 28,
                                          "mem_limit": 2 ** 31}]}],
                "orphans": [{"name": "mucli-job-dead00dead00-checks", "id": "c2", "image": "mucli:latest",
                             "status": "exited", "stage": "checks", "started_at": None, "cpu_percent": None,
                             "mem_bytes": None, "mem_limit": None}]}


def test_activity_tree_sparklines_and_sample_cap(jobs):
    app = JobsApp(ActivityData(jobs["_service"]))
    app.refresh()
    app.handle_key("v")
    for _ in range(70):
        app.refresh_activity()
    hist = app.samples["mucli-job-abc123abc123-implement"]
    assert len(hist["cpu"]) == tui_app.SAMPLES == 60
    text = _text(app, 140, 30)
    assert "╭ Activity 1 agents · 0 checks · 2 containers" in text
    assert "SCRUM-40" in text and "agent     implement" in text and "› Generating it 28/1000" in text
    assert "mucli-job-abc123abc123-implement" in text and "cpu" in text and "256.0M" in text
    assert any(0x2800 <= ord(ch) <= 0x28FF for ch in text)  # braille sparkline
    assert "(not attributed to an active job)" in text
    app.cursors["activity"] = 3  # orphan header → enter flashes
    app.handle_key("K")
    assert app.current_flash()[0] == "select a container to remove"
    app.cursors["activity"] = 4
    app.handle_key("K")
    assert app.mode["kind"] == "confirm" and "mucli-job-dead00dead00-checks" in app.mode["text"]
    app.handle_key("n")
    app.cursors["activity"] = 0
    app.handle_key("escape")
    assert app.route["name"] == "home"


def test_activity_fallback_without_backend(jobs, monkeypatch):
    data = RecordingData(jobs["_service"])
    monkeypatch.setattr(JobsData, "_optional", staticmethod(lambda *names: None))
    app = JobsApp(data)
    app.refresh()
    app.open_activity()
    assert app.activity_vm["fallback"] and {j["key"] for j in app.activity_vm["jobs"]} == {"SCRUM-40", "SCRUM-38"}
    assert "container stats unavailable" in _text(app, 120, 30)


# ---------------------------------------------------------------- primitives / theme

def test_color_mode_detection():
    assert theme.color_mode({"NO_COLOR": "1", "COLORTERM": "truecolor"}) == "none"
    assert theme.color_mode({"COLORTERM": "truecolor"}) == "truecolor"
    assert theme.color_mode({"COLORTERM": "24bit"}) == "truecolor"
    assert theme.color_mode({"WT_SESSION": "x"}) == "truecolor"
    assert theme.color_mode({}) == "16"
    assert theme.pt_style("accent bold", "truecolor") == "fg:#7aa2f7 bold"
    assert theme.pt_style("muted", "16") == "fg:ansibrightblack"
    assert theme.pt_style("danger bold", "none") == "bold"


def test_status_vocabulary_and_state_style():
    assert {k: v["icon"] for k, v in theme.STATUS.items()} == {
        "done": "✓", "active": "●", "waiting": "◆", "failed": "✗", "skipped": "–", "pending": "○"}
    assert theme.state_style("review_done") == {"status": "waiting", "token": "warn", "icon": "◆", "label": "review done"}
    assert theme.state_style("implementing")["token"] == "running"
    assert theme.state_style("failed")["token"] == "danger"


def test_bars_sparkline_and_hints():
    assert primitives.progress_text(0.5, 10) == "▕████    ▏"
    assert primitives.progress_text(1, 6) == "▕████▏"
    assert sum(primitives.stacked_cells([41, 1, 1], 20)) == 20
    assert primitives.stacked_cells([41, 1, 0], 20)[1] >= 1
    spark = primitives.sparkline([0, 25, 50, 100, None, None], 3, 100)
    assert len(spark) == 3 and spark[-1] == " "
    hints = primitives.line_text(primitives.key_hints([("enter", "open"), ("v", "activity")] * 10, 40))
    assert hints.rstrip().endswith(": commands · ? help")
    assert primitives.fit("abcdef", 4) == "abc…"
    steps = [{"id": "plan", "label": "Plan", "status": "done", "elapsed": 360},
             {"id": "approve", "label": "Approve", "status": "done", "elapsed": 5},
             {"id": "implement", "label": "Implement", "status": "active", "elapsed": 1080},
             {"id": "checks", "label": "Checks", "status": "pending"},
             {"id": "review", "label": "Review", "status": "pending"},
             {"id": "push", "label": "Push", "status": "pending"}]
    assert primitives.line_text(primitives.stepper(steps, 200)) == \
        "Plan ✓ 6m ── Approve ✓ ── Implement ● 18m ── Checks ○ ── Review ○ ── Push ○"
    assert primitives.line_text(primitives.stepper(steps, 52)) == "Plan ✓ ─ Appr ✓ ─ Impl ● ─ Chec ○ ─ Revi ○ ─ Push ○"
    assert primitives.line_text(primitives.stepper(steps, 20)) == "✓─✓─●─○─○─○"


def test_spinner_uses_shared_tick(jobs):
    app = _app(jobs)
    first = _text(app, 120, 30)
    app.tick += 1
    second = _text(app, 120, 30)
    assert theme.SPINNER[0] in first and theme.SPINNER[1] in second
    assert theme.TICK_S == 0.120


def test_fuzzy_palette_ranking():
    items = [{"label": "Approve plan", "key": "a"}, {"label": "Activity", "key": "v"},
             {"label": "Delete", "enabled": False, "reason": "x"}, {"label": "Push / open PR", "key": "p"}]
    assert rank_palette(items, "appr")[0]["label"] == "Approve plan"
    assert rank_palette(items, "")[-1]["label"] == "Delete"  # disabled last
    assert fuzzy("zz", "Approve") == -1


def test_normalize_action_fills_contract_fields():
    a = normalize_action({"id": "denyTool", "label": "Deny tool", "enabled": True})
    assert a["tone"] == "danger" and a["input"]["optional"] and a["hidden"] is False
    assert normalize_action({"id": "push"})["dialog"] == "push"
    assert normalize_action({"id": "markViewed"})["hidden"] is True
    assert normalize_action({"id": "push", "dialog": None})["dialog"] is None  # backend wins


def test_parse_patch_counts_and_line_numbers():
    files = content.parse_patch(PATCH)
    assert [(f["path"], f["added"], f["removed"]) for f in files] == [
        ("mu/jobs/sandbox/worker.py", 3, 0), ("mu/jobs/worker.py", 1, 1)]
    _, rows = content.diff_hunk_lines(files[1], 80)
    assert 360 in rows


# ---------------------------------------------------------------- /job command + guards

def test_job_command_without_tty_keeps_text_board(jobs, monkeypatch):
    from mu.commands import job as terminal
    monkeypatch.setattr(terminal, "get_default_job_service", lambda: jobs["_service"])
    result = terminal.job_cmd(SimpleNamespace(ui=None), "", allow_prompt=False)
    assert result.ok and set(result.data) >= {"needs_you", "running", "jobs"}
    explicit = terminal.job_cmd(SimpleNamespace(ui=None), "ui", allow_prompt=False)
    assert not explicit.ok and "interactive terminal" in explicit.message and "jobs" in explicit.data


def test_jobs_command_opens_dashboard_in_a_terminal(jobs, monkeypatch):
    import mu.ui.jobs as tui
    from mu.commands import get, job as terminal
    calls = []
    monkeypatch.setattr(terminal, "get_default_job_service", lambda: jobs["_service"])
    monkeypatch.setattr(tui, "can_open_tui", lambda session=None, allow_prompt=True: allow_prompt)
    monkeypatch.setattr(tui, "open_jobs_tui", lambda **kw: calls.append(kw))
    assert get("/jobs").handler is get("/job").handler
    assert terminal.job_cmd(SimpleNamespace(ui=None), "").message == "Closed the job dashboard."
    terminal.job_cmd(SimpleNamespace(ui=None), "ui SCRUM")
    assert [c["start_job"] for c in calls] == [None, "SCRUM"]
    # subcommands are untouched
    shown = terminal.job_cmd(SimpleNamespace(ui=None), f"show {jobs['plan_review']}", allow_prompt=False)
    assert shown.data["state"] == "plan_review"


def test_can_open_tui_refuses_non_interactive(monkeypatch):
    from mu.ui.jobs import can_open_tui
    assert can_open_tui(allow_prompt=False) is False
    monkeypatch.setenv("MUCLI_NO_JOBS_TUI", "1")
    assert can_open_tui(allow_prompt=True) is False


ALLOWED_CORE = {"mu.jobs.core.vm", "mu.jobs.core.commands", "mu.jobs.core.changes", "mu.jobs.core.actions"}


def test_tui_package_imports_only_core_ui_modules():
    bad = []
    for path in sorted((ROOT / "mu/ui/jobs").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            mods = []
            if isinstance(node, ast.ImportFrom) and node.module:
                mods = [f"{node.module}.{a.name}" for a in node.names] if node.module == "mu.jobs.core" else [node.module]
            elif isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            bad += [f"{path.name}:{m}" for m in mods
                    if (m == "mu.jobs" or m.startswith("mu.jobs.")) and m not in ALLOWED_CORE]
    assert bad == []
