"""W3 git and publish: squash + tree gate, PR re-push, base resolution, fetch,
host merges, providers, secret scan, byproducts, branch cleanup, git timeouts."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from mu.jobs import JobService, JobSpec, JobStatus, JobStore
from mu.jobs.models import AttentionReason
from mu.jobs.publish import (
    PublishError, find_open_pr, open_draft_pr, parse_remote, publish_job, resolve_pr_base, squash_message,
)

EXE = {"provider": "ollama", "model": "m", "session_type": "workspace"}
BRANCH = "mucli/job-abc"


def git(path, *args, env=None):
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True,
                          env=env).stdout.strip()


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    for name in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        monkeypatch.delenv(name, raising=False)


def settings(tmp_path, data):
    path = tmp_path / "home" / "jobs" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def init_repo(path, *, name="Dev Person", email="dev@example.com"):
    path.mkdir(parents=True)
    git(path, "init", "-b", "main")
    if name:
        git(path, "config", "user.name", name)
        git(path, "config", "user.email", email)
    git(path, "config", "commit.gpgsign", "false")
    (path / "README.md").write_text("x\n", encoding="utf-8")
    (path / "app.txt").write_text("base\n", encoding="utf-8")
    git(path, "add", ".")
    git(path, "commit", "-m", "base")
    return path


def commit(repo, rel, content, message):
    (Path(repo) / rel).write_text(content, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def publishing_job(tmp_path, *, mode="github", commits=(("a.txt", "one\n"), ("b.txt", "two\n")), metadata=None):
    """A PUBLISHING job whose branch carries checkpoint commits, verified at its head.

    ``origin`` is a GitHub URL that is never contacted; pushes go to the local
    bare ``mirror`` remote (``publish.push_remote``)."""
    repo = init_repo(tmp_path / "repo")
    bare = tmp_path / "mirror.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare)], check=True, capture_output=True)
    git(repo, "remote", "add", "origin", "git@github.com:acme/app.git")
    git(repo, "remote", "add", "mirror", str(bare))
    settings(tmp_path, {"publish": {"push_remote": "mirror"}})
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", BRANCH)
    for rel, content in commits:
        commit(repo, rel, content, f"mu checkpoint: {rel}")
    head = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "main")
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    job = service.create_from_payload({"title": "Add widgets", "description": "Implements the widget.",
                                       "workspace_root": str(repo), "execution": {**EXE, "publish_mode": mode}})
    service.store.update_runtime_fields(job.id, branch=BRANCH, base_sha=base,
                                        metadata_json={**job.metadata, **(metadata or {})})
    from mu.jobs.verification import VerificationRun, VerificationStore
    VerificationStore(service.store).save(VerificationRun(
        id="ok", job_id=job.id, status="passed", passed=True, started_at=1, finished_at=2, duration_ms=1,
        base_sha=base, head_sha=head, branch=BRANCH, worktree=str(repo)))
    for status in (JobStatus.PREPARING, JobStatus.RUNNING, JobStatus.VERIFYING, JobStatus.REVIEWING,
                   JobStatus.PUBLISHING):
        service.transition(job.id, status, reason="test")
    return service, service.get(job.id), repo, bare, base, head


class Run:
    """Real git for local/mirror operations; nothing reaches the GitHub origin."""

    def __init__(self, gh_list="[]"):
        self.calls, self.gh_list = [], gh_list

    def __call__(self, argv, cwd):
        self.calls.append(list(argv))
        if argv[0] == "git":
            if argv[1] in {"fetch", "ls-remote", "push"} and "origin" in argv:
                return 128, "", "origin is offline in tests"
            proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
            return proc.returncode, proc.stdout, proc.stderr
        if argv[:3] == ["gh", "pr", "list"]:
            return 0, self.gh_list, ""
        if argv[:3] == ["gh", "pr", "create"]:
            return 0, "https://github.com/acme/app/pull/5\n", ""
        return 1, "", "unexpected"

    def pushes(self):
        return [c for c in self.calls if c[:2] == ["git", "push"]]


class Post:
    def __init__(self, response=None):
        self.calls, self.response = [], response or {"html_url": "https://github.com/acme/app/pull/3", "number": 3}

    def __call__(self, url, headers, body):
        self.calls.append({"url": url, "headers": dict(headers), "body": body})
        return self.response


# ------------------------------------------------------------------ squash + tree gate (#5)
def test_publish_squashes_onto_base_with_trailer_and_verified_tree(tmp_path):
    service, job, repo, bare, base, head = publishing_job(
        tmp_path, metadata={"jira": {"key": "PROJ-12", "url": "https://jira/PROJ-12"}})
    run, post = Run(), Post()
    assert publish_job(service, job.id, run=run, post=post, env={"GITHUB_TOKEN": "t"}) == 0
    pushed = git(bare, "rev-parse", f"refs/heads/{BRANCH}")
    assert pushed != head
    assert git(bare, "rev-parse", f"{pushed}^{{tree}}") == git(repo, "rev-parse", f"{head}^{{tree}}")
    assert git(bare, "rev-list", "--parents", "-n", "1", pushed).split()[1:] == [base]
    message = git(bare, "log", "-1", "--format=%B", pushed)
    assert message.splitlines()[0] == "PROJ-12: Add widgets"
    assert "Implements the widget." in message and f"Mucli-Job: {job.id}" in message
    assert git(bare, "log", "-1", "--format=%an <%ae>", pushed) == "Dev Person <dev@example.com>"
    current = service.get(job.id)
    assert current.status == JobStatus.READY_FOR_REVIEW
    assert current.metadata["pr"]["head_sha"] == pushed and current.metadata["pr"]["verified_sha"] == head
    assert current.metadata["pushed_sha"] == pushed
    # The job branch itself (the verified commits) is untouched.
    assert git(repo, "rev-parse", BRANCH) == head


def test_squash_message_uses_ticket_alias_and_title():
    from types import SimpleNamespace
    job = SimpleNamespace(id="j1", title="Fix login", description="", metadata={"ticket": {"key": "GH-7"}})
    assert squash_message(job) == "GH-7: Fix login\n\nMucli-Job: j1\n"
    job.metadata = {"jira": {"key": "PROJ-1"}}
    job.title = "PROJ-1 already named"
    assert squash_message(job).startswith("PROJ-1 already named\n")


def test_tree_gate_refuses_a_commit_with_a_different_tree(tmp_path, monkeypatch):
    service, job, repo, bare, base, head = publishing_job(tmp_path)
    import mu.jobs.publish as publish
    real = publish._local_git

    def tampered(repo_, *args, **kwargs):
        out = real(repo_, *args, **kwargs)
        if args[:1] == ("commit-tree",):
            return base  # a commit whose tree is not the verified tree
        return out
    monkeypatch.setattr(publish, "_local_git", tampered)
    run = Run()
    assert publish_job(service, job.id, run=run, post=Post(), env={"GITHUB_TOKEN": "t"}) != 0
    current = service.get(job.id)
    assert current.attention_reason == AttentionReason.PUBLISH_FAILED
    assert "tree" in current.attention_detail and not run.pushes()


def test_commit_msg_hook_runs_only_when_enabled(tmp_path):
    service, job, repo, bare, base, head = publishing_job(tmp_path)
    hook = repo / ".git" / "hooks" / "commit-msg"
    hook.write_text('#!/bin/sh\necho "Hooked-By: test" >> "$1"\n', encoding="utf-8")
    hook.chmod(0o755)
    assert publish_job(service, job.id, run=Run(), post=Post(), env={"GITHUB_TOKEN": "t"}) == 0
    assert "Hooked-By" not in git(bare, "log", "-1", "--format=%B", BRANCH)

    # publishing_job(tmp_path / "two") writes its settings under an unused home;
    # the active MUCLI_HOME (tmp_path/home) now turns hooks on.
    service2, job2, repo2, bare2, *_ = publishing_job(tmp_path / "two")
    settings(tmp_path, {"publish": {"push_remote": "mirror", "run_hooks": True}})
    hook2 = repo2 / ".git" / "hooks" / "commit-msg"
    hook2.write_text('#!/bin/sh\necho "Hooked-By: test" >> "$1"\n', encoding="utf-8")
    hook2.chmod(0o755)
    assert publish_job(service2, job2.id, run=Run(), post=Post(), env={"GITHUB_TOKEN": "t"}) == 0
    assert "Hooked-By: test" in git(bare2, "log", "-1", "--format=%B", BRANCH)


# ------------------------------------------------------------------ secret scan (#7)
def test_secret_in_diff_refuses_publish_before_any_push(tmp_path):
    secret = "ghp_" + "A" * 36
    service, job, repo, bare, base, head = publishing_job(tmp_path, commits=(("cfg.py", f"TOKEN = '{secret}'\n"),))
    run = Run()
    assert publish_job(service, job.id, run=run, post=Post(), env={"GITHUB_TOKEN": "t"}) != 0
    current = service.get(job.id)
    assert current.attention_reason == AttentionReason.PUBLISH_FAILED
    assert "possible secrets" in current.attention_detail and "cfg.py" in current.attention_detail
    assert secret not in json.dumps(current.to_dict())
    assert not run.pushes()


def test_publish_scan_reads_binary_and_diff_disabled_files(tmp_path):
    """An agent-written .gitattributes ``-diff`` (or a binary file) must not hide a secret."""
    secret = "ghp_" + "B" * 36
    service, job, repo, bare, base, head = publishing_job(
        tmp_path, commits=((".gitattributes", "cfg.py -diff\n"), ("cfg.py", f"TOKEN = '{secret}'\n")))
    run = Run()
    assert publish_job(service, job.id, run=run, post=Post(), env={"GITHUB_TOKEN": "t"}) != 0
    assert "cfg.py" in service.get(job.id).attention_detail and not run.pushes()

    from mu.jobs.publish import scan_publish_diff
    blob = tmp_path / "bin"
    init_repo(blob)
    start = git(blob, "rev-parse", "HEAD")
    (blob / "data.bin").write_bytes(b"\x00\x01\xff" + f"\nKEY = '{secret}'\n".encode() + b"\x00")
    git(blob, "add", "data.bin")
    git(blob, "commit", "-qm", "binary")
    with pytest.raises(PublishError, match="data.bin"):
        scan_publish_diff(str(blob), start, git(blob, "rev-parse", "HEAD"))


def test_publish_scan_matches_credentials_injected_into_the_jobs_stages(tmp_path, monkeypatch):
    from mu.jobs import harness
    opaque = "opaque-credential-9876543210"
    monkeypatch.setattr(harness, "credentials", lambda agent, settings=None: {"env": {"ANTHROPIC_API_KEY": opaque}})
    service, job, repo, bare, base, head = publishing_job(tmp_path, commits=(("notes.txt", f"key {opaque}\n"),))
    execution = {**job.execution, "stage_agents": {"implement": {"agent": "claude"}}}
    service.store.update_runtime_fields(job.id, execution_json=execution)
    run = Run()
    assert publish_job(service, job.id, run=run, post=Post(), env={"GITHUB_TOKEN": "t"}) != 0
    detail = service.get(job.id).attention_detail
    assert "injected credential" in detail and opaque not in detail and not run.pushes()


# ------------------------------------------------------------------ re-push updates the PR (#1)
def test_repush_updates_existing_pr_instead_of_opening_another(tmp_path):
    service, job, repo, bare, base, head = publishing_job(tmp_path)
    post = Post()
    assert publish_job(service, job.id, run=Run(), post=post, env={"GITHUB_TOKEN": "t"}) == 0
    first = git(bare, "rev-parse", f"refs/heads/{BRANCH}")
    assert len(post.calls) == 1

    # Re-prompt: a new verified commit on the branch, back through PUBLISHING.
    git(repo, "checkout", "-q", BRANCH)
    new_head = commit(repo, "c.txt", "three\n", "mu checkpoint: c")
    git(repo, "checkout", "-q", "main")
    from mu.jobs.verification import VerificationRun, VerificationStore
    VerificationStore(service.store).save(VerificationRun(
        id="ok2", job_id=job.id, status="passed", passed=True, started_at=3, finished_at=4, duration_ms=1,
        base_sha=base, head_sha=new_head, branch=BRANCH, worktree=str(repo)))
    for status in (JobStatus.QUEUED, JobStatus.PREPARING, JobStatus.RUNNING, JobStatus.VERIFYING,
                   JobStatus.REVIEWING, JobStatus.PUBLISHING):
        service.transition(job.id, status, reason="re-prompt")
    run = Run()
    assert publish_job(service, job.id, run=run, post=post, env={"GITHUB_TOKEN": "t"}) == 0
    assert len(post.calls) == 1  # no second PR
    second = git(bare, "rev-parse", f"refs/heads/{BRANCH}")
    assert second != first
    assert git(bare, "rev-parse", f"{second}^{{tree}}") == git(repo, "rev-parse", f"{new_head}^{{tree}}")
    assert any(a.startswith("--force-with-lease=") for a in run.pushes()[0])
    assert any(e.event_type == "pr_updated" for e in service.events(job.id))
    assert service.get(job.id).metadata["pr"]["number"] == 3


def test_repush_refuses_to_overwrite_foreign_remote_commits(tmp_path):
    service, job, repo, bare, base, head = publishing_job(tmp_path)
    # Someone else pushed to the job branch name first.
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(bare), str(other)], check=True, capture_output=True)
    git(other, "config", "user.name", "X")
    git(other, "config", "user.email", "x@example.com")
    git(other, "checkout", "-q", "-b", BRANCH)
    commit(other, "foreign.txt", "f\n", "foreign")
    git(other, "push", "-q", "origin", BRANCH)
    assert publish_job(service, job.id, run=Run(), post=Post(), env={"GITHUB_TOKEN": "t"}) != 0
    assert "did not push" in service.get(job.id).attention_detail


def test_crash_after_push_finds_open_pr_by_head_branch(tmp_path):
    service, job, repo, bare, base, head = publishing_job(tmp_path, mode="gh")
    run = Run(gh_list='[{"url": "https://github.com/acme/app/pull/9", "number": 9}]')
    assert publish_job(service, job.id, run=run, post=Post(), env={}) == 0
    assert not [c for c in run.calls if c[:3] == ["gh", "pr", "create"]]
    assert service.get(job.id).metadata["pr"]["url"].endswith("/pull/9")


def test_retry_after_crash_between_push_and_metadata_write_repushes(tmp_path, monkeypatch):
    """A crash after ``git push`` but before ``pushed_sha`` is recorded must not
    leave the job unable to publish: the squash is deterministic."""
    import time

    from mu.jobs import stages
    service, job, repo, bare, base, head = publishing_job(tmp_path)
    real = stages.merge_metadata

    def crash_on_pushed(service_, job_id, **updates):
        if "pushed_sha" in updates:
            raise RuntimeError("worker died after push")
        return real(service_, job_id, **updates)

    monkeypatch.setattr(stages, "merge_metadata", crash_on_pushed)
    assert publish_job(service, job.id, run=Run(), post=Post(), env={"GITHUB_TOKEN": "t"}) == 27
    first = git(bare, "rev-parse", f"refs/heads/{BRANCH}")
    assert "pushed_sha" not in service.get(job.id).metadata
    monkeypatch.setattr(stages, "merge_metadata", real)
    time.sleep(1.1)  # a wall-clock-dated commit-tree would now produce another sha
    service.transition(job.id, JobStatus.PUBLISHING, reason="retry publish")
    assert publish_job(service, job.id, run=Run(), post=Post(), env={"GITHUB_TOKEN": "t"}) == 0
    assert service.get(job.id).status == JobStatus.READY_FOR_REVIEW
    assert git(bare, "rev-parse", f"refs/heads/{BRANCH}") == first
    assert service.get(job.id).metadata["pushed_sha"] == first


def test_retry_adopts_remote_squash_with_verified_tree_on_expected_base(tmp_path):
    """Signed/hook-stamped squashes are not reproducible: a remote commit with
    exactly the verified tree whose only parent is the base is this job's push."""
    service, job, repo, bare, base, head = publishing_job(tmp_path)
    tree = git(repo, "rev-parse", f"{head}^{{tree}}")
    ours = subprocess.run(["git", "-C", str(repo), "commit-tree", tree, "-p", base, "-m", "stamped differently"],
                          check=True, capture_output=True, text=True).stdout.strip()
    git(repo, "push", "-q", "mirror", f"{ours}:refs/heads/{BRANCH}")
    assert publish_job(service, job.id, run=Run(), post=Post(), env={"GITHUB_TOKEN": "t"}) == 0
    assert git(bare, "rev-parse", f"refs/heads/{BRANCH}") == ours
    current = service.get(job.id)
    assert current.metadata["pushed_sha"] == ours and current.metadata["pr"]["head_sha"] == ours


