"""Milestone 6: failure-injection suite.

Each scenario injects one deliberate failure from the design doc's Friday
Five list and asserts the *truthful, actionable* outcome invariants:

  * no false READY (READY_FOR_REVIEW always has verified evidence + a branch);
  * every blocker is surfaced (attention state + reason + push event);
  * cost attribution survives (attempt cost lands on the job exactly once);
  * every history shrink / lease loss / retry leaves an event trail.

Scenarios run the real store/service/controller/worker-outcome code with a
fake process factory and an injectable clock — no provider, no subprocess.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from mu.jobs import AttentionReason, JobService, JobSpec, JobStatus, JobStore
from mu.jobs.base_drift import apply_base_drift
from mu.jobs.controller import JobController
from mu.jobs.review_branch import materialize_review_branch
from mu.jobs.runner import JobRunOutcome
from mu.jobs.ui import JobUI
from mu.jobs.worker import _apply_outcome
from mu.jobs.worktree import JobWorktreeManager


class Clock:
    def __init__(self, now=1_800_000_000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeProcess:
    def __init__(self, pid):
        self.pid = pid
        self.returncode = None
        self.signals = []

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        self.signals.append("TERM")

    def kill(self):
        self.signals.append("KILL")
        self.returncode = -9


class Factory:
    def __init__(self):
        self.processes = []
        self.next_pid = 5000

    def __call__(self, command, **kwargs):
        proc = FakeProcess(self.next_pid)
        self.next_pid += 1
        proc.command = list(command)
        self.processes.append(proc)
        return proc


def git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args], text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=True,
    ).stdout.strip()


@pytest.fixture
def world(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "app.txt").write_text("base\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    clock = Clock()
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3"), clock=clock))
    manager = JobWorktreeManager(service, root=str(tmp_path / "worktrees"))
    events = []
    factory = Factory()
    ctl = JobController(
        service, max_workers=5, process_factory=factory, project_root=str(tmp_path),
        python_executable="python-test", notify=events.append, lease_ttl_seconds=30,
        drift_check_interval_seconds=0,
    )
    return SimpleNamespace(
        tmp=tmp_path, repo=repo, clock=clock, service=service, manager=manager,
        factory=factory, ctl=ctl, events=events,
    )


def submit(w, title="Ticket", **spec):
    return w.service.create(JobSpec(
        title=title, repository=str(w.repo), validation_commands=["true"], max_retries=2,
        execution={"provider": "openai", "model": "test", "session_type": "workspace"},
        **spec,
    ))


def worker_of(w, job_id):
    """Simulate the leased worker's PREPARING->RUNNING + attempt start."""
    job = w.service.get(job_id)
    worker_id = job.worker_id
    assert worker_id, "job must be leased by the controller"
    w.manager.prepare(job)
    w.service.transition(job_id, JobStatus.PREPARING)
    attempt = w.service.start_attempt(job_id, worker_id=worker_id, session_name="s")
    w.service.transition(job_id, JobStatus.RUNNING)
    return worker_id, attempt


def implement(w, job_id, text="implemented\n"):
    job = w.service.get(job_id)
    (w.tmp / "worktrees" / job_id / "app.txt").write_text(text, encoding="utf-8")
    return w.manager.checkpoint(job, label="implementation")


def _attention_ids(w):
    return {j["id"] for j in w.service.attention_summary()["jobs"]}


# ------------------------------------------------------------------ scenario 1: disconnect


def test_browser_disconnect_is_a_noop_for_execution(world):
    """Nothing in the job plane references a client connection: leasing,
    running, and finishing proceed with zero observers."""
    w = world
    job = submit(w)
    assert w.ctl.tick() == 1
    worker_id, attempt = worker_of(w, job.id)
    implement(w, job.id)
    code = _apply_outcome(
        w.service, w.manager, job.id, attempt.id, attempt.number,
        JobRunOutcome(kind="completed", status="completed", cost_usd=0.4), worker_id=worker_id,
    )
    assert code == 0
    current = w.service.get(job.id)
    assert current.status == JobStatus.VERIFYING
    assert current.cost_usd == pytest.approx(0.4)


# ------------------------------------------------------------------ scenario 2: worker crash mid-run


def test_worker_killed_midrun_recovers_without_duplicate_owner(world):
    w = world
    job = submit(w)
    w.ctl.tick()
    worker_id, attempt = worker_of(w, job.id)
    # Worker process dies: no heartbeat, lease expires.
    w.factory.processes[0].returncode = -9
    w.clock.advance(31)
    w.ctl.tick()
    current = w.service.get(job.id)
    assert current.status in {JobStatus.RECOVERING, JobStatus.PREPARING}
    kinds = [e.event_type for e in w.service.events(job.id)]
    assert "worker_process_exited" in kinds
    assert any(e.reason == "implementation worker lease expired" for e in w.service.events(job.id))
    # Exactly one new lease holder; the dead worker's stale outcome is rejected.
    assert current.worker_id and current.worker_id != worker_id
    stale = _apply_outcome(
        w.service, w.manager, job.id, attempt.id, attempt.number,
        JobRunOutcome(kind="completed", status="completed", cost_usd=9.99), worker_id=worker_id,
    )
    assert stale == 5
    assert w.service.get(job.id).cost_usd == 0.0  # zombie cost never attributed


