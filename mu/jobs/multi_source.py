"""Jobs on several sources: one primary plus editable extra sources.

A job runs in its primary source (``job.repository``, ``/workspace`` in the sandbox) as before.
``sources`` in the create payload adds more — folders, repositories (Settings list or URL),
sessions' workspaces, in the ``source`` shape plus an optional ``alias`` (the mount name) — each
resolved like the primary: a URL is cloned, a plain folder is
snapshotted (mu/jobs/folder_snapshot.py), a git repository starts from its current HEAD. They are
kept in ``job.metadata.sources`` (name, kind, input, repository, base_branch, base_sha, and after
preparation worktree and branch; after publishing pr/pushed_sha).

Every extra source gets its own worktree on the job's branch name and is mounted at
``/sources/<name>``: writable in the stages that write (implement, checks, repair), read-only
otherwise, with its ``.git`` pointer mounted read-only so the agent cannot redirect the host's git.
The host guard, checkpoints, the diff (paths prefixed ``sources/<name>/``) and the review cover
every source; push opens one PR per source that has a remote, and a folder's change comes back as
its own patch (``GET /api/jobs/<id>/patch?source=<name>``).

Most of the pipeline is reused through :func:`view`: the job as seen from one source (its
repository, worktree, branch and base swapped in), which the single-repository code accepts.
"""

from __future__ import annotations

import dataclasses
import os
import re
from typing import Any, Dict, List, Optional, Tuple

MAX_SOURCES = 8
MOUNT_ROOT = "/sources"
WRITABLE_STAGES = ("implement", "checks", "repair")
_RESERVED = {"workspace", "attachments", "artifacts", "output", "relay", "cache"}
# Primary-only metadata a source view must not inherit (it would name the primary's repo or PR).
_PRIMARY_KEYS = ("repository_root", "pr", "pushed_sha", "verified_sha", "folder_snapshot", "repository_url",
                 "repository_clone", "repository_warnings", "submission_repository_preflight", "resolved_base_ref",
                 "review_branch", "review_head_sha", "repository_default_branch", "sources")


def _slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9._-]+", "-", str(text or "").lower()).strip("-.")[:40]
    return value or "source"


def sources(job: Any) -> List[Dict[str, Any]]:
    """The job's extra sources (``metadata.sources``), never the primary."""
    items = (getattr(job, "metadata", None) or {}).get("sources") or []
    return [dict(s) for s in items if isinstance(s, dict) and s.get("name") and s.get("repository")]


def view(job: Any, source: Dict[str, Any]):
    """``job`` as seen from one extra source: its repository, worktree, branch and base."""
    meta = {k: v for k, v in dict(job.metadata or {}).items() if k not in _PRIMARY_KEYS}
    meta["repository_root"] = source["repository"]
    for key in ("pr", "pushed_sha", "folder_snapshot", "repository_url"):
        if source.get(key):
            meta[key] = source[key]
    meta["source_name"] = source["name"]
    return dataclasses.replace(job, repository=source["repository"], worktree=str(source.get("worktree") or ""),
                               branch=str(source.get("branch") or ""), base_branch=str(source.get("base_branch") or ""),
                               base_sha=str(source.get("base_sha") or ""), metadata=meta)


def mount_path(source: Dict[str, Any]) -> str:
    return f"{MOUNT_ROOT}/{source['name']}"


# ------------------------------------------------------------------------------- creation

def _identity(path: str) -> str:
    """A repository's identity (its git common dir), or the real path of a non-git folder."""
    from .repository import RepositoryError, RepositoryRegistry
    try:
        return str(RepositoryRegistry.inspect(path).get("git_common_dir") or os.path.realpath(path))
    except RepositoryError:
        return os.path.realpath(path)


