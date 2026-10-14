"""Job caps pause and ask instead of stopping (cost, tokens, time, iterations).

Reaching any job cap pauses the running stage resumably, records its cost and parks the
job on NEEDS_HUMAN / ``limit_reached`` with a structured detail. ``raiseLimit`` approves
more and the paused stage continues with the same conversation; cancel still works; a job
without a cost cap never pauses for it.
"""
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from providers.base import Message, MessagePart, StreamEvent
from mu.jobs import JobService, JobSpec, JobStore
from mu.jobs import limits
from mu.jobs.core import commands
from mu.jobs.core.artifacts import JobArtifacts, artifacts_for
from mu.jobs.core.states import JobState
from mu.jobs.models import AttentionReason, JobStatus
from mu.jobs.sandbox import runner
from mu.jobs.sandbox.model_relay import HarnessRelay, ModelRelay, RelayDenied, wire
from mu.jobs.sandbox.policy import SandboxPolicy

STOPPED = {JobStatus.FAILED, JobStatus.TIMED_OUT, JobStatus.BUDGET_EXCEEDED, JobStatus.CANCELLED}


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def make_service(tmp_path):
    return JobService(JobStore(str(tmp_path / "jobs.db")))


def new_job(service, tmp_path, **spec):
    workspace = tmp_path / "w"
    workspace.mkdir(exist_ok=True)
    execution = {"provider": "openai", "model": "m", "pipeline": "staged"}
    job = service.create(JobSpec(title="cap", repository=str(workspace), execution=execution, **spec))
    service.store.update_runtime_fields(job.id, worktree=str(workspace))
    return service.get(job.id)


# ------------------------------------------------------------------ amounts and messages
@pytest.mark.parametrize("kind,text,expected", [
    ("cost", "25", 25.0), ("cost", "25usd", 25.0), ("cost", "$12.50", 12.5), ("cost", 7, 7.0),
    ("time", "2h", 7200.0), ("time", "30m", 1800.0), ("time", "90s", 90.0), ("time", "45", 2700.0),
    ("tokens", "5M", 5_000_000.0), ("tokens", "5M tokens", 5_000_000.0), ("tokens", "500k", 500_000.0),
    ("tokens", "1,000,000", 1_000_000.0), ("iterations", "250", 250.0),
])
def test_amounts_parse_in_the_limit_unit(kind, text, expected):
    assert limits.parse_amount(kind, text) == expected


@pytest.mark.parametrize("kind,text", [("cost", "-5"), ("cost", "0"), ("time", "2 weeks"), ("tokens", "lots"),
                                       ("cost", "5h"), ("nope", "1")])
def test_bad_amounts_are_refused(kind, text):
    with pytest.raises(limits.LimitError):
        limits.parse_amount(kind, text)


def test_job_cost_message_is_plain_language():
    detail = limits.limit_detail("cost", "job", "implement", 100.03, 100.0)
    assert detail["message"] == ("Job reached its $100.00 cost limit during implement ($100.03 used). "
                                 "Approve more to continue, or cancel.")
    assert detail["summary"] == "Limit reached: cost $100.03 / $100.00"
    assert {k: detail[k] for k in ("kind", "scope", "stage", "used", "limit", "unit")} == {
        "kind": "cost", "scope": "job", "stage": "implement", "used": 100.03, "limit": 100.0, "unit": "usd"}
    assert detail["suggested_text"] == "100"
    time_detail = limits.limit_detail("time", "stage", "review", 3600, 3600)
    assert "review stage reached its 1h time limit" in time_detail["message"]
    assert time_detail["suggested_text"] == "30m" and time_detail["unit"] == "seconds"


# ------------------------------------------------------------------ relay (cost and tokens)
class Provider:
    model_name = ""

    def stream(self, messages, **kwargs):
        yield StreamEvent(kind="text_delta", text="ok")
        yield StreamEvent(kind="usage", input_tokens=10_000, output_tokens=200, total_tokens=10_200)
        yield StreamEvent(kind="done")


def relay_for(tmp_path, job, policy, stage="implement"):
    art = JobArtifacts("j", str(tmp_path / "art")).ensure()
    obj = ModelRelay(job, stage, {"provider": "anthropic", "model": "claude-sonnet-5-5"}, policy, art,
                     provider_factory=lambda p, m: Provider())
    return obj, art


def turn(obj):
    return list(obj.stream(obj.token, {"messages": wire([Message("user", [MessagePart("text", text="go")])])}))