def test_repush_after_closed_pr_opens_a_new_pr_and_mark_done_closes_out(tmp_path):
    from mu.jobs.core.commands import dispatch
    service, job, repo, bare, base, head = publishing_job(tmp_path)
    post = Post()
    assert publish_job(service, job.id, run=Run(), post=post, env={"GITHUB_TOKEN": "t"}) == 0
    service.store.patch_metadata(job.id, mutate=lambda m: m["pr"].update(state="closed"))
    service.require_human(job.id, AttentionReason.PR_CLOSED, "PR closed without merging")

    dispatch(service, {"type": "repush", "job_id": job.id, "args": {}})
    assert service.get(job.id).status == JobStatus.PUBLISHING
    post.response = {"html_url": "https://github.com/acme/app/pull/4", "number": 4}
    assert publish_job(service, job.id, run=Run(), post=post, env={"GITHUB_TOKEN": "t"}) == 0
    assert len(post.calls) == 2  # a NEW PR, the closed one is not reused
    pr = service.get(job.id).metadata["pr"]
    assert pr["number"] == 4 and pr.get("state") != "closed"
    assert pr["replaces"] == "https://github.com/acme/app/pull/3"
    assert any(e.event_type == "pr_opened" for e in service.events(job.id))

    # Closed again: "Mark done" closes the job out without a merge.
    service.store.patch_metadata(job.id, mutate=lambda m: m["pr"].update(state="closed"))
    service.require_human(job.id, AttentionReason.PR_CLOSED, "PR closed without merging")
    dispatch(service, {"type": "markMerged", "job_id": job.id, "args": {}})
    assert service.get(job.id).status == JobStatus.MERGED
    assert service.store.core_state(job.id).state.value == "done"


