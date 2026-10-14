"""Core state machine, the one status/core transition table, legacy migration, pipeline, actions."""

import pytest

from mu.jobs.core.actions import JobFacts, action_map, check_allowed
from mu.jobs.core.pipeline import derive_pipeline, mini_indicator
from mu.jobs.core import states as core_states
from mu.jobs.core.states import (
    CoreState, JobState as S, TransitionError, advance, effective_state, from_legacy,
    to_legacy_status, transition,
)
from mu.jobs.models import AttentionReason, JobSpec, JobStatus
from mu.jobs.service import JobService
from mu.jobs.store import JobStore


# --------------------------------------------------------------- legacy map
LEGACY_TABLE = [
    ("queued", "", S.PROVISIONING),
    ("preparing", "", S.PROVISIONING),
    ("planning", "", S.PLANNING),
    ("needs_human", "approval_required", S.PLAN_REVIEW),
    ("needs_human", "plan_review", S.PLAN_REVIEW),
    ("running", "", S.IMPLEMENTING),
    ("recovering", "", S.IMPLEMENTING),
    ("verifying", "", S.CHECKING),
    ("reviewing", "", S.REVIEWING),
    ("needs_human", "review_stalled", S.REVIEW_DONE),
    ("publishing", "", S.PUSHING),
    ("ready_for_review", "", S.PUSHED),
    ("merged", "", S.DONE),
    ("needs_human", "question", S.NEEDS_INPUT),
    ("conflicted", "", S.NEEDS_INPUT),
    ("failed", "", S.FAILED),
    ("timed_out", "", S.FAILED),
    ("budget_exceeded", "", S.FAILED),
    ("environment_error", "", S.FAILED),
    ("cancelled", "", S.CANCELLED),
]


@pytest.mark.parametrize("status,reason,expected", LEGACY_TABLE)
def test_legacy_status_maps_to_core_state(status, reason, expected):
    assert from_legacy(status, reason).state == expected


def test_every_legacy_status_and_attention_pair_maps():
    for status in JobStatus:
        for reason in AttentionReason:
            core = from_legacy(status.value, reason.value)
            assert isinstance(core.state, S)


def test_legacy_pause_and_failure_origin_from_history():
    paused = from_legacy("needs_human", "question", history=["verifying", "running"])
    assert paused.state == S.NEEDS_INPUT and paused.paused_from == S.CHECKING
    failed = from_legacy("failed", "", history=["needs_human", "reviewing"])
    assert failed.state == S.FAILED and failed.failed_from == S.REVIEWING


def test_core_state_round_trips_to_a_legacy_status():
    for state in S:
        assert to_legacy_status(CoreState(state)) in {s.value for s in JobStatus}


# --------------------------------------------------------------- transitions
def test_happy_path_transitions():
    c = CoreState(S.SELECTED)
    for target in (S.PROVISIONING, S.PLANNING, S.PLAN_REVIEW, S.IMPLEMENTING, S.CHECKING,
                   S.REVIEWING, S.REVIEW_DONE, S.PUSHING, S.PUSHED, S.DONE):
        c = transition(c, target)
    assert c.state == S.DONE


def test_review_never_routes_back_to_implementing_on_its_own():
    with pytest.raises(TransitionError):
        transition(CoreState(S.REVIEWING), S.IMPLEMENTING)


def test_fix_loop_counts_and_respects_limit():
    c = transition(CoreState(S.CHECKING), S.IMPLEMENTING, max_fix_attempts=2)
    assert c.fix_attempts == 1
    c = transition(transition(c, S.CHECKING), S.IMPLEMENTING, max_fix_attempts=2)
    assert c.fix_attempts == 2
    with pytest.raises(TransitionError):
        transition(transition(c, S.CHECKING), S.IMPLEMENTING, max_fix_attempts=2)


def test_reprompt_resets_fix_budget():
    c = CoreState(S.REVIEW_DONE, fix_attempts=2)
    assert transition(c, S.IMPLEMENTING).fix_attempts == 0