def test_relay_job_cost_cap_is_recorded_as_a_job_limit(tmp_path):
    job = SimpleNamespace(id="j", max_cost_usd=1.0, cost_usd=0.9995)
    obj, _ = relay_for(tmp_path, job, SandboxPolicy(max_cost_usd=50))
    with pytest.raises(RelayDenied, match="budget exhausted"):
        for _ in range(3):
            turn(obj)
    hit = obj.limit_hit
    assert hit["kind"] == "cost" and hit["scope"] == "job" and hit["limit"] == 1.0 and hit["stage"] == "implement"
    assert hit["used"] == pytest.approx(0.9995 + obj.cost) and hit["used"] >= 1.0
    assert "Job reached its $1.00 cost limit during implement" in hit["message"]


def test_relay_stage_cost_budget_is_recorded_as_a_stage_limit(tmp_path):
    job = SimpleNamespace(id="j", max_cost_usd=None, cost_usd=0)  # no job cap: never a job limit
    obj, art = relay_for(tmp_path, job, SandboxPolicy(max_cost_usd=0.05))
    art.append_cost({"stage": "implement", "cost_usd": 0.05})  # earlier runs used the budget
    obj, _ = relay_for(tmp_path, job, SandboxPolicy(max_cost_usd=0.05))
    with pytest.raises(RelayDenied):
        turn(obj)
    assert obj.limit_hit["scope"] == "stage" and obj.limit_hit["kind"] == "cost"
    assert obj.limit_hit["limit"] == pytest.approx(0.05) and obj.limit_hit["used"] == pytest.approx(0.05)


def test_relay_token_budget_spans_runs_of_the_stage(tmp_path):
    job = SimpleNamespace(id="j", max_cost_usd=None, cost_usd=0)
    obj, art = relay_for(tmp_path, job, SandboxPolicy(max_tokens=15_000))
    turn(obj)
    assert art.stage_tokens()["implement"] == 10_200
    second, _ = relay_for(tmp_path, job, SandboxPolicy(max_tokens=15_000))  # a resumed run
    assert second.policy.max_tokens == 4_800  # what earlier runs left
    turn(second)
    with pytest.raises(RelayDenied, match="Stage token budget exhausted"):
        turn(second)
    assert second.limit_hit["kind"] == "tokens" and second.limit_hit["scope"] == "stage"
    assert second.limit_hit["used"] == 20_400 and second.limit_hit["limit"] == 15_000
    # An approved grant raises the budget the next run sees.
    granted = limits.with_grants(SandboxPolicy(max_tokens=15_000),
                                 SimpleNamespace(metadata={"limit_grants": {"tokens": {"implement": 20_000}}}),
                                 "implement")
    third, _ = relay_for(tmp_path, job, granted)
    turn(third)
    assert third.limit_hit is None


def test_no_cost_cap_never_pauses_for_the_job_cap(tmp_path):
    job = SimpleNamespace(id="j", max_cost_usd=None, cost_usd=10_000.0)
    obj, _ = relay_for(tmp_path, job, SandboxPolicy(max_cost_usd=50))
    turn(obj)
    turn(obj)
    assert obj.limit_hit is None and obj.cost_scope == "stage"


def test_harness_relay_stop_records_the_limit(tmp_path):
    art = JobArtifacts("j1", root=str(tmp_path / "jobs")).ensure()
    relay = HarnessRelay(SimpleNamespace(max_cost_usd=None), "implement", {"provider": "claude", "model": "claude-sonnet-5-5"},
                         SandboxPolicy(max_cost_usd=1.0), art, agent="claude")
    assert relay.record_usage(relay.token, {"total_cost_usd": 1.2, "total_only": True})["stop"]
    assert relay.limit_hit["kind"] == "cost" and relay.limit_hit["scope"] == "stage"
    assert relay.limit_hit["used"] == pytest.approx(1.2)


# ------------------------------------------------------------------ the stage runner pauses
class FakeRelay:
    def __init__(self, scenario):
        self.scenario, self.token, self.cost, self.limit_hit = scenario, "t", 0.0, None
        self.on_error = self.on_warning = None
        self.provider = None

    def start(self, directory):
        return self

    def close(self):
        pass


class Docker:
    """Fake docker CLI: ``start -a`` runs ``behaviour(outputs, stopped)`` in the stage container."""

    def __init__(self, behaviour):
        self.behaviour, self.stopped, self.calls = behaviour, threading.Event(), []

    def run(self, cmd, check=True, timeout=None, **kwargs):
        self.calls.append(cmd)
        if "stop" in cmd:
            self.stopped.set()
        return SimpleNamespace(returncode=0, stdout="", stderr="")