def test_rest_lookup_by_head_branch_per_provider():
    seen = []

    def get(url, headers):
        seen.append(url)
        if "api.github.com" in url:
            return [{"html_url": "https://github.com/o/r/pull/1", "number": 1}]
        if "/api/v4/" in url:
            return [{"web_url": "https://gitlab.com/o/r/-/merge_requests/2", "iid": 2}]
        if "api.bitbucket.org" in url:
            return {"values": [{"id": 3, "links": {"html": {"href": "https://bitbucket.org/o/r/pull-requests/3"}}}]}
        return [{"head": {"ref": "other"}, "number": 8}, {"head": {"ref": BRANCH}, "number": 4,
                                                           "html_url": "https://git.acme.dev/o/r/pulls/4"}]
    env = {"GITHUB_TOKEN": "a", "GITLAB_TOKEN": "b", "BITBUCKET_TOKEN": "c", "GITEA_TOKEN": "d"}
    kw = dict(repo="", run=lambda *a: (1, "", ""), get=get, env=env)
    assert find_open_pr("github", {"host": "github.com", "path": "o/r"}, BRANCH, BRANCH, **kw)["number"] == 1
    assert "head=o:mucli%2Fjob-abc" in seen[-1] and "state=open" in seen[-1]
    assert find_open_pr("gitlab", {"host": "gitlab.com", "path": "o/r"}, BRANCH, BRANCH, **kw)["number"] == 2
    assert "source_branch=mucli%2Fjob-abc" in seen[-1]
    assert find_open_pr("bitbucket", {"host": "bitbucket.org", "path": "o/r"}, BRANCH, BRANCH, **kw)["number"] == 3
    assert find_open_pr("gitea", {"host": "git.acme.dev", "path": "o/r"}, BRANCH, BRANCH, **kw)["number"] == 4
    assert find_open_pr("gitea", {"host": "git.acme.dev", "path": "o/r"}, BRANCH, BRANCH,
                        repo="", run=kw["run"], get=get, env={}) is None  # no token -> no lookup