def test_pause_resume_and_fail_retry_targets():
    c = transition(CoreState(S.IMPLEMENTING), S.NEEDS_INPUT)
    assert c.paused_from == S.IMPLEMENTING and effective_state(c) == S.IMPLEMENTING
    with pytest.raises(TransitionError):
        transition(c, S.CHECKING)
    assert transition(c, S.IMPLEMENTING).state == S.IMPLEMENTING
    f = transition(CoreState(S.CHECKING), S.FAILED)
    assert f.failed_from == S.CHECKING
    assert transition(f, S.CHECKING).state == S.CHECKING


def test_terminal_states_reject_transitions():
    for state in (S.DONE, S.CANCELLED):
        with pytest.raises(TransitionError):
            transition(CoreState(state), S.CANCELLED)


# --------------------------------------------------------------- pipeline
@pytest.mark.parametrize("core,expected", [
    (CoreState(S.PLANNING), "●─○─○─○─○─○"),
    (CoreState(S.PLAN_REVIEW), "✓─◆─○─○─○─○"),
    (CoreState(S.IMPLEMENTING), "✓─✓─●─○─○─○"),
    (CoreState(S.CHECKING), "✓─✓─✓─●─○─○"),
    (CoreState(S.REVIEW_DONE), "✓─✓─✓─✓─◆─○"),
    (CoreState(S.PUSHED), "✓─✓─✓─✓─✓─✓"),  # push finished; merge is outside the pipeline
    (CoreState(S.DONE), "✓─✓─✓─✓─✓─✓"),
    (CoreState(S.NEEDS_INPUT, paused_from=S.IMPLEMENTING), "✓─✓─◆─○─○─○"),
    (CoreState(S.FAILED, failed_from=S.CHECKING), "✓─✓─✓─✗─○─○"),
])
def test_pipeline_golden(core, expected):
    assert mini_indicator(derive_pipeline(core)) == expected


def test_pipeline_skips_approve_without_plan_gate():
    steps = {s.id: s for s in derive_pipeline(CoreState(S.IMPLEMENTING), plan_approval=False)}
    assert steps["approve"].status == "skipped"


def test_pipeline_fix_attempt_detail():
    steps = {s.id: s for s in derive_pipeline(CoreState(S.IMPLEMENTING, fix_attempts=2))}
    assert steps["implement"].detail == "2 fix attempts"


# --------------------------------------------------------------- actions
def _enabled(core, **kw):
    return {k for k, v in action_map(JobFacts(core=core, **kw)).items() if v.enabled}


def test_actions_match_current_ui_rules():
    assert "retry" in _enabled(CoreState(S.FAILED, failed_from=S.IMPLEMENTING))
    assert "retry" not in _enabled(CoreState(S.IMPLEMENTING))
    assert "approvePlan" in _enabled(CoreState(S.PLAN_REVIEW), has_plan=True, plan_version=1)
    assert "approvePlan" not in _enabled(CoreState(S.IMPLEMENTING), has_plan=True)
    assert "resume" in _enabled(CoreState(S.NEEDS_INPUT, paused_from=S.IMPLEMENTING))
    assert "markMerged" in _enabled(CoreState(S.PUSHED))
    assert "markMerged" not in _enabled(CoreState(S.REVIEW_DONE))
    assert "delete" in _enabled(CoreState(S.DONE))
    assert "discard" not in _enabled(CoreState(S.CANCELLED))


def test_disabled_actions_carry_reason():
    vm = action_map(JobFacts(core=CoreState(S.IMPLEMENTING)))["retry"]
    assert not vm.enabled and vm.reason
    assert check_allowed(JobFacts(core=CoreState(S.IMPLEMENTING)), "retry")
    assert check_allowed(JobFacts(core=CoreState(S.IMPLEMENTING)), "nope")


# --------------------------------------------------------------- store
def test_store_tracks_core_state_through_legacy_transitions(tmp_path):
    store = JobStore(str(tmp_path / "jobs.sqlite3"))
    store.initialize()
    job = store.create_job(JobSpec(title="t"))
    assert store.core_state(job.id).state == S.PROVISIONING
    store.transition(job.id, JobStatus.PREPARING)
    store.transition(job.id, JobStatus.RUNNING)
    store.transition(job.id, JobStatus.VERIFYING)
    store.transition(job.id, JobStatus.FAILED)
    core = store.core_state(job.id)
    assert core.state == S.FAILED and core.failed_from == S.CHECKING


