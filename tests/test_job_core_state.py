"""Phase 0 exit tests: core state machine, legacy mapping, pipeline, actions."""

import pytest

from mu.jobs.core.actions import JobFacts, action_map, check_allowed
from mu.jobs.core.pipeline import derive_pipeline, mini_indicator
from mu.jobs.core.states import (
    CoreState, JobState as S, TransitionError, effective_state, from_legacy,
    to_legacy_status, transition,
)
from mu.jobs.models import AttentionReason, JobSpec, JobStatus
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
