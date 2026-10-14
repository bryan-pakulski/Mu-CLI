"""W8 repository intake: jobs on any repository by URL (clone.py), submodules in job
worktrees, read-only git metadata in the sandbox, terminal `--in URL` and the GUI create
endpoint's ``repository`` field. All remotes are local bare repositories (file://)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from mu.jobs import JobService, JobStore
from mu.jobs import clone as clone_mod
from mu.jobs.clone import CloneError, ensure_clone, parse_repo_url, redact

EXECUTION = {"provider": "ollama", "model": "m", "session_type": "workspace"}


def git(cwd, *args):
    result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


@pytest.fixture
def home(tmp_path, monkeypatch):
    from utils import config

    root = tmp_path / "home"
    monkeypatch.setenv("MUCLI_HOME", str(root))
    monkeypatch.setattr(config, "HISTORY_DIR", str(root))
    # Local submodules use the file transport, which git blocks for submodules by default.
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "protocol.file.allow")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "always")
    for key in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(key, "T")
    for key in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(key, "t@example.com")
    return root


def make_remote(tmp_path, name="proj", files=None, branch="main"):
    """A bare repository at tmp_path/remotes/<name>.git with one commit; returns (bare, work)."""
    work = tmp_path / "src" / name
    work.mkdir(parents=True)
    git(work, "init", "-q", "-b", branch)
    for rel, text in (files or {"README.md": "hello\n"}).items():
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        (work / rel).write_text(text)
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "init")
    bare = tmp_path / "remotes" / f"{name}.git"
    bare.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
    git(work, "remote", "add", "origin", str(bare))
    return bare, work


def push_commit(work, name="next.txt"):
    (work / name).write_text("more\n")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", f"add {name}")
    git(work, "push", "-q", "origin", "HEAD")
    return git(work, "rev-parse", "HEAD")


# ------------------------------------------------------------------ URL parsing


@pytest.mark.parametrize("url,host,org,name,branch", [
    ("https://github.com/acme/widget.git", "github.com", "acme", "widget", ""),
    ("https://github.com/acme/widget@release/2.x", "github.com", "acme", "widget", "release/2.x"),
    ("git@github.com:acme/widget.git@dev", "github.com", "acme", "widget", "dev"),
    ("git@gitlab.example.com:group/sub/widget.git#main", "gitlab.example.com", "group/sub", "widget", "main"),
    ("ssh://git@Host.Example:2222/acme/widget.git", "host.example:2222", "acme", "widget", ""),
    ("file:///srv/git/widget.git", "local", "git", "widget", ""),
])
def test_parse_repo_url_forms(url, host, org, name, branch):
    repo = parse_repo_url(url)
    assert (repo.host, repo.org, repo.name, repo.branch) == (host, org, name, branch)
    assert "@" + branch not in repo.url or not branch


@pytest.mark.parametrize("url", ["https://ghp_SECRETTOKEN0123@github.com/acme/widget.git",
                                 "http://oauth2@gitlab.example.com/acme/widget.git",
                                 "https://x-access-token:t@github.com/acme/widget.git",
                                 "git://user@example.com/acme/widget.git"])
def test_any_userinfo_on_http_urls_is_rejected(url):
    with pytest.raises(CloneError) as info:
        parse_repo_url(url)
    assert info.value.kind == "credentials_in_url"
    assert "SECRETTOKEN" not in str(info.value)
    # ssh users carry no secret and stay accepted.
    assert parse_repo_url("ssh://git@github.com/acme/widget.git").host == "github.com"
    assert parse_repo_url("git@github.com:acme/widget.git").host == "github.com"


def test_credentials_in_url_rejected_and_redacted():
    with pytest.raises(CloneError) as info:
        parse_repo_url("https://bob:ghp_SECRET@github.com/acme/widget.git")
    assert "ghp_SECRET" not in str(info.value)
    assert info.value.kind == "credentials_in_url"
    assert "credential helper for github.com" in str(info.value)
    assert redact("fatal: https://x:tok@h/r failed") == "fatal: https://***@h/r failed"


def test_clone_path_layout(home):
    repo = parse_repo_url("https://github.com/acme/widget.git@dev")
    path = clone_mod.clone_path(repo)
    assert path.startswith(str(home / "repos" / "github.com" / "acme" / "widget-"))
    # The branch suffix does not change the clone location.
    assert path == clone_mod.clone_path(parse_repo_url("https://github.com/acme/widget"))


def test_auth_failure_message_is_actionable():
    repo = parse_repo_url("git@github.com:acme/private.git")
    err = clone_mod._failure("cloning", repo, "git@github.com: Permission denied (publickey).\nfatal: Could not read")
    assert err.kind == "auth_failed" and "configure git credentials / ssh agent for github.com" in str(err)


# ------------------------------------------------------------------ ensure_clone


def test_ensure_clone_creates_then_fetches(tmp_path, home):
    bare, work = make_remote(tmp_path)
    url = f"file://{bare}"
    first = ensure_clone(url)
    assert first.created and os.path.isfile(os.path.join(first.path, "README.md"))
    assert first.branch == "main" and first.base_sha == git(work, "rev-parse", "HEAD")
    assert first.path.startswith(str(home / "repos" / "local"))
    tip = push_commit(work)
    second = ensure_clone(url)
    assert not second.created and second.path == first.path
    assert second.base_sha == tip  # fetched remote tip, even though the checkout is older


def test_ensure_clone_branch_suffix(tmp_path, home):
    bare, work = make_remote(tmp_path)
    git(work, "checkout", "-q", "-b", "feature")
    tip = push_commit(work, "feature.txt")
    result = ensure_clone(f"file://{bare}@feature")
    assert result.branch == "feature" and result.base_sha == tip
    with pytest.raises(CloneError, match="does not exist"):
        ensure_clone(f"file://{bare}#nope")


def test_ensure_clone_refuses_foreign_path(tmp_path, home):
    bare, _ = make_remote(tmp_path)
    url = f"file://{bare}"
    path = Path(clone_mod.clone_path(parse_repo_url(url)))
    path.mkdir(parents=True)
    (path / "notes.txt").write_text("mine")
    with pytest.raises(CloneError) as info:
        ensure_clone(url)
    assert info.value.kind == "path_conflict"
    assert (path / "notes.txt").read_text() == "mine"


def test_ensure_clone_refuses_other_origin(tmp_path, home):
    bare, _ = make_remote(tmp_path)
    other, _ = make_remote(tmp_path, name="other")
    url = f"file://{bare}"
    path = clone_mod.clone_path(parse_repo_url(url))
    subprocess.run(["git", "clone", "-q", str(other), path], check=True)
    with pytest.raises(CloneError, match="its origin is"):
        ensure_clone(url)


def test_missing_remote_reports_not_found(tmp_path, home):
    with pytest.raises(CloneError) as info:
        ensure_clone(f"file://{tmp_path}/absent.git")
    assert info.value.kind in ("not_found", "clone_failed")
    assert not os.path.exists(clone_mod.clone_path(parse_repo_url(f"file://{tmp_path}/absent.git")))


def test_clone_initialises_submodules(tmp_path, home):
    sub_bare, _ = make_remote(tmp_path, name="lib", files={"lib.txt": "lib\n"})
    bare, work = make_remote(tmp_path)
    subprocess.run(["git", "-C", str(work), "submodule", "add", "-q", f"file://{sub_bare}", "vendor/lib"],
                   check=True, capture_output=True)
    git(work, "commit", "-q", "-m", "add submodule")
    git(work, "push", "-q", "origin", "HEAD")
    result = ensure_clone(f"file://{bare}")
    assert (Path(result.path) / "vendor" / "lib" / "lib.txt").is_file()
    assert result.warnings == []
    skipped = ensure_clone(f"file://{bare}", submodules=False)
    assert skipped.warnings == []


def test_lfs_without_git_lfs_warns(tmp_path, home, monkeypatch):
    bare, _ = make_remote(tmp_path, files={".gitattributes": "*.bin filter=lfs diff=lfs merge=lfs -text\n",
                                           "README.md": "x\n"})
    monkeypatch.setattr(clone_mod.shutil, "which", lambda name: None)
    result = ensure_clone(f"file://{bare}")
    assert any("git-lfs is not installed" in w for w in result.warnings)
    assert ensure_clone(f"file://{bare}", lfs="off").warnings == []


# ------------------------------------------------------------------ job creation


def _service(tmp_path):
    return JobService(JobStore(str(tmp_path / "jobs.sqlite3")))


def test_create_job_from_repository_url(tmp_path, home, monkeypatch):
    bare, work = make_remote(tmp_path, files={".gitattributes": "*.bin filter=lfs -text\n"})
    monkeypatch.setattr(clone_mod.shutil, "which", lambda name: None)
    service = _service(tmp_path)
    job = service.create_from_payload({"title": "T", "repository": f"file://{bare}", "execution": dict(EXECUTION)})
    clone_path = clone_mod.clone_path(parse_repo_url(f"file://{bare}"))
    assert job.repository == clone_path
    assert job.metadata["repository_url"] == f"file://{bare}"
    assert job.base_branch == "main" and job.base_sha == git(work, "rev-parse", "HEAD")
    assert job.execution["git_enabled"] is True
    events = [e for e in service.events(job.id) if e.event_type == "repository_warning"]
    assert events and "git-lfs" in events[0].reason


def test_create_job_from_repository_path_and_conflicts(tmp_path, home):
    _, work = make_remote(tmp_path)
    service = _service(tmp_path)
    job = service.create_from_payload({"title": "T", "repo": str(work), "execution": dict(EXECUTION)})
    assert job.repository == str(work) and "repository_url" not in job.metadata
    with pytest.raises(ValueError, match="either 'repository' or 'workspace_root'"):
        service.create_from_payload({"title": "T", "repository": str(work), "workspace_root": str(tmp_path),
                                     "execution": dict(EXECUTION)})
    with pytest.raises(ValueError, match="credential helper"):
        service.create_from_payload({"title": "T", "repository": "https://u:p@example.com/a/b.git",
                                     "execution": dict(EXECUTION)})


def test_gui_create_accepts_repository(tmp_path, home):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from mu.gui.routers import jobs as jobs_router

    bare, _ = make_remote(tmp_path)
    app = FastAPI()
    app.include_router(jobs_router.router, prefix="/api/jobs")
    app.state.job_service = _service(tmp_path)
    with TestClient(app) as client:
        ok = client.post("/api/jobs", json={"title": "T", "repository": f"file://{bare}",
                                            "execution": dict(EXECUTION)})
        assert ok.status_code == 200, ok.text
        job = ok.json()["job"]
        assert job["repository"].startswith(str(home / "repos"))
        assert job["metadata"]["repository_url"] == f"file://{bare}"
        bad = client.post("/api/jobs", json={"title": "T", "repository": "https://u:tok@example.com/a/b.git"})
        assert bad.status_code == 400
        body = bad.json()
        assert body["error"] == {"kind": "credentials_in_url", "host": "example.com"}
        assert "tok" not in body["detail"]


def test_terminal_in_url_uses_clone_and_hidden_session(tmp_path, home, monkeypatch):
    from mu.commands import job as terminal

    bare, work = make_remote(tmp_path)
    service = _service(tmp_path)
    monkeypatch.setattr(terminal, "get_default_job_service", lambda: service)
    monkeypatch.setattr(terminal, "ensure_controller_daemon", lambda: SimpleNamespace(running=True, detail="up"))
    ui = SimpleNamespace(show_info=lambda t: None, show_error=lambda t: None)
    result = terminal.job_cmd(SimpleNamespace(ui=ui), f"Fix it --in file://{bare}", allow_prompt=False)
    assert result.ok, result.message
    job = service.list()[0]
    clone_path = os.path.realpath(clone_mod.clone_path(parse_repo_url(f"file://{bare}")))
    assert job.repository == clone_path
    assert job.metadata["repository_url"] == f"file://{bare}"
    assert job.base_sha == git(work, "rev-parse", "HEAD")
    saved = json.loads((home / "sessions" / job.metadata["origin_session"] / "session.json").read_text())
    assert saved["folder_context"]["folders"] == [clone_path]
    again = terminal.job_cmd(SimpleNamespace(ui=ui), f"create Other --repo file://{bare}", allow_prompt=False)
    assert again.ok, again.message
    assert {j.metadata["origin_session"] for j in service.list()} == {job.metadata["origin_session"]}


# ------------------------------------------------------------------ worktree submodules


def test_job_worktree_initialises_submodules_offline_tolerant(tmp_path, home):
    from mu.jobs.worktree import JobWorktreeManager

    sub_bare, _ = make_remote(tmp_path, name="lib", files={"lib.txt": "lib\n"})
    _, work = make_remote(tmp_path)
    subprocess.run(["git", "-C", str(work), "submodule", "add", "-q", f"file://{sub_bare}", "lib"],
                   check=True, capture_output=True)
    git(work, "commit", "-q", "-m", "add submodule")
    service = _service(tmp_path)
    manager = JobWorktreeManager(service, root=str(tmp_path / "wt"))
    job = service.create_from_payload({"title": "Sub", "workspace_root": str(work), "execution": dict(EXECUTION)})
    info = manager.prepare(job)
    assert (Path(info.worktree) / "lib" / "lib.txt").is_file()
    assert any(e.event_type == "worktree_submodules_ready" for e in service.events(job.id))

    # Remote gone (offline): the worktree is still prepared, with a warning event.
    import shutil
    shutil.rmtree(sub_bare)
    job2 = service.create_from_payload({"title": "Sub 2", "workspace_root": str(work), "execution": dict(EXECUTION)})
    info2 = manager.prepare(job2)
    assert os.path.isdir(info2.worktree)
    assert any(e.event_type == "worktree_submodules_skipped" for e in service.events(job2.id))


# ------------------------------------------------------------------ sandbox git mounts


def _job_worktree(tmp_path):
    _, work = make_remote(tmp_path)
    git(work, "remote", "set-url", "origin", "https://user:ghp_TOKEN@github.com/acme/proj.git")
    hooks = work / ".git" / "hooks"
    (hooks / "pre-commit").write_text("#!/bin/sh\nexit 0\n")
    wt = tmp_path / "wt"
    git(work, "worktree", "add", "-q", "-b", "job", str(wt))
    return work, wt


def test_git_vcs_mounts_are_read_only_and_sanitized(tmp_path, home):
    from mu.jobs.sandbox.policy import GIT_CONTAINER_COMMON, GIT_CONTAINER_GITDIR, git_vcs_mounts

    work, wt = _job_worktree(tmp_path)
    mounts = dict((dst, src) for src, dst in git_vcs_mounts(str(wt)))
    common = os.path.realpath(work / ".git")
    assert mounts[GIT_CONTAINER_GITDIR] == os.path.realpath(common + "/worktrees/wt")
    # objects/refs come from the per-job HEAD-only mirror, never the repository's common dir.
    store = Path(mounts[f"{GIT_CONTAINER_COMMON}/objects"]).parent
    assert mounts[f"{GIT_CONTAINER_COMMON}/refs"] == str(store / "refs")
    assert not any(src.startswith(common + "/") or src == common for src in mounts.values()
                   if src != mounts[GIT_CONTAINER_GITDIR])
    assert not any(dst.endswith("/hooks") or src.endswith("/hooks") for dst, src in mounts.items())
    assert Path(mounts["/workspace/.git"]).read_text() == f"gitdir: {GIT_CONTAINER_GITDIR}\n"
    assert Path(mounts[f"{GIT_CONTAINER_GITDIR}/commondir"]).read_text().strip() == "../.."
    config = Path(mounts[f"{GIT_CONTAINER_COMMON}/config"]).read_text()
    assert "ghp_TOKEN" not in config and "remote" not in config and "url" not in config
    assert "hooksPath = /dev/null" in config and "repositoryformatversion" in config
    assert mounts[f"{GIT_CONTAINER_COMMON}/config"] != common + "/config"


def test_container_args_mount_git_read_only(tmp_path, home, monkeypatch):
    from mu.jobs.sandbox import cli
    from mu.jobs.sandbox.policy import GIT_CONTAINER_GITDIR, SandboxPolicy, container_args

    monkeypatch.setattr(cli, "is_podman", lambda: False)
    monkeypatch.setattr(cli, "selinux_enforcing", lambda: False)
    _, wt = _job_worktree(tmp_path)
    dirs = [tmp_path / n for n in ("input", "output", "relay")]
    for path in dirs:
        path.mkdir()
    args = container_args("n", "implement", str(wt), *map(str, dirs), SandboxPolicy(), "t")
    specs = [a for a in args if a.startswith("type=bind")]
    git_specs = [s for s in specs if "dst=/mucli-git" in s or "dst=/workspace/.git" in s]
    assert any(f"dst={GIT_CONTAINER_GITDIR}," in s for s in git_specs)
    assert git_specs and all(s.endswith(",readonly") for s in git_specs)
    assert sum("dst=/workspace/.git" in s for s in specs) == 1
    # The worktree itself stays writable for implement; the gitdir never is.
    assert any(s.endswith("dst=/workspace") for s in specs)
    rendered = " ".join(args)
    assert "GOFLAGS=-buildvcs=auto" in rendered and "GIT_OPTIONAL_LOCKS=0" in rendered
    assert "ghp_TOKEN" not in rendered and "/hooks" not in rendered


def _stage_vcs_layout(tmp_path, wt, mounts):
    """Copy the mounts to their container paths under a staging root; returns the workspace."""
    import shutil
    root = tmp_path / "ctr"
    for src, dst in sorted(mounts, key=lambda m: len(m[1])):
        target = root / dst.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        if os.path.isdir(src):
            shutil.copytree(src, target, dirs_exist_ok=True)
        else:
            shutil.copyfile(src, target)
    workspace = root / "workspace"
    for item in os.listdir(wt):
        if item != ".git" and (wt / item).is_file():
            shutil.copy(wt / item, workspace / item)
    pointer = workspace / ".git"
    pointer.write_text(pointer.read_text().replace("gitdir: /", f"gitdir: {root}/"))
    return root, workspace


def test_vcs_mirror_exposes_only_the_job_head(tmp_path, home):
    """The mounted object store holds the worktree HEAD commit only: no other branches or
    jobs, no refs/stash (untracked .env), no history (a deleted-but-committed secret)."""
    from mu.jobs.sandbox.policy import GIT_CONTAINER_COMMON, git_vcs_mounts

    work, wt = _job_worktree(tmp_path)
    ident = ["-c", "user.name=t", "-c", "user.email=t@t"]
    (wt / ".env").write_text("SECRET=committed-then-deleted\n")
    git(wt, "add", ".env")
    git(wt, *ident, "commit", "-qm", "oops secret")
    secret_blob = git(wt, "rev-parse", "HEAD:.env")
    git(wt, "rm", "-q", ".env")
    git(wt, *ident, "commit", "-qm", "remove secret")
    git(work, "branch", "other-job", "HEAD")
    (work / "stash.env").write_text("TOKEN=stashed\n")
    git(work, *ident, "stash", "push", "-q", "--include-untracked")
    git(work, "tag", "v1.0")
    stash_sha = git(work, "rev-parse", "refs/stash")
    head = git(wt, "rev-parse", "HEAD")

    mounts = git_vcs_mounts(str(wt))
    by_dst = {dst: src for src, dst in mounts}
    store = Path(by_dst[f"{GIT_CONTAINER_COMMON}/objects"]).parent
    refs = git(store, "--git-dir=.", "for-each-ref", "--format=%(refname) %(objectname)").splitlines()
    assert refs == [f"refs/heads/job {head}"]
    assert (store / "shallow").read_text().split() == [head]
    for missing in (secret_blob, stash_sha, git(work, "rev-parse", "main")):
        probe = subprocess.run(["git", f"--git-dir={store}", "cat-file", "-e", missing], capture_output=True)
        assert probe.returncode != 0, missing
    objects = subprocess.run(["git", f"--git-dir={store}", "rev-list", "--objects", "--all"],
                             capture_output=True, text=True, check=True).stdout
    assert secret_blob not in objects and "stash.env" not in objects
    assert not (store / "objects" / "info" / "alternates").exists()

    root, workspace = _stage_vcs_layout(tmp_path, wt, mounts)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    run = lambda *a: subprocess.run(["git", "-C", str(workspace), *a], capture_output=True, text=True, env=env)
    log = run("log", "--format=%H %s", "-1")
    assert log.returncode == 0 and log.stdout.strip() == f"{head} remove secret", log.stderr
    assert run("status", "--porcelain").returncode == 0
    assert run("show", f"{secret_blob}").returncode != 0
    assert run("show", "HEAD~1:.env").returncode != 0
    assert run("rev-parse", "other-job").returncode != 0 and run("rev-parse", "refs/stash").returncode != 0
    described = run("describe", "--always").stdout.strip()   # no tags in the mirror: falls back to the sha
    assert described and head.startswith(described)

    # HEAD moves (implement committed): the next launch rebuilds the store for the new commit.
    (wt / "new.txt").write_text("x\n")
    git(wt, "add", "new.txt")
    git(wt, *ident, "commit", "-qm", "next")
    store2 = Path(dict((d, s) for s, d in git_vcs_mounts(str(wt)))[f"{GIT_CONTAINER_COMMON}/objects"]).parent
    assert store2 != store
    assert git(store2, "--git-dir=.", "for-each-ref", "--format=%(objectname)") == git(wt, "rev-parse", "HEAD")
    assert subprocess.run(["git", f"--git-dir={store2}", "cat-file", "-e", head], capture_output=True).returncode != 0


def test_vcs_mirror_failure_falls_back_to_the_pointer_with_a_warning(tmp_path, home, monkeypatch):
    from mu.jobs.sandbox import policy

    _, wt = _job_worktree(tmp_path)
    monkeypatch.setattr(policy, "git_object_mirror", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    warnings = []
    assert policy.git_vcs_mounts(str(wt), on_warning=warnings.append) == []
    assert warnings and "boom" in warnings[0]


def test_vcs_mounts_resolve_for_real_git(tmp_path, home):
    """Simulate the container layout on the host (a staged copy at the container paths) and
    check that read-only git commands resolve through the rewritten pointer."""
    import shutil
    from mu.jobs.sandbox.policy import git_vcs_mounts

    _, wt = _job_worktree(tmp_path)
    root = tmp_path / "ctr"
    mounts = git_vcs_mounts(str(wt))
    for src, dst in sorted(mounts, key=lambda m: len(m[1])):
        target = root / dst.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        if os.path.isdir(src):
            shutil.copytree(src, target, dirs_exist_ok=True)
        else:
            shutil.copyfile(src, target)
    # Re-point absolute container paths at the staging root.
    workspace = root / "workspace"
    for item in os.listdir(wt):
        if item != ".git":
            shutil.copy(wt / item, workspace / item) if (wt / item).is_file() else None
    pointer = workspace / ".git"
    pointer.write_text(pointer.read_text().replace("gitdir: /", f"gitdir: {root}/"))
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_CONFIG")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    log = subprocess.run(["git", "-C", str(workspace), "log", "--oneline", "-1"], capture_output=True, text=True,
                         env=env)
    assert log.returncode == 0 and "init" in log.stdout, log.stderr
    status = subprocess.run(["git", "-C", str(workspace), "status", "--porcelain"], capture_output=True,
                            text=True, env=env)
    assert status.returncode == 0, status.stderr