def test_store_backfills_rows_from_before_v3(tmp_path):
    path = str(tmp_path / "jobs.sqlite3")
    store = JobStore(path)
    store.initialize()
    job = store.create_job(JobSpec(title="t"))
    with store._transaction() as conn:
        conn.execute("UPDATE jobs SET state='', status='needs_human', attention_reason='plan_review'")
    JobStore(path).initialize()
    assert JobStore(path).core_state(job.id).state == S.PLAN_REVIEW


# --------------------------------------------------------------- one transition table

# Canonical core state of a job sitting in each stored status.
CANONICAL_PRIOR = {
    "queued": CoreState(S.PROVISIONING),
    "preparing": CoreState(S.PROVISIONING),
    "running": CoreState(S.IMPLEMENTING, fix_attempts=1),
    "needs_human": CoreState(S.NEEDS_INPUT, paused_from=S.CHECKING, fix_attempts=1),
    "verifying": CoreState(S.CHECKING, fix_attempts=1),
    "planning": CoreState(S.PLANNING),
    "reviewing": CoreState(S.REVIEWING, fix_attempts=1),
    "publishing": CoreState(S.PUSHING, fix_attempts=1),
    "ready_for_review": CoreState(S.PUSHED, fix_attempts=1),
    "recovering": CoreState(S.IMPLEMENTING, fix_attempts=1),
    "conflicted": CoreState(S.NEEDS_INPUT, paused_from=S.PUSHED, fix_attempts=1),
    **{s: CoreState(S.FAILED, failed_from=S.CHECKING, fix_attempts=1)
       for s in ("failed", "timed_out", "budget_exceeded", "environment_error")},
}

