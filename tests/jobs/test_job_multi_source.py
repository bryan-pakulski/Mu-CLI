"""Jobs on several editable sources (mu/jobs/multi_source.py): a primary plus extra folders and
repositories, each with its own worktree, guard, checkpoint, diff, and PR or patch."""

from __future__ import annotations

import os
import subprocess

import pytest

from mu.jobs import JobService, JobStore, job_settings
from mu.jobs import multi_source
from mu.jobs.models import JobStatus
from mu.jobs.worktree import JobWorktreeManager

EXE = {"provider": "ollama", "model": "small"}


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("MUCLI_HOME", str(home))
    import utils.config
    monkeypatch.setattr(utils.config, "HISTORY_DIR", str(home))


def git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True).stdout.strip()


def repo(path, files=None):
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.name", "Dev")
    git(path, "config", "user.email", "dev@example.com")
    git(path, "config", "commit.gpgsign", "false")
    for rel, text in (files or {"README.md": "x\n"}).items():
        (path / rel).write_text(text)
    git(path, "add", ".")
    git(path, "commit", "-qm", "base")
    return path


def setup(tmp_path):
    primary = repo(tmp_path / "app", {"app.py": "print('app')\n"})
    lib = repo(tmp_path / "lib", {"lib.py": "VALUE = 1\n"})
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "todo.txt").write_text("one\n")
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    job = service.create_from_payload({"title": "Cross-repo change", "workspace_root": str(primary), "execution": EXE,
                                       "sources": [{"kind": "folder", "path": str(lib)},
                                                   {"kind": "folder", "path": str(notes), "alias": "Notes"}]})
    manager = JobWorktreeManager(service, root=str(tmp_path / "worktrees"))
    manager.prepare(job)
    multi_source.prepare(service, manager, service.get(job.id))
    return service, manager, service.get(job.id), primary, lib, notes


def test_sources_are_resolved_named_and_prepared_on_the_job_branch(tmp_path):
    service, manager, job, primary, lib, notes = setup(tmp_path)
    items = multi_source.sources(job)
    assert [s["name"] for s in items] == ["lib", "notes"]
    lib_src, notes_src = items
    assert lib_src["repository"] == str(lib) and lib_src["base_sha"] == git(lib, "rev-parse", "HEAD")
    assert notes_src["folder_snapshot"]["source_path"] == str(notes.resolve())  # a plain folder is snapshotted
    for src in items:
        assert src["branch"] == job.branch
        assert git(src["worktree"], "rev-parse", "--abbrev-ref", "HEAD") == job.branch
        assert os.path.basename(src["worktree"]) == job.id
    # Preparing again (a retry) reuses the worktrees.
    again = multi_source.prepare(service, manager, service.get(job.id))
    assert [s["worktree"] for s in again] == [s["worktree"] for s in items]


def test_bad_sources_reject_creation(tmp_path):
    primary = repo(tmp_path / "app")
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    base = {"title": "t", "workspace_root": str(primary), "execution": EXE}
    for sources, message in (
        ([{"kind": "folder", "path": str(primary)}], "already a source"),
        ([{"kind": "repository", "name": "nope"}], "no repository named"),
        ([{"kind": "folder", "path": str(tmp_path / "gone")}], "does not exist"),
        ([{"kind": "folder", "path": str(tmp_path)}] * 9, "at most 8"),
        ("lib", "must be a list"),
    ):
        with pytest.raises(ValueError, match=message):
            service.create_from_payload({**base, "sources": sources})
    assert service.list() == []


def test_names_are_unique_and_settings_repositories_work(tmp_path):
    primary = repo(tmp_path / "app")
    one, two = repo(tmp_path / "a" / "lib"), repo(tmp_path / "b" / "lib")
    job_settings.save_settings({"jobs": {"repositories": [{"name": "shared", "url": str(two)}]}})
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    job = service.create_from_payload({"title": "t", "workspace_root": str(primary), "execution": EXE,
                                       "sources": [{"kind": "folder", "path": str(one)}, {"kind": "repository", "name": "shared"}]})
    # Both folders are called lib: the second mount gets a distinct name; ``name`` picked the Settings row.
    assert [s["name"] for s in multi_source.sources(job)] == ["lib", "shared"]
    assert multi_source.sources(job)[1]["repository"] == str(two)
    dup = service.create_from_payload({"title": "t", "workspace_root": str(primary), "execution": EXE,
                                       "sources": [{"kind": "folder", "path": str(one)}, {"kind": "folder", "path": str(two)}]})
    assert [s["name"] for s in multi_source.sources(dup)] == ["lib", "lib-2"]


