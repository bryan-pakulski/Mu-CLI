"""Base-drift detection for durable job review branches (Milestone 5).

A job captures ``base_sha`` when it starts. By the time a reviewer looks at
the branch the base branch may have moved. Two questions matter:

* **drifted** — has the base ref advanced past the captured ``base_sha``?
* **mergeable** — does the job branch still merge cleanly onto the *current*
  base tip?  Computed with ``git merge-tree --write-tree`` (Git >= 2.38),
  which needs no worktree and never touches the index.

``assess_base_drift`` is pure (read-only Git); ``apply_base_drift`` records
the assessment as a job event and moves a READY_FOR_REVIEW job to CONFLICTED
when the branch no longer merges — so "ready" never silently means
"ready except it won't merge".
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .models import JobStatus
from .service import JobService


class BaseDriftError(RuntimeError):
    pass


@dataclass(frozen=True)
class BaseDriftReport:
    job_id: str
    base_ref: str
    captured_base_sha: str
    current_base_sha: str
    branch: str
    branch_head_sha: str
    drifted: bool
    commits_behind: int
    mergeable: Optional[bool]
    conflicted_files: List[str] = field(default_factory=list)
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "base_ref": self.base_ref,
            "captured_base_sha": self.captured_base_sha,
            "current_base_sha": self.current_base_sha,
            "branch": self.branch,
            "branch_head_sha": self.branch_head_sha,
            "drifted": self.drifted,
            "commits_behind": self.commits_behind,
            "mergeable": self.mergeable,
            "conflicted_files": list(self.conflicted_files),
            "detail": self.detail,
        }


def _git(repo: str, *args: str, check: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", "-C", repo, *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise BaseDriftError((result.stderr or result.stdout or "git command failed").strip())
    return result


def _repository_root(service: JobService, job) -> str:
    metadata = dict(job.metadata or {})
    repo = str(metadata.get("repository_root") or "").strip()
    if not repo or not os.path.isdir(os.path.expanduser(repo)):
        from .repository import RepositoryRegistry

        try:
            repo = RepositoryRegistry(service.store).register(job.repository).canonical_path
        except Exception as exc:  # noqa: BLE001
            raise BaseDriftError(f"Could not resolve repository for drift check: {exc}") from exc
    return os.path.abspath(os.path.expanduser(repo))


def _base_ref(job) -> str:
    metadata = dict(job.metadata or {})
    ref = str(metadata.get("resolved_base_ref") or "").strip()
    if ref:
        return ref
    submission = metadata.get("submission_repository_preflight")
    if isinstance(submission, dict):
        ref = str(submission.get("current_branch") or submission.get("default_branch") or "").strip()
        if ref:
            return ref
    ref = str(job.base_branch or "").strip()
    if ref and ref.lower() != "auto":
        return ref
    return "main"


def _parse_conflicts(merge_tree_stdout: str) -> List[str]:
    """`git merge-tree --write-tree --name-only` on conflict prints the tree
    OID, one conflicted path per line, then a blank line and informational
    messages. Collect unique paths (also tolerates the non-name-only form)."""
    files: List[str] = []
    for line in merge_tree_stdout.splitlines()[1:]:
        if not line.strip():
            break
        # `--name-only`: bare path per line; without it: "<mode> <oid> <stage>\t<path>".
        path = line.split("\t", 1)[1].strip() if "\t" in line else line.strip()
        if path and path not in files:
            files.append(path)
    return files


def assess_base_drift(service: JobService, job_id: str) -> BaseDriftReport:
    """Read-only: compare the captured base with the current base tip and
    test-merge the job branch onto it."""
    job = service.get(job_id)
    if not job.branch:
        raise BaseDriftError("Job has no managed branch to assess.")
    if not job.base_sha:
        raise BaseDriftError("Job has no captured base commit to compare against.")
    repo = _repository_root(service, job)
    base_ref = _base_ref(job)

    branch_head = _git(repo, "rev-parse", "--verify", f"refs/heads/{job.branch}^{{commit}}").stdout.strip()
    current_base = _git(repo, "rev-parse", "--verify", f"{base_ref}^{{commit}}", check=False)
    if current_base.returncode != 0:
        # Base ref vanished (renamed/deleted). Report as drifted + unknown.
        return BaseDriftReport(
            job_id=job.id, base_ref=base_ref, captured_base_sha=job.base_sha,
            current_base_sha="", branch=job.branch, branch_head_sha=branch_head,
            drifted=True, commits_behind=0, mergeable=None,
            detail=f"Base ref {base_ref!r} no longer resolves in the repository.",
        )
    current_base_sha = current_base.stdout.strip()
    drifted = current_base_sha != job.base_sha
    commits_behind = 0
    if drifted:
        count = _git(repo, "rev-list", "--count", f"{job.base_sha}..{current_base_sha}", check=False)
        try:
            commits_behind = int((count.stdout or "0").strip() or 0)
        except ValueError:
            commits_behind = 0

    merge = _git(repo, "merge-tree", "--write-tree", "--name-only", current_base_sha, branch_head, check=False)
    if merge.returncode == 0:
        mergeable: Optional[bool] = True
        conflicts: List[str] = []
        detail = "Branch merges cleanly onto the current base." if drifted else "Base unchanged since the job started."
    elif merge.returncode == 1:
        mergeable = False
        conflicts = _parse_conflicts(merge.stdout or "")
        detail = (
            f"Branch conflicts with the current base in {len(conflicts)} file(s)."
            if conflicts else "Branch conflicts with the current base."
        )
    else:
        mergeable = None
        conflicts = []
        detail = (merge.stderr or merge.stdout or "merge-tree failed").strip()[:400]
    return BaseDriftReport(
        job_id=job.id, base_ref=base_ref, captured_base_sha=job.base_sha,
        current_base_sha=current_base_sha, branch=job.branch, branch_head_sha=branch_head,
        drifted=drifted, commits_behind=commits_behind, mergeable=mergeable,
        conflicted_files=conflicts, detail=detail,
    )


def apply_base_drift(service: JobService, job_id: str, *, source: str = "review") -> BaseDriftReport:
    """Assess and persist. READY_FOR_REVIEW + not mergeable -> CONFLICTED;
    CONFLICTED + mergeable again (base moved back / branch fixed) -> stays
    CONFLICTED until a human continues (state machine has no direct return to
    READY_FOR_REVIEW; a requeue re-verifies). Always appends a
    ``base_drift_assessed`` event so the receipt/board can show freshness."""
    report = assess_base_drift(service, job_id)
    payload = {**report.to_dict(), "source": source}
    service.store.append_event(job_id, "base_drift_assessed", reason=report.detail, payload=payload)
    job = service.get(job_id)
    metadata = dict(job.metadata or {})
    metadata["base_drift"] = payload
    try:
        service.store.update_runtime_fields(job_id, metadata_json=metadata)
    except Exception:  # noqa: BLE001 — metadata mirror is best-effort
        pass
    if job.status == JobStatus.READY_FOR_REVIEW and report.mergeable is False:
        service.transition(
            job_id,
            JobStatus.CONFLICTED,
            reason="base branch moved; job branch no longer merges cleanly",
            payload=payload,
            attention_detail=(
                f"{report.detail} Base {report.base_ref} advanced {report.commits_behind} commit(s) "
                f"past the job's base. Request changes to rebase, or resolve manually on {report.branch}."
            ),
        )
    return report


__all__ = ["BaseDriftReport", "BaseDriftError", "assess_base_drift", "apply_base_drift"]