# Every legal stored-status move and the core state it derives:
# "<state>[<origin][#fix_attempts]". NEEDS_HUMAN targets use reason "question".
# ``timed_out`` / ``budget_exceeded`` are never written any more (a reached cap
# pauses with attention ``limit_reached``); old rows can still be retried.
TRANSITION_GOLDEN = [
    ("queued", "cancelled", "cancelled"),
    ("queued", "preparing", "provisioning"),
    ("preparing", "cancelled", "cancelled"),
    ("preparing", "environment_error", "failed<provisioning"),
    ("preparing", "failed", "failed<provisioning"),
    ("preparing", "needs_human", "needs_input<provisioning"),
    ("preparing", "planning", "planning"),
    ("preparing", "recovering", "implementing"),
    ("preparing", "running", "implementing"),
    ("running", "cancelled", "cancelled#1"),
    ("running", "environment_error", "failed<implementing#1"),
    ("running", "failed", "failed<implementing#1"),
    ("running", "needs_human", "needs_input<implementing#1"),
    ("running", "queued", "implementing#1"),
    ("running", "recovering", "implementing#1"),
    ("running", "verifying", "checking#1"),
    ("needs_human", "cancelled", "cancelled#1"),
    ("needs_human", "failed", "failed<checking#1"),
    ("needs_human", "planning", "planning#1"),
    ("needs_human", "publishing", "pushing#1"),
    ("needs_human", "queued", "checking#1"),
    ("needs_human", "reviewing", "reviewing#1"),
    ("needs_human", "running", "implementing#1"),
    ("needs_human", "verifying", "checking#1"),
    ("verifying", "cancelled", "cancelled#1"),
    ("verifying", "conflicted", "needs_input<checking#1"),
    ("verifying", "failed", "failed<checking#1"),
    ("verifying", "needs_human", "needs_input<checking#1"),
    ("verifying", "queued", "implementing#1"),
    ("verifying", "ready_for_review", "pushed#1"),
    ("verifying", "recovering", "implementing#1"),
    ("verifying", "reviewing", "reviewing#1"),
    ("verifying", "running", "implementing#1"),
    ("ready_for_review", "cancelled", "cancelled#1"),
    ("ready_for_review", "conflicted", "needs_input<pushed#1"),
    ("ready_for_review", "merged", "done#1"),
    ("ready_for_review", "needs_human", "needs_input<pushed#1"),
    ("ready_for_review", "queued", "implementing"),
    ("ready_for_review", "running", "implementing#1"),
    ("ready_for_review", "verifying", "checking#1"),
    ("recovering", "cancelled", "cancelled#1"),
    ("recovering", "failed", "failed<implementing#1"),
    ("recovering", "needs_human", "needs_input<implementing#1"),
    ("recovering", "planning", "planning#1"),
    ("recovering", "publishing", "pushing#1"),
    ("recovering", "reviewing", "reviewing#1"),
    ("recovering", "running", "implementing#1"),
    ("recovering", "environment_error", "failed<implementing#1"),
    ("conflicted", "cancelled", "cancelled#1"),
    ("conflicted", "failed", "failed<pushed#1"),
    ("conflicted", "needs_human", "needs_input<pushed#1"),
    # Requeueing a conflicted pushed branch is new implementation work with a
    # fresh fix budget (never back to PUSHED, which would re-fire writeback).
    ("conflicted", "queued", "implementing"),
    ("conflicted", "running", "implementing#1"),
    ("conflicted", "merged", "done#1"),
    ("planning", "cancelled", "cancelled"),
    ("planning", "environment_error", "failed<planning"),
    ("planning", "failed", "failed<planning"),
    ("planning", "needs_human", "needs_input<planning"),
    ("planning", "queued", "provisioning"),
    ("planning", "recovering", "implementing"),
    ("planning", "running", "implementing"),
    ("reviewing", "cancelled", "cancelled#1"),
    ("reviewing", "failed", "failed<reviewing#1"),
    ("reviewing", "needs_human", "needs_input<reviewing#1"),
    ("reviewing", "publishing", "pushing#1"),
    ("reviewing", "queued", "provisioning#1"),
    ("reviewing", "ready_for_review", "pushed#1"),
    ("reviewing", "recovering", "implementing#1"),
    ("reviewing", "running", "implementing#1"),
    ("publishing", "cancelled", "cancelled#1"),
    ("publishing", "failed", "failed<pushing#1"),
    ("publishing", "needs_human", "needs_input<pushing#1"),
    ("publishing", "ready_for_review", "pushed#1"),
    ("publishing", "recovering", "implementing#1"),
    *[(failure, target, expected)
      for failure in ("failed", "timed_out", "budget_exceeded", "environment_error")
      for target, expected in (("cancelled", "cancelled#1"), ("planning", "planning#1"),
                               ("publishing", "pushing#1"), ("queued", "checking#1"),
                               ("reviewing", "reviewing#1"), ("verifying", "checking#1"))],
]


def _fmt(core):
    origin = core.paused_from or core.failed_from
    return core.state.value + (f"<{origin.value}" if origin else "") + (f"#{core.fix_attempts}" if core.fix_attempts else "")


def test_golden_covers_exactly_the_legal_status_moves():
    legal = {(src, dst) for src, targets in core_states.STATUS_TRANSITIONS.items() for dst in targets}
    assert {(src, dst) for src, dst, _ in TRANSITION_GOLDEN} == legal
    assert set(core_states.STATUS_TRANSITIONS) == {s.value for s in JobStatus}


@pytest.mark.parametrize("src,dst,expected", TRANSITION_GOLDEN)
def test_every_legal_status_move_derives_a_pinned_core_state(src, dst, expected):
    attention = "question" if dst == "needs_human" else ""
    assert _fmt(advance(CANONICAL_PRIOR[src], dst, attention)) == expected


def test_advance_is_total_and_ignores_history():
    for state in S:
        prior = CoreState(state, paused_from=S.REVIEWING if state == S.NEEDS_INPUT else None,
                          failed_from=S.PLANNING if state == S.FAILED else None)
        for status in JobStatus:
            for reason in AttentionReason:
                assert isinstance(advance(prior, status, reason).state, S)


@pytest.mark.parametrize("prior,reason,expected", [
    (CoreState(S.PLANNING), "plan_review", "plan_review"),
    (CoreState(S.REVIEWING, fix_attempts=2), "review_done", "review_done#2"),
    (CoreState(S.REVIEWING), "review_stalled", "review_done"),
    (CoreState(S.IMPLEMENTING), "approval_required", "needs_input<implementing"),
    (CoreState(S.PLANNING), "provider_error", "needs_input<planning"),
    # A closed-unmerged PR keeps the job pushed, parked on a human decision.
    (CoreState(S.PUSHED, fix_attempts=1), "pr_closed", "pushed#1"),
])
def test_needs_human_gates_versus_pauses(prior, reason, expected):
    assert _fmt(advance(prior, "needs_human", reason)) == expected