@pytest.fixture
def stage(monkeypatch, tmp_path):
    seen = {}

    def container_args(name, stage_name, worktree, inputs, outputs, relay_dir, policy, token, **kwargs):
        seen.update(inputs=Path(inputs), outputs=Path(outputs), policy=policy)
        return ["create", name]
    monkeypatch.setattr(runner, "stage_environment", lambda service, job, stage: ([], None))
    monkeypatch.setattr(runner, "require_image", lambda *a: "docker")
    monkeypatch.setattr(runner, "check_runtime_image", lambda *a: "")
    monkeypatch.setattr(runner, "container_args", container_args)
    monkeypatch.setattr("mu.jobs.sandbox.engine.attach_docker", lambda policy, *a, **k: (policy, None))
    monkeypatch.setattr(runner, "LIMIT_STOP_GRACE_S", 0.3)

    def run(service, job, docker, behaviour, relay=None, stage_name="plan"):
        relay = relay or FakeRelay("")
        fake = Docker(behaviour)

        def run_with_output(_runner, cmd, timeout=None, output_callback=None):
            inputs = seen["inputs"]
            seen["request"] = json.loads((inputs / "request.json").read_text())
            history = inputs / "history.json"
            seen["history"] = json.loads(history.read_text()) if history.exists() else None
            behaviour(seen["outputs"], fake.stopped, relay)
        monkeypatch.setattr(runner, "run_with_output", run_with_output)
        return runner._run_stage_once(service, job, stage_name, "PLAN-PROMPT-1", runner=fake,
                                      relay_factory=lambda *a, **k: relay), seen
    return run


def write_result(outputs, result, transcript=None):
    (outputs / "transcript.json").write_text(json.dumps(transcript or [{"role": "user", "parts": [{"type": "text", "text": "x"}]}]))
    (outputs / "result.json").write_text(json.dumps(result))


def test_stage_time_limit_pauses_resumably_and_raise_continues_the_same_session(tmp_path, stage, home):
    from mu.jobs.job_settings import save_settings
    save_settings({"sandbox": {"stage_timeout_s": {"plan": 1}}})
    service = make_service(tmp_path)
    job = new_job(service, tmp_path)

    def slow(outputs, stopped, relay):  # runs past the limit until the host stops it (SIGTERM saves state)
        stopped.wait(10)
        write_result(outputs, {"kind": "needs_human", "status": "paused", "error": "stage time limit"},
                     [{"role": "user", "parts": [{"type": "text", "text": "PLAN-PROMPT-1"}]}])
    with pytest.raises(runner.StageLimitReached) as caught:
        stage(service, job, None, slow)
    detail = caught.value.detail
    assert detail["kind"] == "time" and detail["scope"] == "stage" and detail["limit"] == 1
    job = service.get(job.id)
    assert "plan" in job.metadata["stage_resume"] and job.metadata["limit_reached"]["kind"] == "time"
    # The plan worker parks the job on the limit prompt.
    from mu.jobs.worker import _maybe_run_plan_stage
    service.transition(job.id, JobStatus.PREPARING)
    service.transition(job.id, JobStatus.PLANNING)

    def paused_plan(job, prompt, workspace, model):
        raise caught.value
    _maybe_run_plan_stage(service, job.id, generate_fn=paused_plan)
    job = service.get(job.id)
    assert job.status == JobStatus.NEEDS_HUMAN and job.attention_reason == AttentionReason.LIMIT_REACHED
    assert service.store.core_state(job.id).paused_from == JobState.PLANNING
    raise_action = commands.facts_for_job(service, job)
    assert raise_action.limit_reached["kind"] == "time"
    result = commands.dispatch(service, {"type": "raiseLimit", "job_id": job.id, "args": {"amount": "30m"}})
    assert result.data["resumed"] and result.data["limit"]["new"] == 1 + 1800
    job = service.get(job.id)
    assert job.status == JobStatus.PLANNING and "limit_reached" not in job.metadata
    assert job.metadata["limit_grants"]["time"]["plan"] == 1800
    raised = [e for e in service.events(job.id) if e.event_type == "limit_raised"]
    assert raised and raised[-1].payload["old"] == 1 and raised[-1].payload["by"] == "human"

    # The resumed run continues the same conversation, with the approved extra time.
    def finish(outputs, stopped, relay):
        write_result(outputs, {"kind": "completed", "status": "completed", "text": "{}"})
    result, seen = stage(service, job, None, finish)
    request = seen["request"]
    assert request["resume"] is True and "Continue this stage" in request["prompt"]
    assert seen["history"][0]["parts"][0]["text"] == "PLAN-PROMPT-1"
    assert seen["policy"].timeout == 1801 and result["kind"] == "completed"