def resolve(items: Any, *, primary: str) -> List[Dict[str, Any]]:
    """Validate and resolve a payload's ``sources`` (raises ``ValueError``; clones and snapshots)."""
    if items in (None, ""):
        return []
    if not isinstance(items, list):
        raise ValueError("sources must be a list")
    if len(items) > MAX_SOURCES:
        raise ValueError(f"a job takes at most {MAX_SOURCES} extra sources")
    from .clone import apply_repository
    from .folder_snapshot import BRANCH, ensure_snapshot
    from .repository import RepositoryError, RepositoryRegistry
    from .sources import apply_source

    seen = {_identity(primary)} if primary else set()
    names: set = set()
    out: List[Dict[str, Any]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"sources[{index}] must be an object")
        # ``alias`` names the mount (/sources/<alias>); ``name`` stays the Settings repository's name.
        spec = {k: v for k, v in item.items() if k not in ("alias", "include_conversation")}
        resolved = apply_repository(apply_source({"source": spec}))  # CloneError is a ValueError
        path = str(resolved.get("workspace_root") or "").strip()
        if not path or not os.path.isdir(path):
            raise ValueError(f"sources[{index}]: {path or 'source'} does not exist")
        meta = dict(resolved.get("metadata") or {})
        label = str(meta.get("source", {}).get("name") or meta.get("source", {}).get("session")
                    or meta.get("repository_url") or path)
        entry: Dict[str, Any] = {"kind": str(spec.get("kind") or ""), "input": label}
        if meta.get("repository_url"):
            entry["repository_url"] = meta["repository_url"]
        try:
            info = RepositoryRegistry.inspect(path)
            entry.update(repository=path,
                         base_branch=str(resolved.get("base_branch") or info.get("current_branch") or info.get("default_branch") or "main"),
                         base_sha=str(resolved.get("base_sha") or info.get("head_sha") or ""))
            if not entry["base_sha"]:
                raise ValueError(f"sources[{index}]: could not resolve the current commit of {path}")
        except RepositoryError as exc:
            if "not a git repository" not in str(exc).lower():
                raise ValueError(f"sources[{index}]: {exc}") from exc
            snap = ensure_snapshot(path)
            entry.update(repository=snap.path, base_branch=BRANCH, base_sha=snap.base_sha,
                         folder_snapshot=snap.metadata())
        identity = _identity(entry["repository"])
        if identity in seen:
            raise ValueError(f"sources[{index}]: {label} is already a source of this job")
        seen.add(identity)
        base_name = _slug(item.get("alias") or spec.get("name") or os.path.basename(str(meta.get("repository_url") or path).rstrip("/")).removesuffix(".git"))
        name, n = base_name, 2
        while name in names or name in _RESERVED:
            name, n = f"{base_name}-{n}", n + 1
        names.add(name)
        entry["name"] = name
        out.append(entry)
    return out


# ---------------------------------------------------------------------------- preparation

def worktree_path(manager, job, source: Dict[str, Any]) -> str:
    # The final component is the job id so the manager's removal identity checks accept it.
    parent = os.path.join(manager.root, job.id, "sources") if manager._per_job_layout \
        else os.path.join(manager.root, f"{job.id}-sources")
    return os.path.join(parent, source["name"], job.id)


def prepare(service, manager, job) -> List[Dict[str, Any]]:
    """Create (or reattach) each extra source's worktree on the job's branch name."""
    items = sources(job)
    if not items:
        return []
    branch = manager.branch_name(job)
    changed = False
    for source in items:
        path = str(source.get("worktree") or "") or worktree_path(manager, job, source)
        repo = source["repository"]
        attached = os.path.realpath(path) in {os.path.realpath(p) for p in manager._registered_worktrees(repo)}
        if not (attached and os.path.isdir(path)):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            exists = manager._run(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False,
                                  stage="source_branch").returncode == 0
            if exists:  # a retry keeps the work already committed on the branch
                manager._run(repo, "worktree", "add", path, branch, stage="source_worktree_add")
            else:
                manager._run(repo, "worktree", "add", "-b", branch, path, source["base_sha"], stage="source_worktree_add")
            try:
                manager._init_submodules(job.id, path)
            except Exception:  # noqa: BLE001 - offline submodules never stop the job
                pass
            changed = True
        if source.get("worktree") != path or source.get("branch") != branch:
            source.update(worktree=path, branch=branch)
            changed = True
    if changed:
        service.store.patch_metadata(job.id, {"sources": items})
        service.store.append_event(job.id, "sources_ready", payload={
            "sources": [{"name": s["name"], "worktree": s["worktree"], "branch": s["branch"], "base_sha": s["base_sha"]}
                        for s in items]})
    return items


def update_source(service, job_id: str, name: str, **fields: Any) -> None:
    job = service.get(job_id)
    items = sources(job)
    for source in items:
        if source["name"] == name:
            source.update(fields)
    service.store.patch_metadata(job_id, {"sources": items})


# ------------------------------------------------------------------------------- sandbox

def container_mounts(job, stage: str) -> List[Tuple[str, str, bool]]:
    """Bind mounts of the extra sources for a stage or checks container (after the pre-check)."""
    from .sandbox.policy import SandboxGuardError, workspace_precheck
    mounts: List[Tuple[str, str, bool]] = []
    for source in sources(job):
        worktree = str(source.get("worktree") or "")
        if not worktree or not os.path.isdir(worktree):
            continue
        try:
            workspace_precheck(worktree)
        except SandboxGuardError as exc:
            raise SandboxGuardError(f"source {source['name']}: {exc}") from exc
        target = mount_path(source)
        mounts.append((worktree, target, stage not in WRITABLE_STAGES))
        pointer = os.path.join(worktree, ".git")
        if os.path.isfile(pointer) and not os.path.islink(pointer):
            mounts.append((pointer, f"{target}/.git", True))  # the host owns this source's git
    return mounts


def container_paths(job) -> List[str]:
    return [mount_path(s) for s in sources(job) if s.get("worktree") and os.path.isdir(str(s["worktree"]))]