@pytest.mark.parametrize("prior,status,expected", [
    # Leaving a human gate is a fresh implementation run with a fresh fix budget.
    (CoreState(S.PLAN_REVIEW, fix_attempts=2), "queued", "implementing"),
    (CoreState(S.REVIEW_DONE, fix_attempts=2), "queued", "implementing"),
    # Resume / retry return to the step they left.
    (CoreState(S.NEEDS_INPUT, paused_from=S.PLANNING), "queued", "planning"),
    (CoreState(S.FAILED, failed_from=S.REVIEW_DONE), "queued", "review_done"),
    # Paused at a human gate (pushed branch went conflicted, question at a
    # gate): scheduling it is a fresh implementation run, not the gate again.
    (CoreState(S.NEEDS_INPUT, paused_from=S.PUSHED, fix_attempts=2), "queued", "implementing"),
    (CoreState(S.NEEDS_INPUT, paused_from=S.REVIEW_DONE, fix_attempts=2), "queued", "implementing"),
    (CoreState(S.NEEDS_INPUT, paused_from=S.PLAN_REVIEW), "queued", "implementing"),
    (CoreState(S.NEEDS_INPUT, paused_from=S.PUSHED, fix_attempts=2), "recovering", "implementing"),
    # The worker's own scheduling steps keep the fix loop going.
    (CoreState(S.IMPLEMENTING, fix_attempts=1), "preparing", "implementing#1"),
    (CoreState(S.CHECKING, fix_attempts=1), "queued", "implementing#1"),
    # Old rows that lost their origin still land somewhere defined.
    (CoreState(S.NEEDS_INPUT), "queued", "implementing"),
    (CoreState(S.FAILED), "queued", "provisioning"),
])
def test_scheduling_round_trips(prior, status, expected):
    assert _fmt(advance(prior, status)) == expected


def test_sandbox_failure_fails_in_provisioning():
    assert _fmt(advance(CoreState(S.REVIEWING), "failed", stage="provisioning")) == "failed<provisioning"
    assert _fmt(advance(CoreState(S.REVIEWING), "timed_out", stage="provisioning")) == "failed<reviewing"


def test_status_phase_table_drives_scheduling():
    assert core_states.SCHEDULABLE_STATUSES == ("queued", "planning", "recovering", "verifying",
                                                "reviewing", "publishing")
    assert {core_states.phase_for_status(s) for s in core_states.SCHEDULABLE_STATUSES} == set(core_states.PHASES)
    assert core_states.phase_for_status(JobStatus.RECOVERING) == "implementation"


# --------------------------------------------------------------- recovery (W1 cases)
class _Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def _recovery_service(tmp_path):
    clock = _Clock()
    return JobService(JobStore(str(tmp_path / "jobs.sqlite3"), clock=clock), reaper=lambda _id: 0), clock


def _expire(service, clock, job_id):
    assert service.acquire(job_id, "dead-worker", ttl_seconds=10)
    clock.now += 11
    service.recover_expired_leases()


STAGED = {"provider": "openai", "model": "m", "session_type": "chat", "pipeline": "staged"}


def test_planning_lease_expiry_stays_planning_in_both_models(tmp_path):
    service, clock = _recovery_service(tmp_path)
    job = service.create(JobSpec(title="t", execution=STAGED))
    service.transition(job.id, JobStatus.PREPARING)
    service.transition(job.id, JobStatus.PLANNING)
    _expire(service, clock, job.id)
    assert service.get(job.id).status == JobStatus.PLANNING
    assert service.store.core_state(job.id) == CoreState(S.PLANNING)


def test_preparing_without_plan_recovers_to_planning_in_both_models(tmp_path):
    service, clock = _recovery_service(tmp_path)
    job = service.create(JobSpec(title="t", execution=STAGED))
    service.transition(job.id, JobStatus.PREPARING)
    _expire(service, clock, job.id)
    assert service.get(job.id).status == JobStatus.PLANNING
    assert service.store.core_state(job.id).state == S.PLANNING