def test_relay_cost_limit_pauses_the_stage_with_its_cost(tmp_path, stage):
    service = make_service(tmp_path)
    job = new_job(service, tmp_path)
    relay = FakeRelay("cost")

    def refused(outputs, stopped, relay):
        relay.cost = 1.75
        relay.limit_hit = limits.limit_detail("cost", "job", "plan", 100.03, 100.0)
        write_result(outputs, {"kind": "failed", "status": "error", "error": "Stage cost budget exhausted"})
    with pytest.raises(runner.StageLimitReached) as caught:
        stage(service, job, None, refused, relay=relay)
    assert caught.value.cost_usd == pytest.approx(1.75)
    assert caught.value.detail["scope"] == "job"
    assert "plan" in service.get(job.id).metadata["stage_resume"]


def test_lingering_stage_is_stopped_gracefully_after_a_refusal(tmp_path, stage):
    service = make_service(tmp_path)
    job = new_job(service, tmp_path)
    relay = FakeRelay("tokens")

    def lingers(outputs, stopped, relay):
        relay.limit_hit = limits.limit_detail("tokens", "stage", "plan", 50_000_000, 50_000_000)
        stopped.wait(10)  # keeps going until the host's graceful stop
        write_result(outputs, {"kind": "needs_human", "status": "paused", "error": "stage time limit"})
    with pytest.raises(runner.StageLimitReached) as caught:
        stage(service, job, None, lingers, relay=relay)
    assert caught.value.detail["kind"] == "tokens"


def test_iteration_limit_pauses_and_a_grant_raises_the_next_runs_limit(tmp_path, stage):
    service = make_service(tmp_path)
    job = new_job(service, tmp_path, max_iterations=40)

    def capped(outputs, stopped, relay):
        write_result(outputs, {"kind": "needs_human", "status": "max_iterations_reached", "error": "Reached maximum"})
    with pytest.raises(runner.StageLimitReached) as caught:
        stage(service, job, None, capped)
    assert caught.value.detail == {**caught.value.detail, "kind": "iterations", "scope": "stage", "used": 40, "limit": 40}
    service.store.patch_metadata(job.id, {"limit_grants": {"iterations": {"plan": 60}}})

    def finish(outputs, stopped, relay):
        write_result(outputs, {"kind": "completed", "status": "completed"})
    _, seen = stage(service, service.get(job.id), None, finish)
    assert seen["request"]["max_iterations"] == 100


def test_job_runtime_caps_the_stage_and_pauses_at_the_job_limit(tmp_path, stage):
    service = make_service(tmp_path)
    job = new_job(service, tmp_path, max_runtime_seconds=2)
    service.transition(job.id, JobStatus.PREPARING)
    service.transition(job.id, JobStatus.PLANNING)

    def slow(outputs, stopped, relay):
        stopped.wait(10)
        write_result(outputs, {"kind": "needs_human", "status": "paused", "error": "stage time limit"})
    with pytest.raises(runner.StageLimitReached) as caught:
        stage(service, service.get(job.id), None, slow)
    detail = caught.value.detail
    assert detail["kind"] == "time" and detail["scope"] == "job" and detail["limit"] == 2
    # Already over the limit: the next stage pauses before it starts (no container).
    with pytest.raises(runner.StageLimitReached):
        stage(service, service.get(job.id), None, lambda *a: pytest.fail("no container past the limit"))


def test_controller_pause_request_stops_the_stage_gracefully(tmp_path, stage):
    service = make_service(tmp_path)
    job = new_job(service, tmp_path, max_runtime_seconds=3600)

    def runs(outputs, stopped, relay):
        service.store.append_event(job.id, "stage_pause_requested", payload={"reason": "job_runtime"})
        stopped.wait(10)
        write_result(outputs, {"kind": "needs_human", "status": "paused", "error": "stage time limit"})
    with pytest.raises(runner.StageLimitReached) as caught:
        stage(service, job, None, runs)
    assert caught.value.detail["kind"] == "time" and caught.value.detail["scope"] == "job"
    assert "plan" in service.get(job.id).metadata["stage_resume"]


