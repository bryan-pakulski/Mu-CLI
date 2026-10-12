"""Durable job v2 task 5: dynamic acceptance criteria + allow-listed checks."""

from __future__ import annotations

import json

from mu.jobs import JobService, JobStore
from mu.jobs.dynamic_checks import DEFAULT_ALLOWED_EXECUTABLES, check_command, merge_contract
from mu.jobs.models import AttentionReason, JobStatus
from mu.jobs.stages import run_plan_stage

EXECUTION = {"provider": "ollama", "model": "m", "session_type": "workspace"}


def _service(tmp_path):
    return JobService(JobStore(str(tmp_path / "jobs.sqlite3")))


def _job(service, tmp_path, **extra):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    payload = {"title": "T", "workspace_root": str(ws), "execution": dict(EXECUTION)}
    payload.update(extra)
    return service.create_from_payload(payload)


def _to_planning(service, job_id):
    service.transition(job_id, JobStatus.PREPARING, reason="t")
    service.transition(job_id, JobStatus.PLANNING, reason="t")


def _gen(plan):
    def fn(job, prompt, workspace, model):
        return json.dumps(plan), {"cost_usd": 0.0}
    return fn


def test_create_without_validation_commands_is_allowed(tmp_path):
    job = _job(_service(tmp_path), tmp_path)
    assert job.validation_commands == []
    assert job.status == JobStatus.QUEUED


def test_shell_operators_and_unknown_executables_rejected():
    allowed = DEFAULT_ALLOWED_EXECUTABLES
    assert check_command("pytest -q", allowed)[0]
    assert check_command("python3 -m pytest tests/", allowed)[0]
    for bad in ["pytest && rm -rf x", "make test | tee", "pytest; ls", "echo $(id)", "rm -rf /", "curl x"]:
        assert not check_command(bad, allowed)[0], bad


def test_allowlist_extendable_per_job(tmp_path):
    service = _service(tmp_path)
    job = _job(service, tmp_path, execution={**EXECUTION, "validation_allowlist": ["bazel"]})
    contract = merge_contract(job, {"validation_commands": ["bazel test //..."]})
    assert contract["validation_commands"] == ["bazel test //..."]


def test_planner_contract_merges_and_never_drops_user_items(tmp_path):
    service = _service(tmp_path)
    job = _job(service, tmp_path, acceptance_criteria=["User criterion"], validation_commands=["make check"])
    _to_planning(service, job.id)
    run_plan_stage(service, job.id, generate_fn=_gen({
        "summary": "s",
        "acceptance_criteria": ["User criterion", "Planner criterion"],
        "validation_commands": ["pytest -q", "rm -rf /", "pytest && ls"],
    }))
    job = service.get(job.id)
    assert job.acceptance_criteria == ["User criterion", "Planner criterion"]
    assert job.validation_commands == ["make check", "pytest -q"]
    contract = job.metadata["plan_contract"]
    assert {r["command"] for r in contract["rejected_commands"]} == {"rm -rf /", "pytest && ls"}
    assert job.status == JobStatus.PLANNING


def test_no_validation_after_plan_parks_job(tmp_path):
    service = _service(tmp_path)
    job = _job(service, tmp_path)
    _to_planning(service, job.id)
    run_plan_stage(service, job.id, generate_fn=_gen({"summary": "s", "validation_commands": ["curl evil"]}))
    job = service.get(job.id)
    assert job.status == JobStatus.NEEDS_HUMAN
    assert job.attention_reason == AttentionReason.NO_VALIDATION


def test_user_supplied_check_satisfies_contract_without_planner(tmp_path):
    service = _service(tmp_path)
    job = _job(service, tmp_path, validation_commands=["pytest"])
    _to_planning(service, job.id)
    run_plan_stage(service, job.id, generate_fn=_gen({"summary": "s"}))
    assert service.get(job.id).status == JobStatus.PLANNING