def test_running_lease_expiry_maps_recovering_to_implementing(tmp_path):
    service, clock = _recovery_service(tmp_path)
    job = service.create(JobSpec(title="t", execution={"provider": "openai", "model": "m", "session_type": "chat"}))
    service.transition(job.id, JobStatus.PREPARING)
    service.transition(job.id, JobStatus.RUNNING)
    service.store.set_fix_attempts(job.id, 1)
    _expire(service, clock, job.id)
    assert service.get(job.id).status == JobStatus.RECOVERING
    assert service.store.core_state(job.id) == CoreState(S.IMPLEMENTING, fix_attempts=1)
    service.transition(job.id, JobStatus.RUNNING)
    assert service.store.core_state(job.id) == CoreState(S.IMPLEMENTING, fix_attempts=1)


def test_store_derivation_does_not_read_event_history(tmp_path, monkeypatch):
    store = JobStore(str(tmp_path / "jobs.sqlite3"))
    store.initialize()
    job = store.create_job(JobSpec(title="t"))
    monkeypatch.setattr(JobStore, "_legacy_history", staticmethod(lambda *a, **k: pytest.fail("history read")))
    for status in (JobStatus.PREPARING, JobStatus.RUNNING, JobStatus.VERIFYING, JobStatus.REVIEWING):
        store.transition(job.id, status)
    store.transition(job.id, JobStatus.NEEDS_HUMAN, attention_reason=AttentionReason.REVIEW_DONE)
    assert store.core_state(job.id).state == S.REVIEW_DONE


def test_conflicted_pushed_job_requeue_does_not_refire_pushed():
    core = advance(CoreState(S.PUSHED), "conflicted")
    assert _fmt(core) == "needs_input<pushed"
    core = advance(core, "queued")
    assert core.state == S.IMPLEMENTING
    assert _fmt(advance(core, "preparing")) == "implementing"


# --------------------------------------------------------------- reached caps pause and ask
@pytest.mark.parametrize("stage", [S.PLANNING, S.IMPLEMENTING, S.REVIEWING, S.CHECKING, S.PROVISIONING])
def test_limit_reached_pauses_in_the_stage_and_raise_resumes_into_it(stage):
    paused = advance(CoreState(stage, fix_attempts=1), "needs_human", "limit_reached")
    assert _fmt(paused) == f"needs_input<{stage.value}#1"
    resumed = transition(paused, stage)
    assert resumed.state == stage and resumed.paused_from is None


def test_no_status_ever_moves_to_timed_out_or_budget_exceeded():
    for src, targets in core_states.STATUS_TRANSITIONS.items():
        assert "timed_out" not in targets and "budget_exceeded" not in targets, src
    # Old rows keep them readable and retryable.
    assert core_states.STATUS_TRANSITIONS["timed_out"] and core_states.STATUS_TRANSITIONS["budget_exceeded"]


def test_raise_limit_action_while_paused_on_a_limit():
    from mu.jobs.limits import limit_detail
    detail = limit_detail("cost", "job", "implement", 100.03, 100.0)
    acts = action_map(JobFacts(core=CoreState(S.NEEDS_INPUT, paused_from=S.IMPLEMENTING), limit_reached=detail))
    raise_limit = acts["raiseLimit"]
    assert raise_limit.enabled and raise_limit.primary and not raise_limit.hidden
    assert raise_limit.label == "Approve more & continue"
    assert raise_limit.args == {"kind": "cost", "scope": "job", "stage": "implement", "amount": "100"}
    assert raise_limit.input["arg"] == "amount" and raise_limit.input["value"] == "100"
    assert acts["discard"].enabled and acts["discard"].label == "Cancel job"
    assert not acts["resume"].enabled
    running = action_map(JobFacts(core=CoreState(S.IMPLEMENTING)))
    assert running["raiseLimit"].enabled and running["raiseLimit"].hidden and running["raiseLimit"].label == "Raise limit"
    assert check_allowed(JobFacts(core=CoreState(S.DONE)), "raiseLimit")
