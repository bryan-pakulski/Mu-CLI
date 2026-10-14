"""Durable job v2 task 7: PUBLISHING stage (push branch, open draft PR)."""

from __future__ import annotations

import json
import subprocess

from mu.jobs import JobService, JobStore
from mu.jobs.controller import JobController
from mu.jobs.models import AttentionReason, JobStatus
from mu.jobs.publish import parse_remote, publish_job
from mu.jobs.stages import apply_review_verdict

EXE = {"provider": "ollama", "model": "m", "session_type": "workspace"}
SECRET = "ghp_" + "S" * 36


def _git(path, *args):
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True)


def _git_job(tmp_path, mode):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "T")
    _git(repo, "config", "user.email", "t@example.com")
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    job = service.create_from_payload({
        "title": "Ticket", "workspace_root": str(repo),
        "execution": {**EXE, "publish_mode": mode},
    })
    service.store.update_runtime_fields(job.id, branch="mucli/job-abc")
    # Publishing now requires successful checks for the actual branch HEAD.
    _git(repo, "branch", "mucli/job-abc")
    from mu.jobs.verification import VerificationRun, VerificationStore
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()
    VerificationStore(service.store).save(VerificationRun(
        id="passed-checks", job_id=job.id, status="passed", passed=True,
        started_at=1, finished_at=2, duration_ms=1000, base_sha=head, head_sha=head,
        branch="mucli/job-abc", worktree=str(repo)))
    for status in (JobStatus.PREPARING, JobStatus.RUNNING, JobStatus.VERIFYING,
                   JobStatus.REVIEWING, JobStatus.PUBLISHING):
        service.transition(job.id, status, reason="test")
    return service, service.get(job.id)


class FakeRun:
    def __init__(self, origin, *, push_rc=0, push_err="", gh_out="https://github.com/o/r/pull/7\n"):
        self.origin, self.push_rc, self.push_err, self.gh_out = origin, push_rc, push_err, gh_out
        self.calls = []

    def __call__(self, argv, cwd):
        self.calls.append(list(argv))
        if argv[:3] == ["git", "remote", "get-url"]:
            return 0, self.origin + "\n", ""
        if argv[:2] == ["git", "push"]:
            return self.push_rc, "", self.push_err
        if argv[:3] == ["gh", "pr", "create"]:
            return 0, self.gh_out, ""
        return 1, "", "unexpected"


class FakePost:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, url, headers, body):
        self.calls.append({"url": url, "headers": dict(headers), "body": dict(body)})
        return self.response


def test_parse_remote_detects_host_kind():
    assert parse_remote("git@github.com:acme/app.git") == {"host": "github.com", "path": "acme/app", "kind": "github"}
    assert parse_remote("https://gitlab.example.com/grp/sub/app.git")["kind"] == "gitlab"
    assert parse_remote("ssh://git@github.com/acme/app")["path"] == "acme/app"


def test_gh_mode_pushes_and_opens_draft_pr(tmp_path):
    service, job = _git_job(tmp_path, "gh")
    run = FakeRun("git@github.com:acme/app.git")
    assert publish_job(service, job.id, run=run, post=FakePost({}), env={}) == 0
    job = service.get(job.id)
    assert job.status == JobStatus.READY_FOR_REVIEW
    from mu.jobs.verification import VerificationStore
    head = VerificationStore(service.store).latest(job.id).head_sha
    assert ["git", "push", "origin", f"{head}:refs/heads/mucli/job-abc"] in run.calls
    gh = next(c for c in run.calls if c[:3] == ["gh", "pr", "create"])
    assert "--draft" in gh and gh[gh.index("--head") + 1] == "mucli/job-abc"
    assert job.metadata["pr"]["url"] == "https://github.com/o/r/pull/7"
    assert job.metadata["pr"]["draft"] is True


