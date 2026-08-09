"""Shared review/attention actions for durable engineering jobs.

GUI, TUI and mobile call this layer so review feedback, blocker responses and
job diff semantics stay identical across control planes.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict, dataclass
from typing import Any, Dict, List

from .models import JobStatus
from .repository import RepositoryRegistry
from .service import JobService


class JobReviewError(RuntimeError):
    pass


@dataclass(frozen=True)
class JobDiff:
    job_id: str
    base_sha: str
    head_sha: str
    branch: str
    files: List[str]
    stat: str
    patch: str
    truncated: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _git(repo: str, *args: str, timeout: int = 60) -> str:
    result = subprocess.run(
        ["git", "-C", repo, *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=timeout,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "git command failed").strip()
        raise JobReviewError(detail)
    return result.stdout or ""


def _review_target(service: JobService, job) -> tuple[str, str]:
    """Return (git repository/path, head ref) for review.

    Completed jobs are reviewed from their durable branch, so the temporary
    execution worktree can be removed.  A worktree/HEAD fallback remains for
    older or partially-prepared jobs that do not yet have a managed branch.
    """

    if job.branch:
        metadata = dict(job.metadata or {})
        repo = str(metadata.get("repository_root") or "").strip()
        if not repo or not os.path.isdir(os.path.expanduser(repo)):
            try:
                repo = RepositoryRegistry(service.store).register(job.repository).canonical_path
            except Exception as exc:
                raise JobReviewError(f"Could not resolve repository for review branch: {exc}") from exc
        repo = os.path.abspath(os.path.expanduser(repo))
        try:
            _git(repo, "rev-parse", "--verify", f"refs/heads/{job.branch}^{{commit}}")
        except JobReviewError as exc:
            raise JobReviewError(
                f"Review branch {job.branch!r} is not available in the repository: {exc}"
            ) from exc
        return repo, job.branch

    worktree = os.path.abspath(os.path.expanduser(str(job.worktree or "")))
    if worktree and os.path.isdir(worktree):
        return worktree, "HEAD"
    raise JobReviewError("This job does not have an available review branch yet.")


def build_job_diff(service: JobService, job_id: str, *, max_chars: int = 500_000) -> JobDiff:
    job = service.get(job_id)
    if not job.base_sha:
        raise JobReviewError("This job does not have a captured base commit yet.")

    repo, head_ref = _review_target(service, job)
    head_sha = _git(repo, "rev-parse", f"{head_ref}^{{commit}}").strip()
    diff_range = f"{job.base_sha}...{head_ref}"
    files = [
        line.strip()
        for line in _git(repo, "diff", "--name-only", diff_range).splitlines()
        if line.strip()
    ]
    stat = _git(repo, "diff", "--stat", diff_range).strip()
    patch = _git(
        repo,
        "diff",
        "--no-ext-diff",
        "--no-color",
        "--find-renames",
        diff_range,
        "--",
        timeout=120,
    )
    limit = max(10_000, int(max_chars))
    truncated = len(patch) > limit
    if truncated:
        patch = patch[:limit] + "\n\n... [diff truncated by MuCLI review surface] ...\n"
    return JobDiff(
        job_id=job.id,
        base_sha=job.base_sha,
        head_sha=head_sha,
        branch=job.branch,
        files=files,
        stat=stat,
        patch=patch,
        truncated=truncated,
    )


class JobReviewService:
    def __init__(self, service: JobService):
        self.service = service

    def _interaction_context(self, job_id: str) -> Dict[str, Any]:
        for event in reversed(self.service.events(job_id)):
            if event.to_status == JobStatus.NEEDS_HUMAN:
                return dict(event.payload or {})
        return {}

    def _set_validation_commands(self, job_id: str, values: List[str]) -> None:
        commands = [str(value).strip() for value in values if str(value).strip()]
        if not commands:
            raise JobReviewError("At least one validation command is required to resolve this gate.")
        now = float(self.service.store._clock())
        with self.service.store._transaction() as conn:
            cursor = conn.execute(
                "UPDATE jobs SET validation_json = ?, updated_at = ?, version = version + 1 WHERE id = ?",
                (json.dumps(commands, ensure_ascii=False), now, job_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(job_id)
        self.service.store.append_event(
            job_id,
            "verification_contract_updated",
            payload={"validation_commands": commands},
        )

    def respond(
        self,
        job_id: str,
        *,
        detail: str = "",
        decision: str = "",
        value: Any = None,
        selected: List[Any] | None = None,
    ):
        job = self.service.get(job_id)
        if job.status != JobStatus.NEEDS_HUMAN:
            raise JobReviewError("Only a job waiting for human input can be answered.")
        context = self._interaction_context(job_id)
        response = {
            "kind": job.attention_reason.value or "question",
            "decision": str(decision or "").strip().lower(),
            "detail": str(detail or "").strip(),
            "value": value,
            "selected": list(selected or []),
            "target": context,
        }
        if job.attention_reason.value == "approval_required":
            if response["decision"] not in {"approve", "deny", "explain"}:
                raise JobReviewError("Approval responses require decision=approve, deny, or explain.")
            if response["decision"] == "approve" and context.get("can_approve") is False:
                raise JobReviewError("This tool request cannot be approved because its modification preview failed.")
        if job.attention_reason.value == "verification_required":
            commands: List[str] = []
            if isinstance(value, list):
                commands.extend(value)
            elif isinstance(value, str):
                commands.extend(value.splitlines())
            commands.extend(response["selected"])
            if response["detail"]:
                commands.extend(response["detail"].splitlines())
            # Deduplicate while preserving reviewer order.
            seen = set()
            commands = [c for c in commands if not (c in seen or seen.add(c))]
            self._set_validation_commands(job_id, commands)
            response["value"] = commands
        if not response["decision"] and value is None and not response["selected"] and not response["detail"]:
            raise JobReviewError("A response value, decision, selection, or detail is required.")
        self.service.store.append_event(job_id, "interaction_response", payload=response)
        return self.service.resume(
            job_id,
            detail=response["detail"] or str(response.get("value") or response["decision"] or response["selected"]),
        )

    def request_changes(self, job_id: str, feedback: str):
        job = self.service.get(job_id)
        if job.status != JobStatus.READY_FOR_REVIEW:
            raise JobReviewError("Changes can only be requested for a job that is ready for review.")
        text = str(feedback or "").strip()
        if not text:
            raise JobReviewError("Review feedback is required.")
        self.service.store.append_event(
            job_id,
            "review_feedback",
            reason="reviewer requested changes",
            payload={"detail": text},
        )
        self.service.store.append_event(
            job_id,
            "human_response",
            payload={"detail": text, "source": "review_feedback"},
        )
        return self.service.transition(
            job_id,
            JobStatus.QUEUED,
            reason="review changes requested; requeued",
            payload={"feedback": text, "branch": job.branch},
        )

    def continue_job(self, job_id: str, detail: str = ""):
        job = self.service.get(job_id)
        text = str(detail or "").strip()
        if job.status == JobStatus.NEEDS_HUMAN:
            return self.respond(job_id, detail=text or "Continue with the current task.")
        if job.status == JobStatus.READY_FOR_REVIEW:
            return self.request_changes(job_id, text or "Continue working on this ticket before merge.")
        if job.status in {
            JobStatus.FAILED,
            JobStatus.TIMED_OUT,
            JobStatus.BUDGET_EXCEEDED,
            JobStatus.ENVIRONMENT_ERROR,
        }:
            if text:
                self.service.store.append_event(
                    job_id,
                    "human_response",
                    payload={"detail": text, "source": "continue"},
                )
            return self.service.retry(job_id, reason=text or "continue requested by reviewer")
        raise JobReviewError(f"Job cannot be continued from {job.status.value}.")

    def discard(self, job_id: str, reason: str = ""):
        job = self.service.get(job_id)
        if job.status == JobStatus.MERGED:
            raise JobReviewError("A merged job cannot be discarded.")
        return self.service.cancel(job_id, reason=str(reason or "discarded from review"))