# ------------------------------------------------------------------ scenario 3: controller restart


def test_controller_restart_resumes_queue_and_reannounces_attention(world):
    w = world
    blocked = submit(w, "blocked")
    queued = submit(w, "queued")
    w.ctl.tick()
    # Park `blocked` on a human, then "restart" the daemon with a new controller.
    worker_id, attempt = worker_of(w, blocked.id)
    _apply_outcome(
        w.service, w.manager, blocked.id, attempt.id, attempt.number,
        JobRunOutcome(kind="needs_human", status="needs_human", attention_reason=AttentionReason.QUESTION,
                      attention_detail="Which API?", attention_payload={"shape": "input"}),
        worker_id=worker_id,
    )
    w.service.store.release_lease(blocked.id, worker_id)
    events2 = []
    ctl2 = JobController(
        w.service, max_workers=5, process_factory=w.factory, project_root=str(w.tmp),
        python_executable="python-test", notify=events2.append, drift_check_interval_seconds=0,
    )
    ctl2.start()
    ctl2.stop(wait=True)
    ctl2.tick()
    assert events2 and events2[0]["count"] == 1 and events2[0]["entered"][0]["id"] == blocked.id
    assert w.service.get(queued.id).worker_id  # queue kept moving


# ------------------------------------------------------------------ scenario 4: provider failure


def test_transient_provider_failure_retries_then_reports_truthfully(world):
    w = world
    job = submit(w)
    w.ctl.tick()
    worker_id, attempt = worker_of(w, job.id)
    for number in (1, 2):
        code = _apply_outcome(
            w.service, w.manager, job.id, attempt.id, attempt.number,
            JobRunOutcome(kind="failed", status="error", error=f"429 rate limit #{number}", cost_usd=0.1, transient=True),
            worker_id=worker_id,
        )
        assert code == 10
        current = w.service.get(job.id)
        assert current.status == JobStatus.QUEUED and not current.needs_attention
        w.service.store.release_lease(job.id, worker_id)
        w.factory.processes[-1].returncode = 10  # worker process exits with the requeue code
        assert w.ctl.tick() == 0  # backoff honoured
        w.clock.advance(current.metadata["next_run_at"] - w.clock.now + 1)
        assert w.ctl.tick() == 1
        worker_id, attempt = worker_of(w, job.id)
    # Third attempt (> max_retries=2) fails terminally with the real error.
    code = _apply_outcome(
        w.service, w.manager, job.id, attempt.id, attempt.number,
        JobRunOutcome(kind="failed", status="error", error="429 rate limit #3", cost_usd=0.1, transient=True),
        worker_id=worker_id,
    )
    assert code == 1
    final = w.service.get(job.id)
    assert final.status == JobStatus.FAILED
    assert final.cost_usd == pytest.approx(0.3)  # every attempt attributed once
    attempts = w.service.attempts(job.id)
    assert len(attempts) == 3 and attempts[-1].metadata["retry_budget_exhausted"] is True
    assert "429 rate limit #3" in attempts[-1].error
    # Never asked a human to "choose recovery strategy".
    assert not any(e.to_status == JobStatus.NEEDS_HUMAN for e in w.service.events(job.id))


def test_non_transient_failure_is_terminal_immediately(world):
    w = world
    job = submit(w)
    w.ctl.tick()
    worker_id, attempt = worker_of(w, job.id)
    _apply_outcome(
        w.service, w.manager, job.id, attempt.id, attempt.number,
        JobRunOutcome(kind="failed", status="error", error="AssertionError in agent", transient=False),
        worker_id=worker_id,
    )
    assert w.service.get(job.id).status == JobStatus.FAILED
    assert len(w.service.attempts(job.id)) == 1


# ------------------------------------------------------------------ scenario 5: base branch moves


def _to_ready(w, job_id):
    worker_id, attempt = worker_of(w, job_id)
    implement(w, job_id)
    _apply_outcome(
        w.service, w.manager, job_id, attempt.id, attempt.number,
        JobRunOutcome(kind="completed", status="completed", cost_usd=0.2), worker_id=worker_id,
    )
    materialize_review_branch(w.service, job_id)
    w.service.transition(job_id, JobStatus.READY_FOR_REVIEW)
    w.service.store.release_lease(job_id, worker_id)


def test_base_branch_movement_never_leaves_false_ready(world):
    w = world
    job = submit(w)
    w.ctl.tick()
    _to_ready(w, job.id)
    (w.repo / "app.txt").write_text("main diverged\n", encoding="utf-8")
    git(w.repo, "add", ".")
    git(w.repo, "commit", "-m", "main moves")
    report = apply_base_drift(w.service, job.id, source="controller")
    assert report.mergeable is False
    current = w.service.get(job.id)
    assert current.status == JobStatus.CONFLICTED
    assert current.attention_reason == AttentionReason.MERGE_CONFLICT
    w.ctl.tick()
    assert w.events and w.events[-1]["entered"][0]["id"] == job.id
    assert job.id in _attention_ids(w)


