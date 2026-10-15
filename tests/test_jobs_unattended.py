"""Jobs run unattended (user decision 2026-10-15, ``mu.jobs.unattended``).

Stage tool calls are never prompted: implement/checks auto-approve inside the sandbox, plan/review
deny writes automatically, agent questions are auto-answered, and nothing in a stage parks the
job. Parked approvals from before the rule resume on their own; repository trust decisions fall
back instead of parking. No real Docker."""
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from mu.jobs import JobService, JobSpec, JobStore
from mu.jobs.models import AttentionReason, JobStatus, normalize_execution
from mu.jobs.ui import JobUI
from mu.jobs.unattended import AUTO_ANSWER, deny_message
from test_job_worker import running_job


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def _events(service, job_id, kind):
    return [e for e in service.events(job_id) if e.event_type == kind]


# ------------------------------------------------------------------ JobUI (every in-process stage)


@pytest.mark.parametrize("stage", ["plan", "review"])
def test_read_only_stages_deny_writes_automatically(tmp_path, stage):
    service, _, job, _, _ = running_job(tmp_path)
    ui = JobUI(service, job.id, stage=stage)
    decision = ui.request_tool_approval(tool_name="write_file", tool_args={"filename": "x.py"})
    assert decision == ("e", deny_message(stage))
    assert f"read-only {stage} stage; describe the change instead" in decision[1]
    event = _events(service, job.id, "auto_permission")[-1]
    assert event.payload["decision"] == "deny" and event.payload["stage"] == stage
    assert service.get(job.id).status == JobStatus.RUNNING  # never parked


@pytest.mark.parametrize("stage", ["implement", "checks"])
def test_writing_stages_approve_automatically(tmp_path, stage):
    service, _, job, _, _ = running_job(tmp_path)
    ui = JobUI(service, job.id, stage=stage)
    for _ in range(3):
        assert ui.request_tool_approval(tool_name="bash", tool_args={"command": "npm ci"}) == ("y", None)
    decisions = _events(service, job.id, "auto_permission")
    assert [e.payload["decision"] for e in decisions] == ["approve"] * 3
    assert "Auto-approved bash" in decisions[0].reason
    # A call whose preview failed cannot run: the agent is told, still without a prompt.
    verdict, reason = ui.request_tool_approval(tool_name="patch", can_approve=False, preview_error="hunk 1 failed")
    assert verdict == "e" and "hunk 1 failed" in reason


def test_stage_is_derived_from_the_job_when_not_given(tmp_path):
    service, _, job, _, _ = running_job(tmp_path)  # running = implement
    assert JobUI(service, job.id).request_tool_approval(tool_name="write_file") == ("y", None)
    service.transition(job.id, JobStatus.VERIFYING)
    service.transition(job.id, JobStatus.REVIEWING)
    assert JobUI(service, job.id).request_tool_approval(tool_name="write_file")[0] == "e"


def test_questions_are_answered_automatically_and_recorded(tmp_path):
    service, _, job, _, _ = running_job(tmp_path)
    ui = JobUI(service, job.id, stage="plan")
    assert ui.prompt("Which database?") == AUTO_ANSWER
    assert ui.confirm("Proceed?", default=True) is True
    assert ui.prompt_choices("Pick", ["a", "b"]) == "a"
    assert ui.prompt_choices("Pick", ["a", "b"], default="b") == "b"
    assert ui.run_quiz([{"question": "Q1"}]) == {}
    choice = ui.ask_user_choice("Which approach?", ["A", "B"], allow_other=True)
    assert choice == {"selected": [], "other_text": AUTO_ANSWER, "cancelled": False}
    answered = _events(service, job.id, "auto_answered")
    assert [e.payload["shape"] for e in answered] == ["input", "confirm", "choices", "choices", "quiz", "choice"]
    assert answered[0].payload["question"] == "Which database?" and answered[0].payload["answer"] == AUTO_ANSWER
    assert service.get(job.id).status == JobStatus.RUNNING


def test_the_ask_user_choice_tool_returns_the_automatic_answer(tmp_path):
    from mu.tools.prompt.handlers import ask_user_choice_tool
    service, _, job, _, _ = running_job(tmp_path)
    ui = JobUI(service, job.id, stage="implement")
    out = json.loads(ask_user_choice_tool({"question": "Which?", "options": ["x", "y"]}, SimpleNamespace(ui=ui)))
    assert out["ok"] and not out["cancelled"] and out["other_text"] == AUTO_ANSWER