def test_raise_job_runtime_resumes_the_paused_implement_stage(tmp_path):
    service = make_service(tmp_path)
    job = new_job(service, tmp_path, max_runtime_seconds=600)
    service.transition(job.id, JobStatus.PREPARING)
    service.transition(job.id, JobStatus.RUNNING)
    detail = limits.limit_detail("time", "job", "implement", 600, 600)
    limits.record_pause(service, job.id, detail)
    service.require_human(job.id, AttentionReason.LIMIT_REACHED, detail["message"], payload={"limit": detail})
    vm = commands.facts_for_job(service, service.get(job.id))
    assert vm.limit_reached["scope"] == "job"
    result = commands.dispatch(service, {"type": "raiseLimit", "job_id": job.id,
                                         "args": {"kind": "time", "scope": "job", "amount": "1h"}})
    job = service.get(job.id)
    assert result.data["resumed"] and job.max_runtime_seconds == 4200 and job.status == JobStatus.QUEUED
    assert service.store.core_state(job.id).state == JobState.IMPLEMENTING


# ------------------------------------------------------------------ implement / review outcomes
def _limit_error(detail, cost):
    exc = runner.StageLimitReached(detail)
    exc.cost_usd = cost
    return exc


def test_implement_limit_becomes_needs_human_with_detail_and_cost(tmp_path, monkeypatch):
    from mu.jobs.worker import _apply_outcome
    from mu.jobs.worktree import JobWorktreeManager
    service = make_service(tmp_path)
    job = new_job(service, tmp_path)
    assert service.acquire(job.id, "w")
    service.transition(job.id, JobStatus.PREPARING)
    attempt = service.start_attempt(job.id, worker_id="w")
    service.transition(job.id, JobStatus.RUNNING)
    detail = limits.limit_detail("cost", "job", "implement", 100.03, 100.0)
    monkeypatch.setattr(runner, "implement_prompt", lambda service, job: "p")
    monkeypatch.setattr(runner, "run_stage_in_container", lambda *a, **k: (_ for _ in ()).throw(_limit_error(detail, 3.5)))
    outcome = runner.SandboxJobRunner(service).run(service.get(job.id), attempt)
    assert outcome.kind == "needs_human" and outcome.attention_reason == AttentionReason.LIMIT_REACHED
    assert outcome.cost_usd == 3.5 and outcome.attention_payload["limit"] == detail
    _apply_outcome(service, JobWorktreeManager(service), job.id, attempt.id, attempt.number, outcome, worker_id="w")
    job = service.get(job.id)
    assert job.status == JobStatus.NEEDS_HUMAN and job.status not in STOPPED
    assert job.attention_detail.startswith("Job reached its $100.00 cost limit during implement ($100.03 used)")
    assert job.cost_usd == pytest.approx(3.5)
    gate = [e for e in service.events(job.id) if e.event_type == "status_changed"][-1]
    assert gate.payload["limit"]["kind"] == "cost"


def test_review_limit_parks_on_limit_reached_and_cancel_still_works(tmp_path):
    from mu.jobs.review_worker import _review
    service = make_service(tmp_path)
    job = new_job(service, tmp_path)
    for status in (JobStatus.PREPARING, JobStatus.RUNNING, JobStatus.VERIFYING, JobStatus.REVIEWING):
        service.transition(job.id, status)
    detail = limits.limit_detail("tokens", "stage", "review", 50_000_100, 50_000_000)

    def capped(job, prompt, workspace, model):
        artifacts_for(job).append_cost({"stage": "review", "cost_usd": 0.4})
        limits.record_pause(service, job.id, detail)
        raise _limit_error(detail, 0.4)
    _review(service, service.get(job.id), SimpleNamespace(owned=lambda: True), generate_fn=capped)
    job = service.get(job.id)
    assert job.status == JobStatus.NEEDS_HUMAN and job.attention_reason == AttentionReason.LIMIT_REACHED
    assert job.cost_usd == pytest.approx(0.4)
    actions = {a["id"]: a for a in __import__("mu.jobs.core.vm", fromlist=["x"]).job_vm(service, job.id, parts=["state"])["actions"]}
    assert actions["raiseLimit"]["primary"] and actions["raiseLimit"]["enabled"] and not actions["raiseLimit"]["hidden"]
    assert actions["raiseLimit"]["input"]["arg"] == "amount" and actions["raiseLimit"]["input"]["value"] == "50M"
    assert actions["raiseLimit"]["args"] == {"kind": "tokens", "scope": "stage", "stage": "review", "amount": "50M"}
    assert actions["discard"]["label"] == "Cancel job" and not actions["resume"]["enabled"]
    commands.dispatch(service, {"type": "discard", "job_id": job.id, "args": {}})
    assert service.get(job.id).status == JobStatus.CANCELLED