# ------------------------------------------------------------------ providers (#6)
def test_provider_hosts_map_overrides_host_sniffing(tmp_path):
    assert parse_remote("git@bitbucket.org:team/app.git", {})["kind"] == "bitbucket"
    assert parse_remote("https://codeberg.org/me/app.git", {})["kind"] == "gitea"
    assert parse_remote("git@git.acme.dev:o/app.git", {})["kind"] == "unknown"
    assert parse_remote("git@git.acme.dev:o/app.git", {"git.acme.dev": "gitea"})["kind"] == "gitea"
    settings(tmp_path, {"publish": {"provider_hosts": ["code.corp=gitlab"]}})
    assert parse_remote("https://code.corp/grp/app.git")["kind"] == "gitlab"


def _api_job(tmp_path, mode, origin):
    repo = init_repo(tmp_path / "repo")
    git(repo, "branch", BRANCH)
    git(repo, "remote", "add", "origin", origin)
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    job = service.create_from_payload({"title": "Ticket", "workspace_root": str(repo),
                                       "execution": {**EXE, "publish_mode": mode}})
    job = replace(job, branch=BRANCH)
    return job, git(repo, "rev-parse", "HEAD")


def _fake_remote_run(origin):
    def run(argv, cwd):
        if argv[:3] == ["git", "remote", "get-url"]:
            return 0, origin + "\n", ""
        return 0, "", ""
    return run