# ------------------------------------------------------------------ mucli stage worker (in the container)


@pytest.mark.parametrize("stage,expected", [("plan", "e"), ("review", "e"), ("implement", "y")])
def test_mucli_stage_ui_never_raises(monkeypatch, capsys, stage, expected):
    from mu.jobs.sandbox.worker import StageUI
    monkeypatch.delenv("MUCLI_MODEL_RELAY", raising=False)  # no relay: decided locally
    ui = StageUI(stage)
    assert ui.request_tool_approval(tool_name="write_file", tool_args={})[0] == expected
    assert ui.prompt("Anything?") == AUTO_ANSWER
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [line["event"] for line in lines] == ["auto_permission", "auto_answered"]
    assert lines[0]["payload"]["stage"] == stage and lines[0]["reason"]


def test_mucli_worker_auto_approves_only_the_writing_stage():
    from mu.jobs.sandbox import worker
    assert "runs unattended" in worker.UNATTENDED_NOTE  # the stage system prompt says so
    source = (Path(__file__).resolve().parents[1] / "mu/jobs/sandbox/worker.py").read_text()
    assert "yolo=stage not in READ_ONLY_STAGES" in source
    assert "plan_mode=stage in READ_ONLY_STAGES" in source
    assert "auto_approve_writes" not in source  # no per-job switch


# ------------------------------------------------------------------ Claude Code / Codex harness worker


class _Run:
    """A harness Run with its CLI pipe and event stream captured."""

    def __init__(self, monkeypatch, stage, tmp_path):
        from mu.jobs.sandbox import harness_worker
        monkeypatch.setenv("MUCLI_JOB_STAGE", stage)
        monkeypatch.setattr(harness_worker, "DELIVERED", tmp_path / "delivered.json")
        monkeypatch.setattr(harness_worker, "PENDING", tmp_path / "pending.json")
        self.events, self.written = [], []
        monkeypatch.setattr(harness_worker, "emit", lambda event, payload=None, reason="": self.events.append(
            (event, payload or {}, reason)))
        posted = []
        monkeypatch.setattr(harness_worker, "relay_post", lambda *a, **k: posted.append(a) or {})
        self.posted = posted
        self.run = harness_worker.Run("claude", {"prompt": "go"})
        self.run.write = self.written.append
        self.module = harness_worker

    def control(self, tool, tool_input=None):
        self.run.on_control({"type": "control_request", "request_id": "r1", "request": {
            "subtype": "can_use_tool", "tool_name": tool, "input": tool_input or {"file_path": "/workspace/x"}}})
        return json.loads(self.written[-1])["response"]["response"]


@pytest.mark.parametrize("stage", ["plan", "review"])
def test_harness_read_only_stage_denies_a_stray_permission_request(monkeypatch, tmp_path, stage):
    h = _Run(monkeypatch, stage, tmp_path)
    reply = h.control("Write")
    assert reply == {"behavior": "deny", "message": deny_message(stage)}
    assert h.events[-1][0] == "auto_permission" and h.events[-1][1]["decision"] == "deny"
    assert not h.posted  # never sent to the host, never parked
    assert "attention_reason" not in h.run.result(0, "")  # no approval park exists any more


def test_harness_implement_approves_and_answers_questions(monkeypatch, tmp_path):
    from mu.jobs.harness_cli import NO_QUESTION_TOOLS
    h = _Run(monkeypatch, "implement", tmp_path)
    reply = h.control("Bash", {"command": "npm ci"})
    assert reply == {"behavior": "allow", "updatedInput": {"command": "npm ci"}}
    assert h.events[-1][1] == {"stage": "implement", "tool_name": "Bash", "decision": "approve", "source": "claude"}
    ask = h.control(NO_QUESTION_TOOLS[0], {"questions": [{"question": "Which?"}]})
    assert ask == {"behavior": "deny", "message": AUTO_ANSWER}
    assert h.events[-1][0] == "auto_answered"
    assert not h.posted