def test_raising_a_stage_budget_resumes_review_and_never_touches_settings(tmp_path):
    from mu.jobs.job_settings import load_settings
    service = make_service(tmp_path)
    job = new_job(service, tmp_path)
    for status in (JobStatus.PREPARING, JobStatus.RUNNING, JobStatus.VERIFYING, JobStatus.REVIEWING):
        service.transition(job.id, status)
    detail = limits.limit_detail("cost", "stage", "review", 50.2, 50.0)
    limits.record_pause(service, job.id, detail)
    service.require_human(job.id, AttentionReason.LIMIT_REACHED, detail["message"], payload={"limit": detail})
    before = load_settings()
    with pytest.raises(commands.CommandError):
        commands.dispatch(service, {"type": "raiseLimit", "job_id": job.id, "args": {"amount": "-3"}})
    commands.dispatch(service, {"type": "raiseLimit", "job_id": job.id, "args": {"amount": "25usd"}})
    job = service.get(job.id)
    assert job.status == JobStatus.REVIEWING and job.metadata["limit_grants"] == {"cost": {"review": 25.0}}
    assert limits.with_grants(SandboxPolicy(max_cost_usd=50), job, "review").max_cost_usd == 75.0
    assert load_settings() == before


def test_raise_ahead_of_time_on_a_queued_job(tmp_path):
    service = make_service(tmp_path)
    job = new_job(service, tmp_path, max_cost_usd=100.0)
    actions = {a["id"]: a for a in __import__("mu.jobs.core.vm", fromlist=["x"]).job_vm(service, job.id, parts=["state"])["actions"]}
    assert actions["raiseLimit"]["enabled"] and actions["raiseLimit"]["hidden"]
    result = commands.dispatch(service, {"type": "raiseLimit", "job_id": job.id, "args": {"kind": "cost", "amount": 50}})
    assert not result.data["resumed"] and service.get(job.id).max_cost_usd == 150.0
    assert service.get(job.id).status == JobStatus.QUEUED
    with pytest.raises(commands.CommandError, match="no time limit"):
        commands.dispatch(service, {"type": "raiseLimit", "job_id": job.id,
                                    "args": {"kind": "time", "scope": "job", "amount": "1h"}})


def test_home_needs_you_shows_the_limit_summary(tmp_path):
    from mu.jobs.core.vm import home_vm
    service = make_service(tmp_path)
    job = new_job(service, tmp_path)
    service.transition(job.id, JobStatus.PREPARING)
    service.transition(job.id, JobStatus.RUNNING)
    detail = limits.limit_detail("cost", "job", "implement", 100.03, 100.0)
    limits.record_pause(service, job.id, detail)
    service.require_human(job.id, AttentionReason.LIMIT_REACHED, detail["message"], payload={"limit": detail})
    row = next(r for r in home_vm(service)["needs_you"] if r["id"] == job.id)
    assert row["primary_action"] == "raiseLimit" and row["attention_summary"] == "Limit reached: cost $100.03 / $100.00"
    assert row["limit"]["kind"] == "cost"


def test_tracker_writeback_mentions_the_limit(tmp_path):
    from mu.jobs.tracker_sync import _message, state_event
    detail = limits.limit_detail("cost", "job", "implement", 100.03, 100.0)
    job = SimpleNamespace(id="abcdef123456", title="t", metadata={}, attention_detail=detail["message"])
    event, info = state_event(job, "needs_input")
    assert event == "needs_human" and "$100.00 cost limit" in _message(job, event, info)


def test_terminal_raise_limit_parses_kind_amount_and_flags():
    from mu.commands.job import _raise_limit_args
    assert _raise_limit_args("cost 25usd") == {"kind": "cost", "amount": "25usd"}
    assert _raise_limit_args("tokens 5M tokens") == {"kind": "tokens", "amount": "5M tokens"}
    assert _raise_limit_args("time 2h --scope stage --stage review") == {
        "kind": "time", "amount": "2h", "scope": "stage", "stage": "review"}
    with pytest.raises(ValueError, match="usage"):
        _raise_limit_args("cost")