def test_bitbucket_cloud_adapter_opens_draft_pr(tmp_path):
    origin = "git@bitbucket.org:team/app.git"
    job, head = _api_job(tmp_path, "bitbucket", origin)
    post = Post({"id": 11, "links": {"html": {"href": "https://bitbucket.org/team/app/pull-requests/11"}}})
    pr = open_draft_pr(job, run=_fake_remote_run(origin), post=post, env={"BITBUCKET_TOKEN": "bb"}, verified_head=head)
    call = post.calls[0]
    assert call["url"] == "https://api.bitbucket.org/2.0/repositories/team/app/pullrequests"
    assert call["headers"]["Authorization"] == "Bearer bb"
    assert call["body"]["source"] == {"branch": {"name": BRANCH}} and call["body"]["draft"] is True
    assert call["body"]["destination"] == {"branch": {"name": "main"}}
    assert pr["url"].endswith("/pull-requests/11") and pr["number"] == 11 and pr["provider"] == "bitbucket"
    with pytest.raises(PublishError, match="BITBUCKET_TOKEN"):
        open_draft_pr(job, run=_fake_remote_run(origin), post=post, env={}, verified_head=head)


def test_gitea_adapter_via_provider_hosts(tmp_path):
    origin = "https://git.acme.dev/team/app.git"
    settings(tmp_path, {"publish": {"provider_hosts": ["git.acme.dev=gitea"]}})
    job, head = _api_job(tmp_path, "auto", origin)
    post = Post({"html_url": "https://git.acme.dev/team/app/pulls/4", "number": 4})
    pr = open_draft_pr(job, run=_fake_remote_run(origin), post=post, env={"GITEA_TOKEN": "gt"}, verified_head=head)
    call = post.calls[0]
    assert call["url"] == "https://git.acme.dev/api/v1/repos/team/app/pulls"
    assert call["headers"]["Authorization"] == "token gt"
    assert call["body"]["title"] == "WIP: Ticket" and call["body"]["head"] == BRANCH
    assert pr["provider"] == "gitea" and pr["number"] == 4


def test_configurable_push_and_pr_remotes(tmp_path):
    origin, fork = "git@github.com:acme/app.git", "git@github.com:me/app.git"
    settings(tmp_path, {"publish": {"push_remote": "fork", "pr_remote": "upstream"}})
    job, head = _api_job(tmp_path, "github", "git@github.com:unused/x.git")
    calls = []

    def run(argv, cwd):
        calls.append(list(argv))
        if argv[:3] == ["git", "remote", "get-url"]:
            return 0, (origin if argv[3] == "upstream" else fork) + "\n", ""
        return 0, "", ""
    post = Post()
    open_draft_pr(job, run=run, post=post, env={"GITHUB_TOKEN": "t"}, verified_head=head)
    push = next(c for c in calls if c[:2] == ["git", "push"])
    assert push[2] == "fork"
    assert post.calls[0]["url"] == "https://api.github.com/repos/acme/app/pulls"
    assert post.calls[0]["body"]["head"] == f"me:{BRANCH}"


