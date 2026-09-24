"""Milestone 3 optional pass: independent acceptance-criteria review."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys

from mu.jobs import AttentionReason, JobService, JobSpec, JobStatus, JobStore
from mu.jobs.acceptance import (
    REVIEWER_TOOL_ALLOWLIST,
    AcceptanceReviewer,
    acceptance_review_enabled,
    aggregate,
    build_review_prompt,
    parse_review_response,
)
from mu.jobs.verification import DeterministicVerifier, VerificationStore
from mu.jobs.verify_worker import apply_verification_result
from mu.jobs.worktree import JobWorktreeManager


def git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args], text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=True,
    ).stdout.strip()


def verifying_job(tmp_path, *, execution_extra=None, criteria=("code.txt says implemented",)):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "code.txt").write_text("base\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote('pass')}"
    job = service.create(JobSpec(
        title="Accept me", repository=str(repo), validation_commands=[command],
        acceptance_criteria=list(criteria),
        execution={"provider": "openai", "model": "test", "session_type": "workspace", **(execution_extra or {})},
    ))
    manager = JobWorktreeManager(service, root=str(tmp_path / "worktrees"))
    manager.prepare(job)
    current = service.get(job.id)
    (tmp_path / "worktrees" / job.id / "code.txt").write_text("implemented\n", encoding="utf-8")
    manager.checkpoint(current, label="implementation")
    service.transition(job.id, JobStatus.PREPARING)
    service.transition(job.id, JobStatus.RUNNING)
    attempt = service.start_attempt(job.id, worker_id="w", session_name="s")
    service.finish_attempt(attempt.id, status="completed")
    service.transition(job.id, JobStatus.VERIFYING)
    return service, service.get(job.id)


# ------------------------------------------------------------------ parsing


def test_parse_tolerates_fences_and_prose_and_maps_positionally():
    text = 'Here you go:\n```json\n{"criteria":[{"criterion":"a","verdict":"PASS","evidence":"x.py:1"},{"criterion":"b","verdict":"fail","evidence":"missing"}],"summary":"ok"}\n```'
    verdicts, summary, err = parse_review_response(text, ["a", "b"])
    assert err == "" and summary == "ok"
    assert [v.verdict for v in verdicts] == ["pass", "fail"]
    assert aggregate(verdicts) == ("fail", 1, 1, 0)


def test_parse_never_passes_on_garbage_or_missing_criteria():
    verdicts, _, err = parse_review_response("I think it's fine!", ["a", "b"])
    assert err and all(v.verdict == "unknown" for v in verdicts)
    assert aggregate(verdicts)[0] == "unknown"
    # Partial coverage -> the unaddressed criterion is unknown, never pass.
    verdicts, _, _ = parse_review_response(json.dumps({"criteria": [{"criterion": "a", "verdict": "pass"}]}), ["a", "b"])
    assert [v.verdict for v in verdicts] == ["pass", "unknown"]
    assert aggregate(verdicts)[0] == "unknown"
    # Bogus verdict word -> unknown.
    verdicts, _, _ = parse_review_response(json.dumps({"criteria": [{"criterion": "a", "verdict": "maybe"}]}), ["a"])
    assert verdicts[0].verdict == "unknown"
    assert parse_review_response("", ["a"])[2] == "empty response"


def test_prompt_contains_criteria_diff_and_json_contract(tmp_path):
    service, job = verifying_job(tmp_path, criteria=("first", "second"))
    prompt = build_review_prompt(job, "+implemented", "code.txt | 1 +-")
    assert "1. first" in prompt and "2. second" in prompt
    assert "+implemented" in prompt and '"verdict": "pass|fail|unknown"' in prompt
    assert "Do not modify" in prompt


def test_reviewer_allowlist_is_read_only():
    for forbidden in ("write_file", "apply_diff", "search_and_replace_file", "spawn_agent", "bash_background"):
        assert forbidden not in REVIEWER_TOOL_ALLOWLIST


def test_enabled_flag_from_execution_or_env(monkeypatch, tmp_path):
    service, job = verifying_job(tmp_path)
    monkeypatch.delenv("MUCLI_JOBS_ACCEPTANCE_REVIEW", raising=False)
    assert acceptance_review_enabled(job) is False
    monkeypatch.setenv("MUCLI_JOBS_ACCEPTANCE_REVIEW", "1")
    assert acceptance_review_enabled(job) is True
    monkeypatch.delenv("MUCLI_JOBS_ACCEPTANCE_REVIEW", raising=False)
    (tmp_path / "b").mkdir()
    service2, job2 = verifying_job(tmp_path / "b", execution_extra={"acceptance_review": True})
    assert acceptance_review_enabled(job2) is True


# ------------------------------------------------------------------ reviewer


def _fake_generate(response):
    calls = []

    def generate(job, prompt, workspace):
        calls.append({"prompt": prompt, "workspace": workspace})
        return response, {"provider": "fake", "model": "fake-1", "cost_usd": 0.05}

    generate.calls = calls
    return generate


def test_reviewer_persists_evidence_metadata_event_and_cost(tmp_path):
    service, job = verifying_job(tmp_path)
    gen = _fake_generate(json.dumps({
        "criteria": [{"criterion": "code.txt says implemented", "verdict": "pass", "evidence": "code.txt:1"}],
        "summary": "Matches.",
    }))
    review = AcceptanceReviewer(service, generate_fn=gen, evidence_root=str(tmp_path / "evidence")).review(job.id)
    assert review.verdict == "pass" and review.met == 1 and review.cost_usd == 0.05
    assert "+implemented" in gen.calls[0]["prompt"]
    current = service.get(job.id)
    assert current.metadata["acceptance_review"]["verdict"] == "pass"
    assert current.cost_usd == 0.05
    assert (tmp_path / "evidence" / job.id / "acceptance-review.json").exists()
    assert any(e.event_type == "acceptance_review_completed" for e in service.events(job.id))
    # Verification summary now reports the flag truthfully.
    run = DeterministicVerifier(
        service, store=VerificationStore(service.store, evidence_root=str(tmp_path / "evidence"))
    ).verify(service.get(job.id))
    assert run.summary["acceptance_criteria_machine_verified"] is True


def test_reviewer_exception_becomes_unknown_verdict_not_crash(tmp_path):
    service, job = verifying_job(tmp_path)

    def boom(job, prompt, workspace):
        raise RuntimeError("provider down")

    review = AcceptanceReviewer(service, generate_fn=boom, evidence_root=str(tmp_path / "evidence")).review(job.id)
    assert review.verdict == "unknown" and "provider down" in review.error


def test_no_criteria_yields_unknown(tmp_path):
    service, job = verifying_job(tmp_path, criteria=())
    review = AcceptanceReviewer(service, generate_fn=_fake_generate("{}"), evidence_root=str(tmp_path / "e")).review(job.id)
    assert review.verdict == "unknown" and review.total == 0


# ------------------------------------------------------------------ verify_worker gate


def _patch_reviewer(monkeypatch, response):
    import mu.jobs.acceptance as acceptance

    original = acceptance.AcceptanceReviewer.__init__

    def init(self, service, *, generate_fn=None, evidence_root=None):
        original(self, service, generate_fn=_fake_generate(response), evidence_root=evidence_root)

    monkeypatch.setattr(acceptance.AcceptanceReviewer, "__init__", init)


def test_failing_acceptance_review_gates_ready_behind_human(tmp_path, monkeypatch):
    service, job = verifying_job(tmp_path, execution_extra={"acceptance_review": True})
    _patch_reviewer(monkeypatch, json.dumps({
        "criteria": [{"criterion": "code.txt says implemented", "verdict": "fail", "evidence": "nope"}],
        "summary": "Not done.",
    }))
    run = DeterministicVerifier(service, store=VerificationStore(service.store, evidence_root=str(tmp_path / "e"))).verify(job)
    assert run.passed
    assert apply_verification_result(service, job.id, run) == 24
    current = service.get(job.id)
    assert current.status == JobStatus.NEEDS_HUMAN
    assert current.attention_reason == AttentionReason.VERIFICATION_REQUIRED
    assert "1 of 1 criteria unmet" in current.attention_detail
    # Branch was still materialized (review artifact exists) — only the READY claim is withheld.
    assert current.worktree == "" and current.metadata.get("review_branch")


def test_passing_or_unknown_acceptance_review_proceeds_to_ready(tmp_path, monkeypatch):
    service, job = verifying_job(tmp_path, execution_extra={"acceptance_review": True})
    _patch_reviewer(monkeypatch, "not json at all")
    run = DeterministicVerifier(service, store=VerificationStore(service.store, evidence_root=str(tmp_path / "e"))).verify(job)
    assert apply_verification_result(service, job.id, run) == 0
    current = service.get(job.id)
    assert current.status == JobStatus.READY_FOR_REVIEW
    assert current.metadata["acceptance_review"]["verdict"] == "unknown"


def test_acceptance_review_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("MUCLI_JOBS_ACCEPTANCE_REVIEW", raising=False)
    service, job = verifying_job(tmp_path)
    called = []
    import mu.jobs.acceptance as acceptance

    monkeypatch.setattr(acceptance.AcceptanceReviewer, "review", lambda self, *a, **k: called.append(1))
    run = DeterministicVerifier(service, store=VerificationStore(service.store, evidence_root=str(tmp_path / "e"))).verify(job)
    assert apply_verification_result(service, job.id, run) == 0
    assert called == []
    assert service.get(job.id).status == JobStatus.READY_FOR_REVIEW