# ------------------------------------------------------------------ scenario 6: late human answer on chatty job


def test_human_answer_after_thousands_of_events_is_consumed(world):
    w = world
    job = submit(w)
    w.ctl.tick()
    worker_id, attempt = worker_of(w, job.id)
    for i in range(1500):
        w.service.store.append_event(job.id, "tool_call_ui", payload={"tool_name": f"t{i}"})
    _apply_outcome(
        w.service, w.manager, job.id, attempt.id, attempt.number,
        JobRunOutcome(kind="needs_human", status="needs_human", attention_reason=AttentionReason.QUESTION,
                      attention_detail="Pick", attention_payload={"shape": "choices", "choices": ["a", "b"]}),
        worker_id=worker_id,
    )
    w.service.store.append_event(job.id, "interaction_response", payload={"kind": "question", "selected": ["b"]})
    w.service.resume(job.id, detail="b")
    ui = JobUI(w.service, job.id)
    assert ui.prompt_choices("Pick", ["a", "b"]) == "b"


# ------------------------------------------------------------------ scenario 7: runtime deadline


def test_runtime_deadline_times_out_and_escalates_kill(world, monkeypatch):
    w = world
    job = submit(w, max_runtime_seconds=60)
    w.ctl.tick()
    worker_id, attempt = worker_of(w, job.id)
    proc = w.factory.processes[0]
    wall = [w.clock.now + 61]
    monkeypatch.setattr("mu.jobs.controller.time.time", lambda: wall[0])
    w.ctl.tick()
    current = w.service.get(job.id)
    assert current.status == JobStatus.TIMED_OUT
    assert "TERM" in proc.signals
    assert any(e.event_type == "worker_runtime_deadline_exceeded" for e in w.service.events(job.id))
    # Still retryable by a human, never silently dropped.
    assert w.service.retry(job.id).status == JobStatus.QUEUED


# ------------------------------------------------------------------ invariant sweep


def test_friday_five_outcome_invariants(world):
    """Five tickets, five injected fates; every one is understandable from
    the receipt/board alone: status + reason + cost + branch."""
    from mu.jobs.receipt import JobReceiptBuilder

    w = world
    ids = [submit(w, f"T{i}").id for i in range(5)]
    w.ctl.tick()
    fates = {}
    # T0 completes -> VERIFYING
    wid, att = worker_of(w, ids[0]); implement(w, ids[0])
    _apply_outcome(w.service, w.manager, ids[0], att.id, att.number, JobRunOutcome(kind="completed", status="completed", cost_usd=0.5), worker_id=wid)
    fates[ids[0]] = JobStatus.VERIFYING
    # T1 needs a human
    wid, att = worker_of(w, ids[1])
    _apply_outcome(w.service, w.manager, ids[1], att.id, att.number, JobRunOutcome(kind="needs_human", status="needs_human", attention_reason=AttentionReason.AMBIGUOUS_REQUIREMENT, attention_detail="Which schema?", cost_usd=0.1), worker_id=wid)
    fates[ids[1]] = JobStatus.NEEDS_HUMAN
    # T2 transient failure -> queued with backoff
    wid, att = worker_of(w, ids[2])
    _apply_outcome(w.service, w.manager, ids[2], att.id, att.number, JobRunOutcome(kind="failed", status="error", error="503", cost_usd=0.05, transient=True), worker_id=wid)
    fates[ids[2]] = JobStatus.QUEUED
    # T3 hard failure
    wid, att = worker_of(w, ids[3])
    _apply_outcome(w.service, w.manager, ids[3], att.id, att.number, JobRunOutcome(kind="failed", status="error", error="Traceback: KeyError", cost_usd=0.2), worker_id=wid)
    fates[ids[3]] = JobStatus.FAILED
    # T4 cancelled by user while running
    wid, att = worker_of(w, ids[4])
    w.service.cancel(ids[4])
    fates[ids[4]] = JobStatus.CANCELLED

    total_cost = 0.0
    for job_id, expected in fates.items():
        job = w.service.get(job_id)
        assert job.status == expected, (job.title, job.status)
        receipt = JobReceiptBuilder(w.service).build(job_id)
        assert receipt["job"]["status"] == expected.value
        assert receipt["outcome"]["ready_for_review"] is False  # nobody is falsely ready
        if job.needs_attention:
            assert job.attention_detail  # blocker is explained
        if expected == JobStatus.FAILED:
            assert receipt["attempts"][-1]["error"]
        total_cost += job.cost_usd
    assert total_cost == pytest.approx(0.85)  # 100% attribution, nothing double counted
    w.ctl.tick()
    assert w.service.attention_summary()["count"] == 1
    assert w.events[-1]["count"] == 1