# ------------------------------------------------------------------ PR base (#2)
def test_pr_base_is_upstream_of_base_branch_without_remote_prefix(tmp_path):
    upstream = init_repo(tmp_path / "up")
    git(upstream, "branch", "develop")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(upstream), str(clone)], check=True, capture_output=True)
    git(clone, "checkout", "-q", "-b", "feature", "--track", "origin/develop")
    git(clone, "checkout", "-q", "-b", "local-only")
    assert resolve_pr_base(str(clone), "feature") == "develop"
    assert resolve_pr_base(str(clone), "origin/main") == "main"
    assert resolve_pr_base(str(clone), "develop") == "develop"
    # No upstream and not on the remote -> the remote default branch.
    assert resolve_pr_base(str(clone), "local-only") == "main"
    assert resolve_pr_base(str(clone), "auto") == "main"


# ------------------------------------------------------------------ fetch + drift (#3)
def _clone_with_job(tmp_path):
    upstream = init_repo(tmp_path / "up")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(upstream), str(clone)], check=True, capture_output=True)
    git(clone, "config", "user.name", "Dev Person")
    git(clone, "config", "user.email", "dev@example.com")
    git(clone, "config", "commit.gpgsign", "false")
    return upstream, clone


def test_fetch_quietly_updates_tracking_ref_and_tolerates_offline(tmp_path):
    from mu.jobs.repository import fetch_quietly
    upstream, clone = _clone_with_job(tmp_path)
    tip = commit(upstream, "app.txt", "moved\n", "upstream moves")
    assert fetch_quietly(str(clone), "main") is True
    assert git(clone, "rev-parse", "origin/main") == tip
    git(clone, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    assert fetch_quietly(str(clone), "main") is False  # offline: no raise
    assert fetch_quietly(str(tmp_path / "up"), "main") is False  # no remote at all


def test_new_job_base_resolution_fetches_first(tmp_path):
    from mu.jobs.worktree import JobWorktreeManager
    upstream, clone = _clone_with_job(tmp_path)
    git(upstream, "checkout", "-q", "-b", "release")
    tip = commit(upstream, "rel.txt", "r\n", "release branch")
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    job = service.create(JobSpec(title="Fetch", repository=str(clone), base_branch="release"))
    info = JobWorktreeManager(service, root=str(tmp_path / "wt")).prepare(job)
    assert info.base_sha == tip and info.base_ref == "origin/release"


def test_drift_compares_against_remote_tracking_ref_after_fetch(tmp_path):
    from mu.jobs.base_drift import assess_base_drift
    from mu.jobs.worktree import JobWorktreeManager
    upstream, clone = _clone_with_job(tmp_path)
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    job = service.create(JobSpec(title="Drift", repository=str(clone), base_branch="main"))
    manager = JobWorktreeManager(service, root=str(tmp_path / "wt"))
    manager.prepare(job)
    job = service.get(job.id)
    (Path(job.worktree) / "app.txt").write_text("job edit\n", encoding="utf-8")
    manager.checkpoint(job, label="impl")
    commit(upstream, "app.txt", "upstream edit\n", "conflicting upstream")  # local main in clone untouched
    report = assess_base_drift(service, job.id)
    assert report.base_ref == "origin/main"
    assert report.drifted is True and report.mergeable is False and report.conflicted_files == ["app.txt"]


# ------------------------------------------------------------------ host merge on conflict (#4)
def _ready_conflicted(tmp_path, main_edit):
    from mu.jobs.base_drift import apply_base_drift
    from mu.jobs.worktree import JobWorktreeManager
    repo = init_repo(tmp_path / "repo")
    (repo / "other.txt").write_text("other\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "other")
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    job = service.create(JobSpec(title="Merge", repository=str(repo), validation_commands=["true"]))
    manager = JobWorktreeManager(service, root=str(tmp_path / "worktrees"))
    manager.prepare(job)
    (tmp_path / "worktrees" / job.id / "app.txt").write_text("job change\n", encoding="utf-8")
    manager.checkpoint(service.get(job.id), label="implementation")
    for status in (JobStatus.PREPARING, JobStatus.RUNNING, JobStatus.VERIFYING):
        service.transition(job.id, status)
    # The execution worktree is gone (e.g. retired after the push); the host
    # merge must work from the canonical repository alone.
    manager.remove(service.get(job.id))
    service.store.update_runtime_fields(job.id, worktree="")
    service.transition(job.id, JobStatus.READY_FOR_REVIEW)
    rel, content = main_edit
    tip = commit(repo, rel, content, "main moves")
    apply_base_drift(service, job.id)
    return repo, service, service.get(job.id), tip


def test_conflicted_base_is_merged_on_host_with_markers_and_files_in_prompt(tmp_path):
    from mu.jobs.review import JobReviewService
    repo, service, job, tip = _ready_conflicted(tmp_path, ("app.txt", "main edit\n"))
    assert job.status == JobStatus.CONFLICTED
    queued = JobReviewService(service).continue_job(job.id)
    assert queued.status == JobStatus.QUEUED
    parents = git(repo, "rev-list", "--parents", "-n", "1", job.branch).split()[1:]
    assert tip in parents and len(parents) == 2
    content = git(repo, "show", f"{job.branch}:app.txt")
    assert "<<<<<<<" in content and ">>>>>>>" in content
    assert queued.base_sha == tip
    detail = [e for e in service.events(job.id) if e.event_type == "human_response"][-1].payload["detail"]
    assert "app.txt" in detail and "conflict markers are committed" in detail
    assert any(e.event_type == "base_merged" and e.payload["status"] == "conflicted" for e in service.events(job.id))
    assert git(repo, "worktree", "list").count("\n") == 0  # temporary worktree cleaned up
    assert git(repo, "status", "--porcelain") == ""


def test_clean_base_merge_becomes_checkpoint(tmp_path):
    from mu.jobs.review import merge_base_into_branch
    repo, service, job, tip = _ready_conflicted(tmp_path, ("other.txt", "elsewhere\n"))
    result = merge_base_into_branch(service, job, job.metadata.get("base_drift"))
    assert result["status"] == "clean"
    head = git(repo, "rev-parse", job.branch)
    assert result["head_sha"] == head and tip in git(repo, "rev-list", "--parents", "-n", "1", head)
    assert git(repo, "show", f"{job.branch}:app.txt") == "job change"
    assert git(repo, "show", f"{job.branch}:other.txt") == "elsewhere"
    assert git(repo, "log", "-1", "--format=%an", head) == "Dev Person"
    assert merge_base_into_branch(service, service.get(job.id))["status"] == "up_to_date"


# ------------------------------------------------------------------ identity (#5)
def test_checkpoint_inherits_git_identity_and_falls_back_to_mucli(tmp_path, monkeypatch):
    from mu.jobs.worktree import JobWorktreeManager, git_identity_env
    repo = init_repo(tmp_path / "repo")
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    job = service.create(JobSpec(title="Who", repository=str(repo)))
    manager = JobWorktreeManager(service, root=str(tmp_path / "worktrees"))
    manager.prepare(job)
    job = service.get(job.id)
    (Path(job.worktree) / "x.txt").write_text("x", encoding="utf-8")
    sha = manager.checkpoint(job, label="impl")
    assert git(job.worktree, "log", "-1", "--format=%an <%ae>|%cn", sha) == "Dev Person <dev@example.com>|Dev Person"

    bare_cfg = tmp_path / "noid"
    bare_cfg.mkdir()
    subprocess.run(["git", "init", "-q", str(bare_cfg)], check=True)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "empty-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    env = git_identity_env(str(bare_cfg))
    assert env["GIT_AUTHOR_NAME"] == "MuCLI" and env["GIT_COMMITTER_EMAIL"] == "mucli@localhost"
    env = git_identity_env(str(bare_cfg), {"GIT_AUTHOR_NAME": "Env Person"})
    assert env["GIT_AUTHOR_NAME"] == "Env Person"


# ------------------------------------------------------------------ checks byproducts (#8)
def test_check_byproducts_are_discarded_and_reported_not_dirty(tmp_path):
    from mu.jobs.checks import ChecksStage
    from mu.jobs.worktree import JobWorktreeManager
    repo = init_repo(tmp_path / "repo")
    (repo / "package-lock.json").write_text('{"v": 1}\n', encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "lock")
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    job = service.create(JobSpec(title="Checks", repository=str(repo), validation_commands=["pytest -q", "ruff ."]))
    JobWorktreeManager(service, root=str(tmp_path / "worktrees")).prepare(job)
    job = service.get(job.id)
    seen = []

    def suite(job_, workspace, spec):
        seen.append(sorted(os.listdir(workspace)))
        (Path(workspace) / "build").mkdir(exist_ok=True)
        (Path(workspace) / "build" / f"{spec.name}.o").write_text("obj", encoding="utf-8")
        (Path(workspace) / "package-lock.json").write_text('{"v": 2}\n', encoding="utf-8")
        return {"passed": True, "return_code": 0}
    run = ChecksStage(service, run_suite=suite, settings={}).verify(job)
    assert run.passed and not run.summary["dirty_worktree"]
    kinds = {(b["suite"], b["path"], b["kind"]) for b in run.summary["byproducts"]}
    assert ("check1", "build/check1.o", "untracked") in kinds and ("check1", "package-lock.json", "lockfile") in kinds
    assert "build" not in seen[1] or not os.listdir(Path(job.worktree) / "build")  # discarded between suites
    assert git(job.worktree, "status", "--porcelain") == ""
    assert (Path(job.worktree) / "package-lock.json").read_text() == '{"v": 1}\n'


def test_tracked_source_edit_by_checks_still_fails(tmp_path):
    from mu.jobs.checks import ChecksStage
    from mu.jobs.worktree import JobWorktreeManager
    repo = init_repo(tmp_path / "repo")
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    job = service.create(JobSpec(title="Checks", repository=str(repo), validation_commands=["pytest -q"]))
    JobWorktreeManager(service, root=str(tmp_path / "worktrees")).prepare(job)
    job = service.get(job.id)

    def suite(job_, workspace, spec):
        (Path(workspace) / "app.txt").write_text("mutated\n", encoding="utf-8")
        (Path(workspace) / "new.tmp").write_text("x", encoding="utf-8")
        return {"passed": True, "return_code": 0}
    run = ChecksStage(service, run_suite=suite, settings={}).verify(job)
    assert not run.passed and run.summary["dirty_worktree"]
    assert [b["path"] for b in run.summary["byproducts"]] == ["new.tmp"]


# ------------------------------------------------------------------ branch cleanup on archive (#9)
def _archivable(tmp_path, *, pushed=False):
    from mu.jobs.worktree import JobWorktreeManager
    repo = init_repo(tmp_path / "repo")
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(bare)], check=True)
    git(repo, "remote", "add", "origin", str(bare))
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    job = service.create(JobSpec(title="Archive", repository=str(repo)))
    manager = JobWorktreeManager(service, root=str(tmp_path / "worktrees"))
    manager.prepare(job)
    job = service.get(job.id)
    (Path(job.worktree) / "x.txt").write_text("x", encoding="utf-8")
    manager.checkpoint(job, label="impl")
    manager.remove(service.get(job.id))
    git(repo, "push", "-q", "origin", job.branch)
    if pushed:
        pushed_sha = git(repo, "rev-parse", job.branch)  # what publish records (on_pushed / pr.head_sha)
        service.store.update_runtime_fields(job.id, metadata_json={**job.metadata, "pr": {"url": "u"},
                                                                   "pushed_sha": pushed_sha})
    service.cancel(job.id, reason="done")
    return repo, bare, service, service.get(job.id)


