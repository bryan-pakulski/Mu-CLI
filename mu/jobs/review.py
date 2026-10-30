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


def build_job_patch(service: JobService, job_id: str, *, source: str = "") -> str:
    """The job's whole change as one patch (binary-safe, untruncated) that ``git apply`` applies
    to the base: how a job on a folder snapshot (mu/jobs/folder_snapshot.py) hands its result back.
    ``source`` names an extra source (mu/jobs/multi_source.py); its patch applies in that source."""
    job = service.get(job_id)
    if source:
        from .multi_source import source_diff, sources
        match = next((s for s in sources(job) if s["name"] == source), None)
        if match is None:
            raise JobReviewError(f"This job has no source named {source!r}.")
        if not match.get("branch"):
            raise JobReviewError(f"Source {source!r} has not been prepared yet.")
        return source_diff(job, match, prefixed=False, binary=True)
    if not job.base_sha:
        raise JobReviewError("This job does not have a captured base commit yet.")
    repo, head_ref = _review_target(service, job)
    return _git(repo, "diff", "--no-ext-diff", "--no-color", "--binary", "--full-index", "--find-renames",
                f"{job.base_sha}...{head_ref}", "--", timeout=300)


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
    # Extra sources (mu/jobs/multi_source.py): their changes follow, paths under sources/<name>/.
    from .multi_source import source_diff, sources
    for source in sources(job):
        if not source.get("branch"):
            continue
        piece = source_diff(job, source)
        if not piece:
            continue
        patch += piece
        prefix = f"sources/{source['name']}/"
        files += [line[len("+++ b/"):] for line in piece.splitlines() if line.startswith(f"+++ b/{prefix}")]
        stat += f"\n{prefix}: {piece.count('diff --git ')} file(s) changed"
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

    def _respond_plan_review(self, job, response: Dict[str, Any]):
        """Plan gate: ``approve`` (optionally with edited criteria/commands) or ``replan``."""
        decision = response["decision"]
        if decision not in {"approve", "replan"}:
            raise JobReviewError("Plan review responses require decision=approve or replan.")
        value = response.get("value") if isinstance(response.get("value"), dict) else {}
        metadata = dict(job.metadata or {})
        if decision == "replan":
            previous = metadata.pop("plan", None)
            metadata.pop("plan_contract", None)
            if response["detail"]:
                metadata["replan_feedback"] = response["detail"]
            self.service.store.update_runtime_fields(job.id, metadata_json=metadata)
            self.service.store.append_event(
                job.id, "plan_rejected", reason="human requested a new plan",
                payload={"detail": response["detail"], "previous_summary": (previous or {}).get("summary", "")},
            )
        else:
            fields: Dict[str, Any] = {}
            for key, column in (("acceptance_criteria", "acceptance_json"),
                                ("validation_commands", "validation_json")):
                if key in value:
                    items = value[key]
                    if not isinstance(items, list):
                        raise JobReviewError(f"{key} must be a list of strings.")
                    fields[column] = [str(v).strip() for v in items if str(v or "").strip()][:50]
            if "validation_json" in fields:
                from .dynamic_checks import allowed_executables, check_command

                allowed = allowed_executables(job)
                bad = [c for c in fields["validation_json"] if not check_command(c, allowed)[0]]
                if bad:
                    raise JobReviewError("Validation command not allowed: " + "; ".join(bad[:3]))
            metadata["plan_approved"] = {"by": "human", "detail": response["detail"]}
            try:
                from .core.plan_ops import legacy_approval_version

                approved_version = legacy_approval_version(job, value)
                if approved_version:
                    metadata["plan_approved_version"] = approved_version
            except (OSError, ValueError):
                pass
            self.service.store.update_runtime_fields(job.id, metadata_json=metadata, **fields)
            self.service.store.append_event(
                job.id, "plan_approved", reason="human approved plan",
                payload={"edited": sorted(k for k in value if k in {"acceptance_criteria", "validation_commands"}),
                         "detail": response["detail"]},
            )
        self.service.store.append_event(job.id, "interaction_response", payload=response)
        return self.service.resume(job.id, detail=response["detail"] or decision)

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
        if job.attention_reason.value == "plan_review":
            return self._respond_plan_review(job, response)
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
        if job.status == JobStatus.CONFLICTED:
            return self.resolve_conflict(job_id, text)
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

    def resolve_conflict(self, job_id: str, detail: str = ""):
        """Requeue a CONFLICTED job so the agent rebases/merges the current
        base into the job branch and re-runs verification. The drift report
        is handed to the next attempt as review feedback."""
        job = self.service.get(job_id)
        if job.status != JobStatus.CONFLICTED:
            raise JobReviewError("Only a conflicted job can be resolved this way.")
        drift = dict((job.metadata or {}).get("base_drift") or {})
        # The sandbox has no gitdir, so the host does the merge: a clean merge
        # becomes a checkpoint; a conflicted one is committed WITH markers as a
        # checkpoint and the agent is told exactly which files to resolve.
        merge = merge_base_into_branch(self.service, job, drift)
        files = ", ".join(drift.get("conflicted_files") or []) or "unknown files"
        base_label = merge.get("base_ref") or drift.get("base_ref") or job.base_branch
        tip = str(merge.get("base_sha") or drift.get("current_base_sha") or "")[:12]
        if merge.get("status") == "clean":
            guidance = (
                f"The host merged the current base {base_label} ({tip}) into the job branch cleanly "
                f"(checkpoint {str(merge.get('head_sha') or '')[:12]}). Re-run the validation commands and "
                "fix anything the new base broke, keeping the ticket's behaviour."
            )
        elif merge.get("status") == "conflicted":
            conflicted = ", ".join(merge.get("conflicted_files") or []) or files
            guidance = (
                f"The host merged the current base {base_label} ({tip}) into the job branch and the merge "
                f"conflicted in: {conflicted}. The conflict markers are committed as checkpoint "
                f"{str(merge.get('head_sha') or '')[:12]}. Resolve every <<<<<<< / ======= / >>>>>>> marker "
                "in those files, keeping both the base's changes and the ticket's behaviour (do not rebase; "
                "the merge is already recorded), then re-run the validation commands."
            )
        else:
            guidance = (
                f"The base branch {base_label} moved and the job branch no "
                f"longer merges cleanly (conflicts: {files}). Merge or rebase onto the current base tip "
                f"{tip}, resolve conflicts, keep the ticket's behaviour, and re-run the validation commands."
            )
        text = str(detail or "").strip()
        text = f"{text}\n\n{guidance}" if text and merge.get("status") in {"clean", "conflicted"} else (text or guidance)
        self.service.store.append_event(
            job_id, "human_response", payload={"detail": text, "source": "conflict_resolution"},
        )
        # CONFLICTED -> RUNNING is not a worker-owned path; route through
        # QUEUED so the controller leases a fresh implementation attempt.
        # (CONFLICTED -> QUEUED is allowed for exactly this loop.)
        return self.service.transition(
            job_id, JobStatus.QUEUED,
            reason="conflict resolution requested; requeued",
            payload={"feedback": text, "branch": job.branch, "base_drift": drift, "base_merge": merge},
        )

    def discard(self, job_id: str, reason: str = ""):
        job = self.service.get(job_id)
        if job.status == JobStatus.MERGED:
            raise JobReviewError("A merged job cannot be discarded.")
        return self.service.cancel(job_id, reason=str(reason or "discarded from review"))