def prompt_lines(job) -> List[str]:
    items = sources(job)
    if not items:
        return []
    lines = ["", "Sources: the primary is /workspace. Extra sources, each its own repository (edit them where the task "
                 "needs it; their changes are committed, reviewed and published separately):"]
    for s in items:
        kind = "folder (its change becomes a patch)" if s.get("folder_snapshot") else (s.get("repository_url") or s.get("input") or "")
        lines.append(f"- {mount_path(s)}: {s.get('input')} — {kind}, base {s.get('base_branch') or 'HEAD'}")
    return lines


# ---------------------------------------------------------------------- guard, checkpoints

def guard(job, *, literals=()) -> None:
    """Host guard for every extra source (the primary is guarded by the caller)."""
    from .sandbox.policy import SandboxGuardError, guard_policy
    from .sandbox.runner import _guard
    for source in sources(job):
        if not source.get("worktree") or not os.path.isdir(str(source["worktree"])):
            continue
        sub = view(job, source)
        try:
            _guard(sub, guard_policy(sub, "implement"), literals=literals)
        except SandboxGuardError as exc:
            raise SandboxGuardError(f"source {source['name']}: {exc}") from exc


def checkpoint(manager, job, *, label: str) -> Dict[str, str]:
    """Commit every extra source's worktree; returns ``{name: head sha}``."""
    heads: Dict[str, str] = {}
    for source in sources(job):
        if source.get("worktree") and os.path.isdir(str(source["worktree"])):
            sha = manager.checkpoint(view(job, source), label=label)
            if sha:
                heads[source["name"]] = sha
    return heads


def source_diff(job, source: Dict[str, Any], *, head: str = "", prefixed: bool = True, binary: bool = False) -> str:
    """One extra source's change against its base (paths ``sources/<name>/...`` when prefixed)."""
    from .worktree import JobWorktreeManager
    repo = source["repository"]
    ref = head or str(source.get("branch") or "")
    if not ref or not source.get("base_sha"):
        return ""
    args = ["diff", "--no-ext-diff", "--no-color", "--find-renames"]
    if binary:
        args += ["--binary", "--full-index"]
    if prefixed:
        args += [f"--src-prefix=a/sources/{source['name']}/", f"--dst-prefix=b/sources/{source['name']}/"]
    result = JobWorktreeManager._run(repo, *args, f"{source['base_sha']}...{ref}", "--", check=False, stage="source_diff")
    return result.stdout or "" if result.returncode == 0 else ""


def combined_diff(job) -> str:
    """Every extra source's diff, prefixed, to append to the primary's."""
    return "".join(source_diff(job, s) for s in sources(job))


# --------------------------------------------------------------------------------- publish

def publish(service, job, *, run, post, env, get=None) -> Optional[str]:
    """Open/update one draft PR per extra source with a remote and changes; returns an error or None.

    Folders and repositories without a remote keep their change as a patch."""
    from .publish import PublishError, open_draft_pr
    from .worktree import JobWorktreeManager
    for source in sources(job):
        if source.get("folder_snapshot"):
            continue
        repo = source["repository"]
        remote = JobWorktreeManager._run(repo, "remote", check=False, stage="source_remote")
        if not (remote.stdout or "").strip():
            continue
        head = JobWorktreeManager._run(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{source.get('branch') or ''}",
                                       check=False, stage="source_head")
        sha = (head.stdout or "").strip()
        if not sha or sha == source.get("base_sha"):
            continue  # nothing changed in this source
        name = source["name"]
        try:
            pr = open_draft_pr(view(job, source), run=run, post=post, env=env, verified_head=sha, get=get,
                               base_sha=str(source.get("base_sha") or ""),
                               on_pushed=lambda info, n=name: update_source(service, job.id, n, **info))
        except (PublishError, Exception) as exc:  # noqa: BLE001 - reported on the parked job
            return f"source {name}: {exc}"
        updated = bool(pr.pop("updated", False))
        update_source(service, job.id, name, pr=pr)
        service.store.append_event(job.id, "pr_updated" if updated else "pr_opened", reason=pr.get("url", ""),
                                   payload={**pr, "source": name})
    return None


# --------------------------------------------------------------------------------- cleanup

def remove(service, job) -> None:
    """Remove the extra sources' worktrees and managed branches (archive / delete). Best effort."""
    from .worktree import JobWorktreeManager, delete_job_branches
    for source in sources(job):
        sub = view(job, source)
        try:
            if sub.worktree and os.path.exists(sub.worktree):
                JobWorktreeManager(service).remove(sub)
            delete_job_branches(service, sub)
        except Exception as exc:  # noqa: BLE001 - a dirty source keeps its worktree; never blocks cleanup
            service.store.append_event(job.id, "source_cleanup_failed", reason=str(exc)[:300],
                                       payload={"source": source["name"]})
