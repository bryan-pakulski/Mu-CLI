"""Git isolation and checkpointing for durable engineering jobs."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from utils.config import HISTORY_DIR

from .models import Job
from .service import JobService


class WorktreeError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorktreeInfo:
    repository: str
    worktree: str
    branch: str
    base_sha: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "repository": self.repository,
            "worktree": self.worktree,
            "branch": self.branch,
            "base_sha": self.base_sha,
        }


class JobWorktreeManager:
    def __init__(self, service: JobService, *, root: Optional[str] = None):
        self.service = service
        self.root = os.path.abspath(
            os.path.expanduser(root or os.path.join(HISTORY_DIR, "jobs", "worktrees"))
        )
        os.makedirs(self.root, exist_ok=True)

    @staticmethod
    def _run(repo: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        result = subprocess.run(
            ["git", "-C", repo, *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            check=False,
        )
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout or "git command failed").strip()
            raise WorktreeError(detail)
        return result

    @classmethod
    def canonical_repo(cls, path: str) -> str:
        candidate = os.path.abspath(os.path.expanduser(str(path or "")))
        if not candidate or not os.path.isdir(candidate):
            raise WorktreeError(f"Repository does not exist: {candidate or path}")
        result = cls._run(candidate, "rev-parse", "--show-toplevel")
        root = (result.stdout or "").strip()
        if not root:
            raise WorktreeError(f"Not a Git repository: {candidate}")
        return os.path.abspath(root)

    @staticmethod
    def _slug(text: str, limit: int = 28) -> str:
        value = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
        return (value or "job")[:limit].rstrip("-")

    def branch_name(self, job: Job) -> str:
        return job.branch or f"mu/job-{job.id[:10]}-{self._slug(job.title)}"

    def worktree_path(self, job: Job) -> str:
        return job.worktree or os.path.join(self.root, job.id)

    def _resolve_base(self, repo: str, job: Job) -> str:
        if job.base_sha:
            result = self._run(repo, "rev-parse", "--verify", f"{job.base_sha}^{{commit}}")
            return (result.stdout or "").strip()
        branch = str(job.base_branch or "main")
        for ref in (branch, f"origin/{branch}"):
            result = self._run(repo, "rev-parse", "--verify", f"{ref}^{{commit}}", check=False)
            if result.returncode == 0 and (result.stdout or "").strip():
                return (result.stdout or "").strip()
        raise WorktreeError(f"Could not resolve base branch {branch!r} in {repo}")

    def _registered_worktrees(self, repo: str) -> Dict[str, Dict[str, str]]:
        result = self._run(repo, "worktree", "list", "--porcelain")
        entries: Dict[str, Dict[str, str]] = {}
        current: Dict[str, str] = {}
        for raw in (result.stdout or "").splitlines() + [""]:
            line = raw.strip()
            if not line:
                path = current.get("worktree")
                if path:
                    entries[os.path.abspath(path)] = dict(current)
                current = {}
                continue
            key, _, value = line.partition(" ")
            current[key] = value
        return entries

    def prepare(self, job: Job) -> WorktreeInfo:
        repo = self.canonical_repo(job.repository)
        base_sha = self._resolve_base(repo, job)
        branch = self.branch_name(job)
        worktree = os.path.abspath(self.worktree_path(job))
        registered = self._registered_worktrees(repo)

        existing = registered.get(worktree)
        if existing:
            head = self._run(worktree, "rev-parse", "--verify", "HEAD^{commit}")
            if not (head.stdout or "").strip():
                raise WorktreeError(f"Registered worktree has no HEAD: {worktree}")
        else:
            if os.path.exists(worktree):
                if os.path.isdir(worktree) and not os.listdir(worktree):
                    os.rmdir(worktree)
                else:
                    raise WorktreeError(
                        f"Refusing to replace unregistered non-empty job worktree: {worktree}"
                    )
            os.makedirs(os.path.dirname(worktree), exist_ok=True)
            branch_exists = self._run(
                repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False
            ).returncode == 0
            if branch_exists:
                self._run(repo, "worktree", "add", worktree, branch)
            else:
                self._run(repo, "worktree", "add", "-b", branch, worktree, base_sha)

        updated = self.service.store.update_runtime_fields(
            job.id,
            branch=branch,
            worktree=worktree,
            base_sha=base_sha,
            metadata_json={
                **dict(job.metadata or {}),
                "repository_root": repo,
                "worktree_managed": True,
            },
        )
        self.service.store.append_event(
            job.id,
            "worktree_ready",
            payload={
                "repository": repo,
                "worktree": updated.worktree,
                "branch": updated.branch,
                "base_sha": updated.base_sha,
            },
        )
        return WorktreeInfo(repo, updated.worktree, updated.branch, updated.base_sha)

    def checkpoint(self, job: Job, *, label: str) -> Optional[str]:
        worktree = str(job.worktree or "")
        if not worktree or not os.path.isdir(worktree):
            return None
        status = self._run(worktree, "status", "--porcelain")
        if not (status.stdout or "").strip():
            head = self._run(worktree, "rev-parse", "--verify", "HEAD^{commit}")
            return (head.stdout or "").strip() or None

        self._run(worktree, "add", "-A")
        env = os.environ.copy()
        env.setdefault("GIT_AUTHOR_NAME", "MuCLI")
        env.setdefault("GIT_AUTHOR_EMAIL", "mucli@localhost")
        env.setdefault("GIT_COMMITTER_NAME", "MuCLI")
        env.setdefault("GIT_COMMITTER_EMAIL", "mucli@localhost")
        result = subprocess.run(
            ["git", "-C", worktree, "commit", "--no-gpg-sign", "-m", f"mu checkpoint: {label}"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            env=env,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "checkpoint commit failed").strip()
            raise WorktreeError(detail)
        head = self._run(worktree, "rev-parse", "--verify", "HEAD^{commit}")
        sha = (head.stdout or "").strip()
        self.service.store.append_event(
            job.id,
            "checkpoint_created",
            payload={"sha": sha, "label": label, "branch": job.branch, "worktree": worktree},
        )
        return sha

    def remove(self, job: Job, *, force: bool = False) -> bool:
        repo = self.canonical_repo(job.repository)
        worktree = str(job.worktree or self.worktree_path(job))
        if not os.path.exists(worktree):
            return False
        args: List[str] = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(worktree)
        self._run(repo, *args)
        if os.path.exists(worktree) and force:
            shutil.rmtree(worktree, ignore_errors=True)
        self.service.store.append_event(
            job.id,
            "worktree_removed",
            payload={"worktree": worktree, "force": bool(force)},
        )
        return True