def test_every_source_is_checkpointed_diffed_and_patched(tmp_path):
    from mu.jobs.core.artifacts import artifacts_for
    from mu.jobs.review import build_job_diff, build_job_patch
    from mu.jobs.worker import _checkpoint

    service, manager, job, primary, lib, notes = setup(tmp_path)
    lib_src, notes_src = multi_source.sources(job)
    with open(os.path.join(job.worktree, "app.py"), "w") as handle:
        handle.write("print('app v2')\n")
    with open(os.path.join(lib_src["worktree"], "lib.py"), "w") as handle:
        handle.write("VALUE = 2\n")
    with open(os.path.join(notes_src["worktree"], "todo.txt"), "w") as handle:
        handle.write("one\ntwo\n")
    assert _checkpoint(manager, service, job.id, "implement")
    # Each source's change is committed on its own job branch; the source folders are untouched.
    assert git(lib_src["worktree"], "log", "-1", "--format=%s") == "mu checkpoint: implement"
    assert (lib / "lib.py").read_text() == "VALUE = 1\n" and (notes / "todo.txt").read_text() == "one\n"
    patch = artifacts_for(job).read_text("diff.patch")
    assert "b/app.py" in patch and "b/sources/lib/lib.py" in patch and "b/sources/notes/todo.txt" in patch
    diff = build_job_diff(service, job.id)
    assert {"sources/lib/lib.py", "sources/notes/todo.txt"} <= set(diff.files) and "app.py" in diff.files
    # A folder source's own patch applies in that folder.
    notes_patch = tmp_path / "notes.patch"
    notes_patch.write_text(build_job_patch(service, job.id, source="notes"))
    subprocess.run(["git", "apply", str(notes_patch)], cwd=notes, check=True, capture_output=True)
    assert (notes / "todo.txt").read_text() == "one\ntwo\n"


def test_a_secret_in_an_extra_source_blocks_the_checkpoint(tmp_path):
    from mu.jobs.worker import _checkpoint

    service, manager, job, *_ = setup(tmp_path)
    lib_src = multi_source.sources(job)[0]
    with open(os.path.join(lib_src["worktree"], ".env"), "w") as handle:
        handle.write("TOKEN=abc\n")
    blocked = []
    assert _checkpoint(manager, service, job.id, "implement", blocked=blocked) is None
    assert blocked and "source lib" in str(blocked[0])


def test_sandbox_mounts_follow_the_stage_and_protect_git(tmp_path):
    from mu.jobs.sandbox.policy import SandboxPolicy, container_args

    service, manager, job, *_ = setup(tmp_path)
    implement = multi_source.container_mounts(job, "implement")
    plan = multi_source.container_mounts(job, "plan")
    assert [(dst, ro) for _, dst, ro in implement] == [("/sources/lib", False), ("/sources/lib/.git", True),
                                                       ("/sources/notes", False), ("/sources/notes/.git", True)]
    assert all(ro for _, _, ro in plan)
    dirs = [tmp_path / n for n in ("in", "out", "relay")]
    for d in dirs:
        d.mkdir()
    args = container_args("c", "implement", job.worktree, *map(str, dirs), SandboxPolicy.for_stage("implement"), "t",
                          extra_mounts=tuple(implement))
    joined = " ".join(args)
    assert "/sources/lib" in joined and "/sources/notes/.git" in joined
    with pytest.raises(ValueError, match="unsupported source mount"):
        container_args("c", "implement", job.worktree, *map(str, dirs), SandboxPolicy.for_stage("implement"), "t",
                       extra_mounts=((job.worktree, "/etc", False),))
    with pytest.raises(ValueError, match="must be read-only"):
        container_args("c", "implement", job.worktree, *map(str, dirs), SandboxPolicy.for_stage("implement"), "t",
                       extra_mounts=((os.path.join(job.worktree, ".git"), "/sources/lib/.git", False),))


def test_prompts_name_every_source(tmp_path):
    from mu.jobs.runner import implement_prompt
    from mu.jobs.stages import build_plan_prompt

    service, _, job, *_ = setup(tmp_path)
    for prompt in (build_plan_prompt(job), implement_prompt(service, job)):
        assert "/sources/lib" in prompt and "/sources/notes" in prompt and "becomes a patch" in prompt


