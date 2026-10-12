"""Durable job v2 task 3: staged statuses + per-stage model resolution."""

from __future__ import annotations

import pytest

from mu.jobs.models import (
    JobSpec,
    JobStatus,
    can_transition,
    resolve_stage_model,
)


@pytest.mark.parametrize("current,target", [
    ("preparing", "planning"),
    ("planning", "running"),
    ("running", "verifying"),
    ("verifying", "reviewing"),
    ("reviewing", "running"),
    ("reviewing", "publishing"),
    ("reviewing", "ready_for_review"),
    ("reviewing", "needs_human"),
    ("publishing", "ready_for_review"),
    ("publishing", "needs_human"),
])
def test_staged_flow_transitions_allowed(current, target):
    assert can_transition(current, target)


@pytest.mark.parametrize("current,target", [
    ("queued", "planning"),
    ("planning", "publishing"),
    ("planning", "ready_for_review"),
    ("running", "publishing"),
    ("publishing", "running"),
    ("reviewing", "merged"),
])
def test_staged_flow_rejects_skips(current, target):
    assert not can_transition(current, target)


def test_new_statuses_can_be_cancelled():
    for status in (JobStatus.PLANNING, JobStatus.REVIEWING, JobStatus.PUBLISHING):
        assert can_transition(status, JobStatus.CANCELLED)


def test_retired_review_loop_setting_is_not_carried_into_new_jobs():
    spec = JobSpec("t", execution={"provider": "p", "model": "m"}).normalized()
    assert "max_review_rounds" not in spec.execution
    assert "max_review_rounds" not in JobSpec("t", execution={"max_review_rounds": 0}).normalized().execution


def test_stage_model_falls_back_stage_then_job_then_session():
    execution = JobSpec("t", execution={
        "provider": "anthropic", "model": "sonnet",
        "stages": {"plan": {"model": "opus"}, "review": {"provider": "openai", "model": "gpt-big"}},
    }).normalized().execution
    plan = resolve_stage_model(execution, "plan")
    assert (plan["provider"], plan["model"], plan["source"]) == ("anthropic", "opus", "stage")
    impl = resolve_stage_model(execution, "implement")
    assert (impl["provider"], impl["model"], impl["source"]) == ("anthropic", "sonnet", "job")
    review = resolve_stage_model(execution, "review")
    assert (review["provider"], review["model"]) == ("openai", "gpt-big")
    session = resolve_stage_model({}, "plan", session_provider="ollama", session_model="m")
    assert (session["provider"], session["model"], session["source"]) == ("ollama", "m", "session")


def test_unknown_stage_rejected():
    with pytest.raises(ValueError, match="unknown execution stage"):
        JobSpec("t", execution={"stages": {"deploy": {"model": "x"}}}).normalized()
    with pytest.raises(ValueError):
        resolve_stage_model({}, "deploy")