def test_harness_resume_of_a_legacy_parked_approval(monkeypatch, tmp_path):
    h = _Run(monkeypatch, "implement", tmp_path)
    h.module.PENDING.write_text(json.dumps({"tool_name": "Edit"}))
    h.run.request = {"prompt": "Continue.", "resume_session": "sid"}
    text = h.run.prompt()
    assert "pending Edit call is now approved automatically" in text and not h.module.PENDING.exists()
    assert h.events[-1][0] == "auto_permission" and h.events[-1][1]["source"] == "resume"


def test_harness_stage_request_has_no_auto_approve_switch():
    source = (Path(__file__).resolve().parents[1] / "mu/jobs/sandbox/harness_worker.py").read_text()
    assert "auto_approve" not in source and "relay_post(\"/interaction\"" not in source


# ------------------------------------------------------------------ host relay fallback


def test_host_resolves_a_stage_interaction_automatically(tmp_path):
    from mu.jobs.sandbox.runner import claim_stage_response
    service, _, job, _, _ = running_job(tmp_path)
    approve = claim_stage_response(service, job, "implement", {"kind": "approval_required", "tool_name": "Edit"})
    deny = claim_stage_response(service, job, "plan", {"kind": "approval_required", "tool_name": "Edit"})
    answer = claim_stage_response(service, job, "review", {"kind": "question", "message": "Which?"})
    assert approve["decision"] == "approve" and deny["decision"] == "deny" and answer["value"] == AUTO_ANSWER
    assert [e.payload["decision"] for e in _events(service, job.id, "auto_permission")] == ["approve", "deny"]
    assert _events(service, job.id, "auto_answered")[0].payload["question"] == "Which?"
    assert service.get(job.id).status == JobStatus.RUNNING


# ------------------------------------------------------------------ no per-job auto-approve


def test_auto_approve_false_on_input_is_ignored(tmp_path):
    assert normalize_execution({"auto_approve_writes": False})["auto_approve_writes"] is True
    assert normalize_execution({})["auto_approve_writes"] is True
    service = JobService(JobStore(str(tmp_path / "jobs.db")))
    job = service.create(JobSpec(title="t", execution={"auto_approve_writes": False}))
    assert service.get(job.id).execution["auto_approve_writes"] is True


def test_terminal_create_accepts_the_retired_flag_silently():
    from mu.commands.job import _parse_create
    for flag in ("--auto-approve", "--no-auto-approve"):
        values = _parse_create(f'"Fix it" {flag}', {"workspace_root": "/w", "execution": {}})
        assert values["title"] == "Fix it"
        assert "auto_approve_writes" not in values["execution"]


def test_jobs_tui_and_terminal_offer_no_auto_approve_option():
    root = Path(__file__).resolve().parents[1]
    for rel in ("mu/ui/jobs/app.py", "mu/ui/jobs/views.py", "mu/ui/jobs/data.py"):
        text = (root / rel).read_text().lower()
        assert "auto-approve" not in text and "auto_approve" not in text and "unattended in sandbox" not in text, rel
    command = (root / "mu/commands/job.py").read_text()
    assert 'execution["auto_approve_writes"]' not in command


# ------------------------------------------------------------------ parked approvals resume on their own


def _park(service, job, payload):
    service.require_human(job.id, AttentionReason.APPROVAL_REQUIRED, "Approval required", payload=payload)
    service.release(job.id, "worker", reason="test")


def test_a_parked_implement_approval_is_approved_and_resumed_on_the_next_tick(tmp_path):
    from mu.jobs.controller import JobController
    service, _, job, _, _ = running_job(tmp_path)
    _park(service, job, {"tool_name": "Bash", "tool_args": {"command": "npm ci"}, "can_approve": True})
    controller = JobController(service, process_factory=lambda *a, **k: pytest.fail("no worker in this test"))
    controller._resume_parked_approvals()
    resumed = service.get(job.id)
    assert resumed.status == JobStatus.QUEUED
    response = _events(service, job.id, "interaction_response")[-1].payload
    assert response["decision"] == "approve" and response["target"]["tool_name"] == "Bash"
    decided = _events(service, job.id, "auto_permission")[-1]
    assert decided.payload == {"stage": "implement", "tool_name": "Bash", "decision": "approve", "source": "parked"}
    # The resumed stage's next request for that exact call consumes the recorded approval once.
    assert JobUI(service, job.id, stage="implement").request_tool_approval(tool_name="Bash") == ("y", None)