def test_archive_deletes_local_branch_and_keeps_remote_by_default(tmp_path):
    from mu.jobs.management import JobManagementService
    repo, bare, service, job = _archivable(tmp_path, pushed=True)
    head = git(repo, "rev-parse", job.branch)
    JobManagementService(service).archive(job.id)
    assert git(repo, "branch", "--list", job.branch) == ""
    assert git(bare, "branch", "--list", job.branch) != ""
    event = [e for e in service.events(job.id) if e.event_type == "job_branches_deleted"][-1]
    assert event.payload["head_sha"] == head and event.payload["local_deleted"] is True


def test_archive_deletes_remote_branch_when_enabled_and_pushed(tmp_path):
    from mu.jobs.management import JobManagementService
    settings(tmp_path, {"publish": {"delete_remote_branch": True}})
    repo, bare, service, job = _archivable(tmp_path, pushed=True)
    JobManagementService(service).archive(job.id)
    assert git(bare, "branch", "--list", job.branch) == ""


def test_archive_keeps_a_remote_branch_with_commits_the_job_did_not_push(tmp_path):
    from mu.jobs.management import JobManagementService
    settings(tmp_path, {"publish": {"delete_remote_branch": True}})
    repo, bare, service, job = _archivable(tmp_path, pushed=True)
    # A reviewer pushes a follow-up commit onto the job branch.
    reviewer = tmp_path / "reviewer"
    subprocess.run(["git", "clone", "-q", "-b", job.branch, str(bare), str(reviewer)], check=True, capture_output=True)
    git(reviewer, "-c", "user.name=r", "-c", "user.email=r@r", "commit", "-q", "--allow-empty", "-m", "review fix")
    git(reviewer, "push", "-q", "origin", job.branch)
    reviewed = git(reviewer, "rev-parse", "HEAD")
    JobManagementService(service).archive(job.id)
    assert git(bare, "rev-parse", f"refs/heads/{job.branch}") == reviewed  # not destroyed
    kept = [e for e in service.events(job.id) if e.event_type == "job_remote_branch_kept"]
    assert kept and "did not push" in kept[-1].reason
    deleted = [e for e in service.events(job.id) if e.event_type == "job_branches_deleted"][-1]
    assert deleted.payload["remote_deleted"] is False and deleted.payload["remote_skipped"]


def test_archive_never_deletes_remote_of_an_unpushed_job(tmp_path):
    from mu.jobs.management import JobManagementService
    settings(tmp_path, {"publish": {"delete_remote_branch": True}})
    repo, bare, service, job = _archivable(tmp_path, pushed=False)
    JobManagementService(service).archive(job.id)
    assert git(repo, "branch", "--list", job.branch) == ""
    assert git(bare, "branch", "--list", job.branch) != ""


# ------------------------------------------------------------------ git timeouts (#10)
def test_git_timeout_is_configurable_and_wrapped_as_repository_error(tmp_path, monkeypatch):
    import mu.jobs.repository as repository
    settings(tmp_path, {"jobs": {"git_timeout_seconds": 300}})
    assert repository.git_timeout_seconds() == 300
    seen = {}

    def slow(argv, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))
    monkeypatch.setattr(repository.subprocess, "run", slow)
    with pytest.raises(repository.RepositoryError, match="timed out"):
        repository.RepositoryRegistry.inspect(str(tmp_path))
    assert seen["timeout"] == 300