def merge_base_into_branch(service: JobService, job, drift: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Merge the current base tip into the job branch on the host (W3 #4).

    Uses the job's live worktree when it exists (it must be clean), otherwise
    a temporary worktree on the job branch. Returns ``status``: ``clean``
    (merge commit made), ``conflicted`` (merge committed with markers; see
    ``conflicted_files``), ``up_to_date`` or ``unavailable`` (with ``error``).
    On a recorded merge the job's ``base_sha`` moves to the merged base tip so
    review diffs and baseline triage stay scoped to the job's own changes."""
    import shutil
    import tempfile

    from .base_drift import _base_ref, _repository_root, comparison_ref
    from .repository import git_timeout_seconds
    from .worktree import git_identity_env

    drift = dict(drift or {})
    result: Dict[str, Any] = {"status": "unavailable"}
    if not job.branch:
        return {**result, "error": "job has no branch"}
    try:
        repo = _repository_root(service, job)
        base_ref = comparison_ref(repo, str(drift.get("base_ref") or "").strip() or _base_ref(job))
    except Exception as exc:  # noqa: BLE001
        return {**result, "error": str(exc)[:300]}
    result["base_ref"] = base_ref
    timeout = git_timeout_seconds()

    def git(cwd: str, *args: str, env=None) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", cwd, *args], text=True,
                              capture_output=True, timeout=timeout, env=env, check=False)

    temp_dir = ""
    path = ""
    try:
        tip = git(repo, "rev-parse", "--verify", f"{base_ref}^{{commit}}")
        if tip.returncode != 0:
            return {**result, "error": f"base ref {base_ref!r} does not resolve"}
        tip_sha = tip.stdout.strip()
        result["base_sha"] = tip_sha
        if git(repo, "merge-base", "--is-ancestor", tip_sha, f"refs/heads/{job.branch}").returncode == 0:
            return {**result, "status": "up_to_date"}
        live = str(job.worktree or "")
        if live and os.path.isdir(live):
            path = live
            if git(path, "status", "--porcelain").stdout.strip():
                return {**result, "error": "job worktree has uncommitted changes"}
            if git(path, "symbolic-ref", "--quiet", "--short", "HEAD").stdout.strip() != job.branch:
                return {**result, "error": "job worktree is not on the job branch"}
        else:
            temp_dir = tempfile.mkdtemp(prefix="mucli-merge-")
            path = os.path.join(temp_dir, "worktree")
            added = git(repo, "worktree", "add", path, job.branch)
            if added.returncode != 0:
                return {**result, "error": (added.stderr or "worktree add failed").strip()[:300]}
        env = git_identity_env(path)
        message = f"mu checkpoint: merge {base_ref} ({tip_sha[:12]}) into {job.branch}"
        merged = git(path, "-c", "commit.gpgsign=false", "merge", "--no-ff", "--no-edit", "-m", message, tip_sha, env=env)
        if merged.returncode != 0:
            unmerged = git(path, "diff", "--name-only", "--diff-filter=U")
            conflicted = [line.strip() for line in unmerged.stdout.splitlines() if line.strip()]
            if not conflicted:
                git(path, "merge", "--abort")
                return {**result, "error": (merged.stderr or merged.stdout or "merge failed").strip()[:300]}
            git(path, "add", "-A")
            committed = git(path, "commit", "--no-verify", "--no-gpg-sign", "-m",
                            f"{message} (conflict markers in {len(conflicted)} file(s))", env=env)
            if committed.returncode != 0:
                git(path, "merge", "--abort")
                return {**result, "error": (committed.stderr or "conflict checkpoint failed").strip()[:300]}
            result.update(status="conflicted", conflicted_files=conflicted)
        else:
            result["status"] = "clean"
        result["head_sha"] = git(path, "rev-parse", "HEAD").stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return {**result, "status": "unavailable", "error": str(exc)[:300]}
    finally:
        if temp_dir:
            if path and os.path.isdir(path):
                try:
                    git(repo, "worktree", "remove", "--force", path)
                except (OSError, subprocess.SubprocessError):
                    pass
            shutil.rmtree(temp_dir, ignore_errors=True)
            try:
                git(repo, "worktree", "prune")
            except (OSError, subprocess.SubprocessError):
                pass
    try:
        service.store.update_runtime_fields(job.id, base_sha=result["base_sha"])
    except Exception:  # noqa: BLE001
        pass
    service.store.append_event(job.id, "base_merged", reason=f"host merged {base_ref} ({result['status']})",
                               payload=result)
    return result
