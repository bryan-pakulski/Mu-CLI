from __future__ import annotations

from mu.jobs import JobService, JobSpec, JobStore
from mu.jobs.runner import implement_prompt, session_name


def make_job(service, repo):
    return service.create(JobSpec(
        title="Implement durable thing",
        description="Do the requested change.",
        repository=str(repo),
        acceptance_criteria=["works"],
        validation_commands=["pytest"],
        execution={"provider": "openai", "model": "test-model", "session_type": "workspace"},
    ))


def test_implement_prompt_carries_ticket_context_and_check_feedback(tmp_path):
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    repo = tmp_path / "repo"
    repo.mkdir()
    job = make_job(service, repo)
    service.store.append_event(job.id, "verification_failed", payload={
        "verification_id": "run-7",
        "failed_checks": [{"command": "pytest -q", "return_code": 1, "stderr": "AssertionError: boom"}],
    })
    prompt = implement_prompt(service, service.get(job.id))

    assert "Title: Implement durable thing" in prompt
    assert "Acceptance criteria:" in prompt and "- works" in prompt
    assert "Validation expected by the controller:" in prompt
    assert "- FAILED: pytest -q" in prompt and "AssertionError: boom" in prompt
    assert session_name(job) == f"job-{job.id[:20]}"
