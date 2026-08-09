"""Shared review/attention actions for durable engineering jobs.

GUI, TUI and mobile call this layer so review feedback, blocker responses and
job diff semantics stay identical across control planes.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import asdict, dataclass
from typing import Any, Dict, List

from .models import JobStatus
from .service import JobService, JobStateError


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


def _git(worktree: str, *args: str, timeout: int = 60) -> str:
    result = subprocess.run(
        ["git", "-C", worktree, *args],
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


def build_job_diff(service: JobService, job_id: str, *, max_chars: int = 500_000) -> JobDiff:
    job = service.get(job_id)
    worktree = os.path.abspath(os.path.expanduser(str(job.worktree or "")))
    if not worktree or not os.path.isdir(worktree):
        raise JobReviewError("This job does not have an available worktree yet.")
    if not job.base_sha:
        raise JobReviewError("This job does not have a captured base commit yet.")

    head_sha = _git(worktree, "rev-parse", "HEAD").strip()
    files = [
        line.strip()
        for line in _git(worktree, "diff", "--name-only", f"{job.base_sha}...HEAD").splitlines()
        if line.strip()
    ]
    stat = _git(worktree, "diff", "--stat", f"{job.base_sha}...HEAD").strip()
    patch = _git(
        worktree,
        "diff",
        "--no-ext-diff",
        "--no-color",
        "--find-renames",
        f"{job.base_sha}...HEAD",
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
        if job.attention_reason.value == "approval_required" and response["decision"] not in {
            "approve", "deny", "explain"
        }:
            raise JobReviewError("Approval responses require decision=approve, deny, or explain.")
        if not response["decision"] and value is None and not response["selected"] and not response["detail"]:
            raise JobReviewError("A response value, decision, selection, or detail is required.")
        self.service.store.append_event(job_id, "interaction_response", payload=response)
        return self.service.resume(job_id, detail=response["detail"] or str(value or response["decision"] or response["selected"]))

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
        # The runner already reads the newest human_response into the next
        # implementation prompt. Keep that contract shared with blockers.
        self.service.store.append_event(
            job_id,
            "human_response",
            payload={"detail": text, "source": "review_feedback"},
        )
        return self.service.transition(
            job_id,
            JobStatus.QUEUED,
            reason="review changes requested; requeued",
            payload={"feedback": text},
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