def test_a_parked_plan_approval_is_denied_and_resumed(tmp_path):
    from mu.jobs.unattended import resume_parked_approvals
    service = JobService(JobStore(str(tmp_path / "jobs.db")))
    job = service.create_from_payload({"title": "Plan", "workspace_root": str(tmp_path)})
    service.transition(job.id, JobStatus.PREPARING)
    service.transition(job.id, JobStatus.PLANNING)
    service.require_human(job.id, AttentionReason.APPROVAL_REQUIRED, "Approval required",
                          payload={"tool_name": "write_file", "can_approve": True})
    assert resume_parked_approvals(service) == [job.id]
    response = _events(service, job.id, "interaction_response")[-1].payload
    assert response["decision"] == "deny" and "read-only plan stage" in response["detail"]
    assert service.get(job.id).status != JobStatus.NEEDS_HUMAN


def test_resume_command_resolves_a_parked_approval(tmp_path):
    from mu.jobs.core.commands import dispatch
    from mu.jobs.core.vm import job_vm
    service, _, job, _, _ = running_job(tmp_path)
    _park(service, job, {"tool_name": "Edit", "tool_args": {}, "can_approve": True})
    resume = next(a for a in job_vm(service, job.id)["actions"] if a["id"] == "resume")
    assert resume["enabled"]
    dispatch(service, {"type": "resume", "job_id": job.id})
    assert service.get(job.id).status == JobStatus.QUEUED
    assert _events(service, job.id, "auto_permission")[-1].payload["decision"] == "approve"


def test_other_parks_are_left_alone(tmp_path):
    from mu.jobs.unattended import resume_parked_approvals
    service, _, job, _, _ = running_job(tmp_path)
    service.require_human(job.id, AttentionReason.LIMIT_REACHED, "cost limit")
    assert resume_parked_approvals(service) == []
    assert service.get(job.id).status == JobStatus.NEEDS_HUMAN


# ------------------------------------------------------------------ repository trust: fall back, never park


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def _repo_job(tmp_path, files):
    from mu.jobs.worktree import JobWorktreeManager
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    for rel, text in ({"app.py": "print('hi')\n"} | files).items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    service = JobService(JobStore(str(tmp_path / "jobs.db")))
    job = service.create(JobSpec(title="t", repository=str(repo)))
    JobWorktreeManager(service, root=str(tmp_path / "worktrees")).prepare(job)
    return service, service.get(job.id), repo


def test_an_unapproved_repository_image_falls_back_with_an_event(tmp_path):
    from mu.jobs.core.vm import environment_approval, facts_for
    from mu.jobs.core.actions import action_map
    from mu.jobs.sandbox.environments import ensure_environments
    service, job, repo = _repo_job(tmp_path, {".mucli/jobs.toml": '[sandbox]\nimage = "ghcr.io/acme/builder:2"\n'})
    assert ensure_environments(service, job, settings={}) == []  # the default runtime: no park
    skipped = _events(service, job.id, "repository_environment_skipped")
    assert len(skipped) == 1
    payload = skipped[0].payload
    assert payload["fingerprint"] == "image:ghcr.io/acme/builder:2" and payload["action"] == "approveEnvironment"
    assert payload["used"] == "default" and "future jobs" in skipped[0].reason
    # The existing approval UI offers it, non-blocking.
    pending = environment_approval(service, service.get(job.id))
    assert pending["fingerprint"] == payload["fingerprint"] and pending["blocking"] is False
    assert action_map(facts_for(service, service.get(job.id)))["approveEnvironment"].enabled
    # Once per job, and pinned for the rest of the job even after approval.
    from mu.jobs.sandbox.devcontainer import approve
    approve(str(repo), payload["fingerprint"])
    assert ensure_environments(service, service.get(job.id), settings={}) == []
    assert len(_events(service, job.id, "repository_environment_skipped")) == 1
    assert service.get(job.id).status != JobStatus.NEEDS_HUMAN