def test_github_rest_draft_pr_and_token_not_persisted(tmp_path):
    service, job = _git_job(tmp_path, "github")
    post = FakePost({"html_url": "https://github.com/acme/app/pull/3", "number": 3})
    publish_job(service, job.id, run=FakeRun("git@github.com:acme/app.git"), post=post,
                env={"GITHUB_TOKEN": SECRET})
    call = post.calls[0]
    assert call["url"] == "https://api.github.com/repos/acme/app/pulls"
    assert call["body"]["draft"] is True and call["body"]["head"] == "mucli/job-abc"
    job = service.get(job.id)
    assert job.status == JobStatus.READY_FOR_REVIEW
    assert job.metadata["pr"]["url"].endswith("/pull/3")
    assert SECRET not in json.dumps(job.to_dict())


def test_gitlab_merge_request_is_draft(tmp_path):
    service, job = _git_job(tmp_path, "gitlab")
    post = FakePost({"web_url": "https://gitlab.example.com/grp/app/-/merge_requests/9", "iid": 9})
    publish_job(service, job.id, run=FakeRun("git@gitlab.example.com:grp/app.git"), post=post,
                env={"GITLAB_TOKEN": "glpat-x"})
    assert "/projects/grp%2Fapp/merge_requests" in post.calls[0]["url"]
    assert post.calls[0]["body"]["title"].startswith("Draft: ")
    assert service.get(job.id).metadata["pr"]["number"] == 9


def test_push_failure_parks_publish_failed_with_redaction(tmp_path):
    service, job = _git_job(tmp_path, "gh")
    run = FakeRun("https://x:" + SECRET + "@github.com/acme/app.git", push_rc=1,
                  push_err="fatal: auth failed for https://x:" + SECRET + "@github.com/acme/app.git")
    assert publish_job(service, job.id, run=run, post=FakePost({}), env={}) != 0
    job = service.get(job.id)
    assert job.status == JobStatus.NEEDS_HUMAN
    assert job.attention_reason == AttentionReason.PUBLISH_FAILED
    assert SECRET not in json.dumps(job.to_dict())


def test_missing_token_parks_publish_failed(tmp_path):
    service, job = _git_job(tmp_path, "github")
    publish_job(service, job.id, run=FakeRun("git@github.com:acme/app.git"), post=FakePost({}), env={})
    assert service.get(job.id).attention_reason == AttentionReason.PUBLISH_FAILED


def test_publish_mode_none_skips_to_ready(tmp_path):
    service, job = _git_job(tmp_path, "none")
    run = FakeRun("git@github.com:acme/app.git")
    publish_job(service, job.id, run=run, post=FakePost({}), env={})
    assert service.get(job.id).status == JobStatus.READY_FOR_REVIEW
    assert run.calls == []


def test_git_job_has_publish_configuration_but_review_waits_for_human(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "T")
    _git(repo, "config", "user.email", "t@example.com")
    (repo / "a").write_text("a", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "a")
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    job = service.create_from_payload({"title": "T", "workspace_root": str(repo), "execution": dict(EXE)})
    assert job.execution["publish_mode"] == "auto"
    for status in (JobStatus.PREPARING, JobStatus.RUNNING, JobStatus.VERIFYING, JobStatus.REVIEWING):
        service.transition(job.id, status, reason="test")
    apply_review_verdict(service, job.id, {"verdict": "approve", "summary": "ok", "feedback": []})
    assert service.get(job.id).attention_reason == AttentionReason.REVIEW_DONE
    assert service.store.core_state(job.id).state.value == "review_done"


def test_non_git_job_never_publishes(tmp_path):
    ws = tmp_path / "plain"
    ws.mkdir()
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    job = service.create_from_payload({"title": "T", "workspace_root": str(ws), "execution": dict(EXE)})
    assert job.execution["publish_mode"] == "none"


def test_controller_dispatches_publish_phase():
    assert JobController._phase_for_status(JobStatus.PUBLISHING) == "publish"