def test_push_opens_a_pr_per_source_with_a_remote(tmp_path):
    from mu.jobs.publish import publish_job
    from mu.jobs.verification import VerificationRun, VerificationStore

    primary = repo(tmp_path / "app", {"app.py": "1\n"})
    lib = repo(tmp_path / "lib", {"lib.py": "1\n"})
    mirrors = {}
    for name, path in (("app", primary), ("lib", lib)):
        bare = tmp_path / f"{name}.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
        git(path, "remote", "add", "origin", f"git@github.com:acme/{name}.git")
        git(path, "remote", "add", "mirror", str(bare))
        mirrors[name] = bare
    job_settings.save_settings({"publish": {"push_remote": "mirror"}})
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    job = service.create_from_payload({"title": "Bump both", "workspace_root": str(primary), "sources": [
        {"kind": "folder", "path": str(lib)}], "execution": {**EXE, "publish_mode": "github"}})
    manager = JobWorktreeManager(service, root=str(tmp_path / "worktrees"))
    manager.prepare(job)
    multi_source.prepare(service, manager, service.get(job.id))
    job = service.get(job.id)
    for worktree, rel in ((job.worktree, "app.py"), (multi_source.sources(job)[0]["worktree"], "lib.py")):
        with open(os.path.join(worktree, rel), "w") as handle:
            handle.write("2\n")
    from mu.jobs.worker import _checkpoint
    head = _checkpoint(manager, service, job.id, "implement")
    VerificationStore(service.store).save(VerificationRun(
        id="ok", job_id=job.id, status="passed", passed=True, started_at=1, finished_at=2, duration_ms=1,
        base_sha=job.base_sha, head_sha=head, branch=job.branch, worktree=job.worktree))
    for status in (JobStatus.PREPARING, JobStatus.RUNNING, JobStatus.VERIFYING, JobStatus.REVIEWING, JobStatus.PUBLISHING):
        if service.get(job.id).status != status:
            service.transition(job.id, status, reason="test")

    posts = []

    def run(argv, cwd):
        if argv[0] == "git":
            if argv[1] in {"fetch", "ls-remote", "push"} and "origin" in argv:
                return 128, "", "origin is offline in tests"
            proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
            return proc.returncode, proc.stdout, proc.stderr
        return 1, "", "unexpected"

    def post(url, headers, body):
        posts.append(url)
        number = len(posts)
        return {"html_url": f"https://github.com/acme/x/pull/{number}", "number": number}

    assert publish_job(service, job.id, run=run, post=post, env={"GITHUB_TOKEN": "t"}) == 0, service.get(job.id).attention_detail
    assert sorted(posts) == ["https://api.github.com/repos/acme/app/pulls", "https://api.github.com/repos/acme/lib/pulls"]
    job = service.get(job.id)
    assert git(mirrors["lib"], "rev-parse", f"refs/heads/{job.branch}")
    lib_src = multi_source.sources(job)[0]
    assert lib_src["pr"]["url"].startswith("https://github.com/acme/x/pull/") and lib_src.get("pushed_sha")
    assert job.metadata["pr"]["url"] and job.metadata["pr"]["url"] != lib_src["pr"]["url"]


def test_archiving_removes_the_extra_worktrees(tmp_path):
    from mu.jobs.management import JobManagementService

    service, manager, job, *_ = setup(tmp_path)
    worktrees = [s["worktree"] for s in multi_source.sources(job)]
    service.transition(job.id, JobStatus.CANCELLED, reason="test")
    JobManagementService(service).archive(job.id, reason="done")
    assert not any(os.path.exists(w) for w in worktrees)


def test_checks_may_run_inside_an_extra_source_only(tmp_path):
    from mu.jobs.checks import resolve_suites

    service, _, job, *_ = setup(tmp_path)

    def suites_with(cwd):
        return resolve_suites(job, {"checks": {"suites": [{"name": "lib", "command": "pytest -q", "cwd": cwd}]}})
    assert any(s.cwd == "/sources/lib/tests" for s in suites_with("/sources/lib/tests"))
    for bad in ("/sources/other", "/sources/lib/../../etc", "/etc", "/sources"):
        with pytest.raises(ValueError, match="cwd must be"):
            suites_with(bad)