def test_a_secret_scan_hit_falls_back_to_the_next_source(tmp_path, monkeypatch):
    from mu.jobs.sandbox import environments as envs
    service, job, _ = _repo_job(tmp_path, {})
    service.store.update_runtime_fields(job.id, execution_json={**job.execution, "environments": ["image:leaky:1"]})

    def snapshot(job, binding, **kwargs):
        error = envs.EnvironmentBlocked(f"Build environment `{binding['name']}` contains secret files: /root/.ssh/id_rsa.")
        error.secret_paths = ["/root/.ssh/id_rsa"]
        raise error
    monkeypatch.setattr(envs, "snapshot_binding", snapshot)
    monkeypatch.setattr(envs, "resolve_source", lambda b: {"image": b["ref"], "egress_allow": [], "gpus": ""})
    monkeypatch.setattr(envs, "cleanup_job", lambda *a, **k: {})
    states = envs.ensure_environments(service, service.get(job.id), docker=SimpleNamespace(runner=None), settings={})
    assert states == []  # the image is never used; the job runs on the next source (default runtime)
    skipped = _events(service, job.id, "build_environment_skipped")
    assert len(skipped) == 1 and skipped[0].payload["reason"] == "secret_scan"
    assert skipped[0].payload["secret_paths"] == ["/root/.ssh/id_rsa"] and skipped[0].payload["origin"] == "job"
    meta = service.get(job.id).metadata
    assert meta["environments_excluded"] == ["job"] and meta["build_environments"] == []
    # The next stage does not retry the refused image.
    assert envs.ensure_environments(service, service.get(job.id), docker=SimpleNamespace(runner=None), settings={}) == []
    assert len(_events(service, job.id, "build_environment_skipped")) == 1


# ------------------------------------------------------------------ the planner states its assumptions


def test_planner_assumptions_are_listed_in_the_plan():
    from mu.jobs.core.plan_doc import PlanDoc
    from mu.jobs.stages import build_plan_prompt, parse_plan
    job = SimpleNamespace(title="Add export", description="", acceptance_criteria=[], validation_commands=[],
                          metadata={}, repository="", worktree="", execution={})
    prompt = build_plan_prompt(job)
    assert "runs unattended" in prompt and '"assumptions"' in prompt
    plan = parse_plan(json.dumps({"summary": "s", "steps": ["a"], "acceptance_criteria": ["x"],
                                  "validation_commands": ["pytest -q"], "assumptions": ["CSV, not XLSX"]}))
    assert plan["risks"] == ["Assumption: CSV, not XLSX"]
    assert "- Assumption: CSV, not XLSX" in PlanDoc.from_legacy(plan).render()
    assert "risks" not in parse_plan(json.dumps({"summary": "s"}))


# ------------------------------------------------------------------ surfaces show the non-blocking outcomes


def test_web_and_terminal_show_the_setup_warning_and_unverified_push():
    root = Path(__file__).resolve().parents[1]
    view = (root / "mu/gui/static/js/work/view.js").read_text()
    assert "job.setup_warning" in view and "data-setup-warning" in view
    assert "Job setup failed; the job continued without it" in view
    assert "data-push-unverified" in view and "The PR body says so." in view
    assert "pushing needs an override" not in view
    tui = (root / "mu/ui/jobs/views.py").read_text()
    assert 'vm.get("setup_warning")' in tui and "Job setup failed; continued without it" in tui
    assert "Repository environment skipped (approve for future jobs)" in tui


def test_session_view_lists_automatic_decisions_once(tmp_path):
    from mu.jobs.core.vm import job_vm
    from mu.ui.jobs.content import session_items
    service, _, job, _, _ = running_job(tmp_path)
    decided = {"stage": "implement", "tool_name": "Bash", "decision": "approve"}
    service.store.append_event(job.id, "sandbox_event", payload={"stage": "implement", "event": "auto_permission",
                                                                 "payload": decided})
    reason = "Auto-approved Bash (implement stage runs unattended in the sandbox)"
    service.store.append_event(job.id, "auto_permission", reason=reason, payload=decided)
    events = job_vm(service, job.id, parts=["session"])["session"]["events"]
    assert [e["event_type"] for e in events if "auto_permission" in (e["event_type"], (e["payload"] or {}).get("event"))] \
        == ["auto_permission"]
    assert reason in [i.get("text") for i in session_items({"events": events, "stages": {}})]
