"""W5 terminal job-mode polish: `/job approve` default version, one-shot `/job <ticket|title> [--in PATH]`,
lowercase-hyphen action names, and the dashboard's Setup/Doctor tabs, Environments fold and detail tab names."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from mu.commands import job as terminal
from mu.jobs import JobService, JobStore
from mu.jobs.models import AttentionReason, JobStatus
from mu.session.visibility import is_user_visible_session
from mu.ui.jobs import views
from mu.ui.jobs.app import SETUP_TABS, TAB_LABEL, TABS, JobsApp
from mu.ui.jobs.data import fold_environment_sections
from mu.ui.jobs.primitives import to_plain

PLAN = {"summary": "Add the widget", "steps": ["edit widget.py"], "acceptance_criteria": ["widget renders"],
        "validation_commands": ["pytest -q"]}


@pytest.fixture
def home(tmp_path, monkeypatch):
    from utils import config

    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(config, "HISTORY_DIR", str(tmp_path / "home"))
    return tmp_path / "home"


@pytest.fixture
def service(tmp_path, monkeypatch, home):
    svc = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    monkeypatch.setattr(terminal, "get_default_job_service", lambda: svc)
    monkeypatch.setattr(terminal, "ensure_controller_daemon",
                        lambda: SimpleNamespace(running=True, detail="controller up"))
    return svc


class UI:
    def __init__(self):
        self.info, self.errors = [], []

    def show_info(self, text):
        self.info.append(text)

    def show_error(self, text):
        self.errors.append(text)


def _no_session():
    """A terminal with no chat session loaded (no manager, provider or workspace)."""
    return SimpleNamespace(ui=UI())


def _workspace(tmp_path, name="repo"):
    ws = tmp_path / name
    ws.mkdir(exist_ok=True)
    return ws


def _at_plan_review(service, tmp_path):
    from mu.jobs.core.plan_ops import record_planner_plan
    from mu.jobs.stages import merge_metadata

    job = service.create_from_payload({"title": "Ticket", "workspace_root": str(_workspace(tmp_path)),
                                       "execution": {"provider": "ollama", "model": "m", "session_type": "workspace"}})
    service.transition(job.id, JobStatus.PREPARING)
    service.transition(job.id, JobStatus.PLANNING)
    merge_metadata(service, job.id, plan=PLAN)
    record_planner_plan(service.get(job.id), PLAN)
    service.require_human(job.id, AttentionReason.PLAN_REVIEW, "review the plan")
    return service.get(job.id)


# ------------------------------------------------------------------ approve default version

def test_approve_without_version_approves_the_latest_plan(service, tmp_path):
    job = _at_plan_review(service, tmp_path)
    latest = terminal.job_cmd(_no_session(), f"show {job.id}", allow_prompt=False).data["plan"]["version"]
    result = terminal.job_cmd(_no_session(), f"approve {job.id[:10]}", allow_prompt=False)
    assert result.ok, result.message
    assert service.get(job.id).metadata["plan_approved_version"] == latest


def test_approve_rejects_a_non_numeric_version(service, tmp_path):
    job = _at_plan_review(service, tmp_path)
    result = terminal.job_cmd(_no_session(), f"approve {job.id} latest-ish", allow_prompt=False)
    assert not result.ok and "plan version must be a number" in result.message


def test_hyphenated_action_names_are_accepted(service, tmp_path):
    job = _at_plan_review(service, tmp_path)
    result = terminal.job_cmd(_no_session(), f"approve-plan {job.id}", allow_prompt=False)
    assert result.ok and result.message == "approvePlan"


def test_help_lists_lowercase_action_names_only():
    from mu.commands import get as get_command

    help_text = get_command("/job").help
    for name in ("approve <id> [version]", "save-plan-draft", "restart-stage", "mark-merged", "--in PATH"):
        assert name in help_text
    for camel in ("savePlanDraft", "restartStage", "markMerged", "approvePlan"):
        assert camel not in help_text


# ------------------------------------------------------------------ one-shot creation

def test_one_shot_title_with_in_needs_no_chat_session(service, tmp_path, home):
    ws = _workspace(tmp_path)
    result = terminal.job_cmd(_no_session(), f"Fix the login bug --in {ws}", allow_prompt=False)
    assert result.ok, result.message
    job = service.list()[0]
    assert job.title == "Fix the login bug"
    assert job.repository == os.path.realpath(ws)
    origin = job.metadata["origin_session"]
    saved = json.loads((home / "sessions" / origin / "session.json").read_text())
    assert saved["folder_context"]["folders"] == [os.path.realpath(ws)]
    # The hidden workspace session stays out of chat session lists, and is reused.
    assert not is_user_visible_session(origin, saved)
    terminal.job_cmd(_no_session(), f"Second task --in={ws}", allow_prompt=False)
    assert {j.metadata["origin_session"] for j in service.list()} == {origin}


def test_create_accepts_in_and_rejects_missing_paths(service, tmp_path):
    ws = _workspace(tmp_path)
    assert terminal.job_cmd(_no_session(), f"create Tidy imports --in {ws}", allow_prompt=False).ok
    missing = terminal.job_cmd(_no_session(), f"Tidy more --in {tmp_path / 'nope'}", allow_prompt=False)
    assert not missing.ok and "not a directory" in missing.message


def test_one_shot_without_in_uses_the_current_session(service, tmp_path):
    ws = _workspace(tmp_path)
    session = SimpleNamespace(ui=UI(), session_manager=SimpleNamespace(current_session_name="mine"),
                              provider=SimpleNamespace(name="ollama", model_name="m"), variables={},
                              folder_context=SimpleNamespace(folders=[str(ws)]))
    result = terminal.job_cmd(session, "Refactor the parser", allow_prompt=False)
    assert result.ok
    job = service.list()[0]
    assert job.title == "Refactor the parser" and job.metadata["origin_session"] == "mine"


def test_one_shot_ticket_key_imports_from_jira(service, tmp_path, monkeypatch):
    """`/job PROJ-7 --in PATH` takes the `/job jira pull` path with the workspace from --in."""
    seen = {}

    def fake_import(svc, *, keys, jql, defaults):
        seen.update(keys=list(keys), jql=jql, defaults=defaults)
        return {"created": [{"key": keys[0], "job_id": "abc1234567", "title": "Fix login"}], "skipped": [], "errors": []}

    monkeypatch.setattr(terminal.commands, "import_jira", fake_import)
    ws = _workspace(tmp_path)
    result = terminal.job_cmd(_no_session(), f"PROJ-7 --in {ws}", allow_prompt=False)
    assert result.ok, result.message
    assert seen["keys"] == ["PROJ-7"] and seen["jql"] == ""
    assert seen["defaults"]["workspace_root"] == os.path.realpath(ws)
    assert seen["defaults"]["metadata"]["origin_session"].startswith("jobs-repo-")
    assert "repository" not in seen["defaults"] and "repo" not in seen["defaults"]


def test_single_unknown_word_never_creates_a_job(service):
    result = terminal.job_cmd(_no_session(), "frobnicate", allow_prompt=False)
    assert not result.ok and "No job or subcommand" in result.message
    typo = terminal.job_cmd(_no_session(), "setings", allow_prompt=False)
    assert not typo.ok and "/job settings" in typo.message
    assert service.list() == []


def test_mistyped_subcommand_with_arguments_is_not_a_title(service, tmp_path):
    result = terminal.job_cmd(_no_session(), f"aprove abc123 --in {_workspace(tmp_path)}", allow_prompt=False)
    assert not result.ok and "/job approve" in result.message
    assert service.list() == []


def test_existing_subcommands_and_job_ids_still_win(service, tmp_path):
    job = _at_plan_review(service, tmp_path)
    shown = terminal.job_cmd(_no_session(), job.id[:10], allow_prompt=False)
    assert shown.ok and shown.data["state"] == "plan_review"
    assert terminal.job_cmd(_no_session(), "list", allow_prompt=False).ok
    assert len(service.list()) == 1


# ------------------------------------------------------------------ dashboard

def test_detail_tabs_match_web_and_mobile():
    assert TABS == ("session", "plan", "diff", "review", "checks", "diagnostics", "costs")
    assert TAB_LABEL["diagnostics"] == "Diagnostics" and TAB_LABEL["costs"] == "Cost & audit"


def test_toolchains_fold_into_environments():
    sections = [{"id": "checks", "fields": [{"path": "a"}]},
                {"id": "toolchains", "title": "Toolchains", "hint": "rows", "fields": [{"path": "sandbox.toolchains"}]},
                {"id": "build_environments", "title": "Build environments", "fields": [{"path": "environments.x"}]}]
    folded = fold_environment_sections(sections)
    assert [s["id"] for s in folded] == ["checks", "build_environments"]
    env = folded[1]
    assert env["title"] == "Environments" and env["toolchains"]
    assert [f["path"] for f in env["fields"]] == ["environments.x", "sandbox.toolchains"]
    assert fold_environment_sections(sections[:1]) == sections[:1]


class _SetupData:
    def __init__(self):
        self.calls = []

    def home(self):
        return {"needs_you": [], "running": [], "jobs": []}

    def status(self, home=None):
        return {}

    def needs_setup(self):
        return False

    def setup(self, *, recheck=False):
        self.calls.append("setup")
        return {"steps": [], "done": 0, "total": 0, "blocking": [], "can_finish": False}

    def doctor(self, *, deep=False):
        self.calls.append("doctor")
        return [{"name": "docker", "status": "ok", "detail": "ok"}]


def test_doctor_is_a_tab_of_setup():
    app = JobsApp(_SetupData())
    app.run_background = lambda work: work()
    app.refresh()
    app.handle_key("g")
    app.handle_key("u")
    assert app.route["name"] == "setup"
    depth = len(app.stack)
    text = to_plain(views.render(app, 120, 30))
    assert "Checklist" in text and "Doctor" in text
    app.handle_key("tab")
    assert app.route["name"] == "doctor" and len(app.stack) == depth
    app.handle_key("tab")
    assert app.route["name"] == "setup" and len(app.stack) == depth
    app.handle_key("g")
    app.handle_key("d")
    assert app.route["name"] == "doctor" and len(app.stack) == depth
    app.handle_key("escape")
    assert app.route["name"] == "home"
    go = [i["label"] for i in app.palette_items() if i["group"] == "go"]
    assert sum(1 for label in go if label.startswith(("Setup", "Doctor"))) == 1
    assert set(SETUP_TABS) == {"setup", "doctor"}
