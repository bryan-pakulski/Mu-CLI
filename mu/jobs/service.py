"""Control-plane-neutral service for durable engineering jobs."""

from __future__ import annotations

import copy
import logging
import threading
from typing import Any, Dict, Iterable, List, Optional

from .core import states as _states
from .models import AttentionReason, Job, JobAttempt, JobEvent, JobSpec, JobStatus, coerce_status
from .store import JobStore

logger = logging.getLogger("mucli")



def _job_default(path, fallback=None):
    """Settings → New job defaults (applied when the payload leaves the field out)."""
    from .job_settings import rule_or
    return rule_or(path, fallback)

class JobStateError(RuntimeError):
    pass


class LeaseLostError(JobStateError):
    """A lease-fenced write was attempted by a worker that no longer owns the job."""


class JobService:
    def __init__(self, store: Optional[JobStore] = None, *, reaper=None):
        self.store = store or JobStore()
        # Injectable stage-container reaper (job_id -> removed count); None
        # uses mu.jobs.sandbox.reaper.reap_stage_containers.
        self.reaper = reaper
        # Set on fenced() views: the worker whose lease every status write must hold.
        self.lease_owner: Optional[str] = None

    def fenced(self, worker_id: str) -> "JobService":
        """A view of this service whose status writes are lease-fenced on ``worker_id``.

        Workers hand this view to stage code, so every transition (including
        require_human / cancel) raises ``LeaseLostError`` once the lease is gone."""
        view = copy.copy(self)
        view.lease_owner = str(worker_id)
        return view

    def create(self, spec: JobSpec, *, job_id: Optional[str] = None) -> Job:
        return self.store.create_job(spec, job_id=job_id)

    def create_from_payload(self, payload: Dict[str, Any]) -> Job:
        # Jobs run in ``workspace_root`` (the session's workspace, resolved by
        # the GUI/mobile router or TUI) or in ``repository``: a URL (cloned into
        # $MUCLI_HOME/repos, see mu/jobs/clone.py) or a local path.
        from .clone import apply_repository

        value = apply_repository(dict(payload or {}))  # CloneError is a ValueError
        repository = str(value.get("workspace_root") or "").strip()
        execution = dict(value.get("execution") or {})
        metadata = dict(value.get("metadata") or {})
        supplied_base_branch = str(value.get("base_branch") or "").strip()
        supplied_base_sha = str(value.get("base_sha") or "").strip()
        clone_warnings: List[str] = [str(w) for w in metadata.get("repository_warnings") or []]
        base_branch = supplied_base_branch or "main"
        base_sha = supplied_base_sha

        # GUI/mobile normally submit a workspace but no base. Resolve that
        # ambiguity synchronously, before the job is queued, so a bad path or
        # non-Git directory is reported immediately instead of producing an
        # opaque child-worker environment failure several seconds later.
        # Snapshot the current committed HEAD: that is the actual code state
        # the user was looking at when delegating the ticket.
        session_type = str(execution.get("session_type") or "workspace").strip().lower()
        git_enabled = True
        if repository and session_type == "workspace":
            import os

            if not os.path.isdir(repository):
                raise ValueError(f"Repository preflight failed for {repository}: Workspace does not exist")
            from .repository import RepositoryError, RepositoryRegistry

            try:
                RepositoryRegistry.inspect(repository)
            except RepositoryError as exc:
                if "not a git repository" not in str(exc).lower():
                    raise ValueError(f"Repository preflight failed for {repository}: {exc}") from exc
                # Approved decision: non-git workspaces are allowed; git-only
                # stages (worktree, drift, publish) are skipped.
                git_enabled = False
        execution["git_enabled"] = bool(execution.get("git_enabled", git_enabled)) and git_enabled
        # Durable job v2: new jobs run plan -> implement -> verify -> review.
        execution.setdefault("pipeline", "staged")
        # Approved default: git workspaces open a draft PR after review.
        execution.setdefault("publish_mode", "auto" if execution.get("git_enabled") else "none")
        if execution.get("environments"):
            # Job override of the build environments (New job sheet / `/job create --env`).
            from .sandbox.environments import bindings_from_list
            execution["environments"] = [b.to_dict() for b in bindings_from_list(execution["environments"])]
        else:
            execution.pop("environments", None)
        # Agent per stage (mucli / Claude Code / Codex). Stamped at creation once any stage uses an
        # external agent, so later Settings changes don't switch a running job's agents.
        from .harness import STAGES as AGENT_STAGES, clean_stage_agents, stage_agent
        from types import SimpleNamespace
        chosen = clean_stage_agents(execution.get("stage_agents"))
        probe = SimpleNamespace(execution={**execution, "stage_agents": chosen})
        resolved = {s: stage_agent(probe, s) for s in AGENT_STAGES}
        if chosen or any(cfg["agent"] != "mucli" for cfg in resolved.values()):
            execution["stage_agents"] = {s: {**{"agent": cfg["agent"]}, **({"model": chosen[s]["model"]}
                                             if chosen.get(s, {}).get("model") else {}),
                                             **({"effort": chosen[s]["effort"]} if chosen.get(s, {}).get("effort") else {})}
                                         for s, cfg in resolved.items()}
        else:
            execution.pop("stage_agents", None)
        if (
            repository
            and git_enabled
            and session_type == "workspace"
            and not supplied_base_branch
            and not supplied_base_sha
        ):
            try:
                from .repository import RepositoryRegistry

                info = RepositoryRegistry.inspect(repository)
            except Exception as exc:
                raise ValueError(f"Repository preflight failed for {repository}: {exc}") from exc
            base_branch = str(info.get("current_branch") or info.get("default_branch") or "main")
            base_sha = str(info.get("head_sha") or "")
            if not base_sha:
                raise ValueError(
                    f"Repository preflight failed for {repository}: could not resolve current HEAD commit"
                )
            metadata["submission_repository_preflight"] = {
                "canonical_path": info.get("canonical_path", ""),
                "git_common_dir": info.get("git_common_dir", ""),
                "origin_url": info.get("origin_url", ""),
                "current_branch": info.get("current_branch", ""),
                "default_branch": info.get("default_branch", ""),
                "remote_default_ref": info.get("remote_default_ref", ""),
                "head_sha": base_sha,
                "clean": bool(info.get("clean", False)),
            }

        job = self.create(JobSpec(
            title=str(value.get("title") or ""),
            description=str(value.get("description") or ""),
            repository=repository,
            base_branch=base_branch,
            base_sha=base_sha,
            acceptance_criteria=list(value.get("acceptance_criteria") or []),
            validation_commands=list(value.get("validation_commands") or []),
            # 0 = unlimited (explicit); unset = Settings → Job cost limit (default $100).
            max_cost_usd=(lambda cap: None if cap is None or float(cap) == 0 else cap)(
                value.get("max_cost_usd") if value.get("max_cost_usd") is not None else _job_default("defaults.max_cost_usd")),
            max_runtime_seconds=(value.get("max_runtime_seconds") if value.get("max_runtime_seconds") is not None
                                 else _job_default("defaults.max_runtime_seconds")),
            max_iterations=value.get("max_iterations"),
            max_retries=int(value["max_retries"] if value.get("max_retries") is not None else _job_default("defaults.max_retries", 2)),
            max_subagents=value.get("max_subagents"),
            environment=dict(value.get("environment") or {}),
            execution=execution,
            metadata=metadata,
        ))
        for warning in clone_warnings:
            # e.g. git-lfs missing: the job still runs, the warning is visible on it.
            self.store.append_event(job.id, "repository_warning", reason=warning,
                                    payload={"repository_url": metadata.get("repository_url", "")})
        return job

    def get(self, job_id: str) -> Job:
        return self.store.get_job(job_id)

    def list(self, *, statuses: Optional[Iterable[JobStatus | str]] = None, limit: int = 200) -> List[Job]:
        return self.store.list_jobs(statuses=statuses, limit=limit)

    def list_unarchived(self, *, limit: int = 200) -> List[Job]:
        """Jobs not archived (round-49 F9/F10): SQL-side filter so the board
        never materializes the full archived-id set per render."""
        return self.store.list_unarchived_jobs(limit=limit)

    def attention_summary(self) -> Dict[str, Any]:
        """Jobs currently waiting on a human (NEEDS_HUMAN / CONFLICTED)."""
        return self.store.attention_summary()

    def events(self, job_id: str, *, after_id: int = 0, limit: int = 500) -> List[JobEvent]:
        self.get(job_id)
        return self.store.list_events(job_id, after_id=after_id, limit=limit)

    def attempts(self, job_id: str) -> List[JobAttempt]:
        self.get(job_id)
        return self.store.list_attempts(job_id)

    def transition(
        self,
        job_id: str,
        target: JobStatus | str,
        *,
        reason: str = "",
        payload: Optional[Dict[str, Any]] = None,
        attention_reason: AttentionReason | str = AttentionReason.NONE,
        attention_detail: str = "",
        expected_version: Optional[int] = None,
        lease_owner: Optional[str] = None,
    ) -> Job:
        current = self.get(job_id)
        target_status = coerce_status(target)
        if current.status == target_status:
            return current
        if not _states.can_transition_status(current.status, target_status):
            raise JobStateError(
                f"cannot transition job {job_id} from {current.status.value} to {target_status.value}"
            )
        if target_status == JobStatus.NEEDS_HUMAN:
            attention = attention_reason if isinstance(attention_reason, AttentionReason) else AttentionReason(str(attention_reason or ""))
            if attention == AttentionReason.NONE:
                raise JobStateError("needs_human requires an attention_reason")
        elif target_status == JobStatus.CONFLICTED:
            # Base drift / merge conflict is a human-attention state too:
            # keep the reason so boards and the attention push explain WHY.
            attention_reason = AttentionReason.MERGE_CONFLICT
            attention_detail = str(attention_detail or "The job branch no longer merges cleanly onto its base branch.")
        else:
            attention_reason = AttentionReason.NONE
            attention_detail = ""
        return self.store.transition(
            job_id,
            target_status,
            reason=reason,
            payload=payload,
            attention_reason=attention_reason,
            attention_detail=attention_detail,
            expected_version=expected_version,
            lease_owner=lease_owner if lease_owner is not None else self.lease_owner,
        )

    def cancel(self, job_id: str, *, reason: str = "cancelled by user") -> Job:
        current = self.get(job_id)
        if current.status == JobStatus.CANCELLED:
            return current
        return self.transition(job_id, JobStatus.CANCELLED, reason=reason)

    def require_human(
        self,
        job_id: str,
        reason: AttentionReason | str,
        detail: str,
        *,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Job:
        return self.transition(
            job_id,
            JobStatus.NEEDS_HUMAN,
            reason="human attention required",
            payload=payload,
            attention_reason=reason,
            attention_detail=detail,
        )

    def resume(self, job_id: str, *, detail: str = "") -> Job:
        current = self.get(job_id)
        if current.status != JobStatus.NEEDS_HUMAN:
            raise JobStateError("only a needs_human job can be resumed")
        self.store.append_event(job_id, "human_response", payload={"detail": str(detail or "")})
        self._clear_retry_backoff(job_id)
        return self.transition(job_id, JobStatus.QUEUED, reason="human response received; requeued")

    def _clear_retry_backoff(self, job_id: str) -> None:
        """Drop a pending automatic-retry ``next_run_at`` so an explicit
        human retry/resume runs immediately."""
        try:
            if "next_run_at" in (self.get(job_id).metadata or {}):
                self.store.patch_metadata(job_id, remove=("next_run_at",))
        except Exception:  # noqa: BLE001
            logger.debug("clear_retry_backoff failed", exc_info=True)

    def retry(self, job_id: str, *, reason: str = "retry requested") -> Job:
        self._clear_retry_backoff(job_id)
        current = self.get(job_id)
        if current.status.value not in _states.RETRYABLE_STATUSES:
            raise JobStateError(f"job {job_id} is not retryable from {current.status.value}")
        return self.transition(job_id, JobStatus.QUEUED, reason=reason)

    def acquire(self, job_id: str, worker_id: str, *, ttl_seconds: int = 60) -> bool:
        if not worker_id:
            raise ValueError("worker_id is required")
        return self.store.acquire_lease(job_id, worker_id, ttl_seconds=ttl_seconds)

    def heartbeat(self, job_id: str, worker_id: str, *, ttl_seconds: int = 60) -> bool:
        return self.store.heartbeat(job_id, worker_id, ttl_seconds=ttl_seconds)

    def release(self, job_id: str, worker_id: str, *, reason: str = "") -> bool:
        return self.store.release_lease(job_id, worker_id, reason=reason)

    def start_attempt(self, job_id: str, *, worker_id: str = "", session_name: str = "", metadata: Optional[Dict[str, Any]] = None) -> JobAttempt:
        return self.store.start_attempt(job_id, worker_id=worker_id, session_name=session_name, metadata=metadata)

    def finish_attempt(self, attempt_id: str, *, status: str, error: str = "", cost_usd: float = 0.0, metadata: Optional[Dict[str, Any]] = None) -> JobAttempt:
        return self.store.finish_attempt(attempt_id, status=status, error=error, cost_usd=cost_usd, metadata=metadata)

    def _resume_after_finished_attempt(
        self, job: Job, attempt: JobAttempt, *, expired_worker: str,
        expected_version: Optional[int] = None,
    ) -> Job:
        """Round-16 F20: idempotent replay of the post-finish steps the
        worker would have taken for an attempt that committed as
        completed/needs_human before the crash. Safe to call repeatedly —
        the store transitions validate against the live row and
        ``expected_version`` fences against a concurrent re-lease."""
        metadata = attempt.metadata or {}
        checkpoint = metadata.get("checkpoint")
        if attempt.status == "completed":
            return self.transition(
                job.id,
                JobStatus.VERIFYING,
                reason="implementation attempt completed (recovered after crash)",
                payload={"checkpoint": checkpoint, "attempt_id": attempt.id},
                expected_version=expected_version,
            )
        attention = metadata.get("attention") or {}
        raw_reason = str(metadata.get("attention_reason") or "")
        try:
            reason = AttentionReason(raw_reason)
        except ValueError:
            reason = AttentionReason.NONE
        if reason in (AttentionReason.NONE, ""):
            # Older attempts (pre-round-16) never persisted the reason;
            # NEEDS_HUMAN requires one, so fall back to a generic value
            # instead of dropping the job into RECOVERING (re-run).
            reason = AttentionReason.QUESTION
        return self.transition(
            job.id,
            JobStatus.NEEDS_HUMAN,
            reason="human attention required",
            payload={**attention, "checkpoint": checkpoint},
            attention_reason=reason,
            attention_detail=str(metadata.get("attention_detail") or
                                 "recovered from crash after needs-human attempt finished"),
            expected_version=expected_version,
        )

    # ------------------------------------------------------------ lease recovery

    # Lease expiry in these stages re-dispatches the same stage (no status
    # change); repeated crashes are capped by jobs.max_stage_crash_retries.
    _STAGE_RETRY_STATUSES = frozenset(JobStatus(v) for v in _states.STAGE_RETRY_STATUSES)
    _STAGE_LEASE_EVENTS = ("verification_lease_expired", "planning_lease_expired")
    DEFAULT_MAX_STAGE_CRASH_RETRIES = 2

    def _reap_stage_containers(self, job_id: str) -> int:
        """Remove the job's orphaned non-engine stage containers before the
        stage is re-dispatched (best effort; docker trouble is a no-op)."""
        try:
            if self.reaper is not None:
                removed = int(self.reaper(job_id) or 0)
            else:
                from .sandbox.reaper import reap_stage_containers
                removed = reap_stage_containers(job_id)
        except Exception:  # noqa: BLE001 - cleanup must never block recovery
            logger.debug("stage container reap failed for job %s", job_id, exc_info=True)
            return 0
        if removed:
            try:
                self.store.append_event(
                    job_id, "stage_containers_reaped",
                    reason="orphaned stage containers removed before recovery",
                    payload={"removed": removed},
                )
            except Exception:  # noqa: BLE001
                pass
        return removed

    @staticmethod
    def _max_stage_crash_retries() -> int:
        try:
            value = int(_job_default("jobs.max_stage_crash_retries", JobService.DEFAULT_MAX_STAGE_CRASH_RETRIES))
        except (TypeError, ValueError):
            value = JobService.DEFAULT_MAX_STAGE_CRASH_RETRIES
        return max(0, value)

    def _stage_crash_count(self, job_id: str, status: JobStatus) -> int:
        """Lease expiries recorded since the job last entered ``status``."""
        events = self.store.list_events_by_type(job_id, ("status_changed", *self._STAGE_LEASE_EVENTS))
        count = 0
        for event in reversed(events):
            if event.event_type == "status_changed":
                if event.to_status == status:
                    break
                continue
            if str((event.payload or {}).get("status") or status.value) == status.value:
                count += 1
        return count

    def _close_orphaned_attempt(self, job_id: str, worker: str) -> Optional[JobAttempt]:
        """A worker that died mid-run leaves its attempt row 'running'
        forever; close it as interrupted so history and attempt counts are
        truthful. Never touches the job row (no version bump)."""
        attempts = [a for a in self.store.list_attempts(job_id) if a.status == "running"]
        closed = None
        for attempt in attempts:
            try:
                closed = self.store.finish_attempt(
                    attempt.id,
                    status="interrupted",
                    error="worker lease expired; attempt abandoned",
                    cost_usd=float(attempt.cost_usd or 0.0),
                    metadata={"interrupted": True, "expired_worker_id": worker},
                )
            except KeyError:
                continue
        return closed

    def _recover_one(self, job_id: str, worker: str) -> Optional[Job]:
        """Recover one job whose expired lease this pass has just claimed.

        Every status write is fenced on the version observed right after the
        claim: if another controller leased the job in between, the write
        raises and the job is left to its new owner."""
        job = self.get(job_id)
        if job.worker_id:
            return None  # already re-leased by another controller
        version = job.version
        status = job.status

        if status == JobStatus.RUNNING:
            # Round-16 F20: the worker commits the attempt result FIRST and
            # the job transition SECOND — a crash between the two leaves a
            # finished attempt attached to a RUNNING job. Finish what the
            # worker started (idempotent replay) instead of re-running work.
            attempts = self.store.list_attempts(job.id)
            newest = max(attempts, key=lambda a: (a.number, a.started_at or 0.0), default=None)
            if newest is not None and newest.status in {"completed", "needs_human"}:
                try:
                    return self._resume_after_finished_attempt(
                        job, newest, expired_worker=worker, expected_version=version,
                    )
                except Exception as exc:  # noqa: BLE001
                    if isinstance(exc, RuntimeError) and not isinstance(exc, JobStateError):
                        raise  # expected_version mismatch: superseded, not broken
                    logger.exception(
                        "Idempotent recovery failed for job %s (attempt %s, status %s); "
                        "falling back to RECOVERING", job.id, newest.id, newest.status,
                    )
                    version = self.get(job.id).version

        if status in {JobStatus.PREPARING, JobStatus.RUNNING}:
            self._close_orphaned_attempt(job.id, worker)
            from .stages import is_staged

            if (
                status == JobStatus.PREPARING
                and is_staged(job)
                and not (job.metadata or {}).get("plan_approved_version")
            ):
                # A staged job that died before planning finished must plan
                # again — RECOVERING would hand it to implementation with no
                # approved plan.
                return self.transition(
                    job.id, JobStatus.PLANNING,
                    reason="worker lease expired before planning; planning restarts",
                    payload={"worker_id": worker},
                    expected_version=version,
                )
            return self.transition(
                job.id,
                JobStatus.RECOVERING,
                reason="implementation worker lease expired",
                payload={"worker_id": worker},
                expected_version=version,
            )

        if status in self._STAGE_RETRY_STATUSES:
            limit = self._max_stage_crash_retries()
            prior = self._stage_crash_count(job.id, status)
            if prior >= limit:
                return self.transition(
                    job.id,
                    JobStatus.NEEDS_HUMAN,
                    reason="stage worker crashed repeatedly",
                    payload={"stage_status": status.value, "crashes": prior + 1, "worker_id": worker},
                    attention_reason=AttentionReason.WORKER_LOST,
                    attention_detail=(
                        f"The {status.value} worker stopped {prior + 1} times without finishing "
                        f"(limit jobs.max_stage_crash_retries={limit}). Check the worker log, then "
                        "retry the stage."
                    ),
                    expected_version=version,
                )
            if status == JobStatus.PLANNING:
                self.store.append_event(
                    job.id,
                    "planning_lease_expired",
                    reason="planning worker lease expired; planning will restart",
                    payload={"worker_id": worker, "status": status.value, "retry": prior + 1, "limit": limit},
                )
            else:
                self.store.append_event(
                    job.id,
                    "verification_lease_expired",
                    reason="verification worker lease expired; verifier will retry",
                    payload={"worker_id": worker, "status": status.value, "retry": prior + 1, "limit": limit},
                )
            return self.get(job.id)
        return None

    def recover_expired_leases(self) -> List[Job]:
        recovered: List[Job] = []
        for job in self.store.expired_leases():
            worker = job.worker_id
            # Round-36 F1: CLAIM the expired lease BEFORE acting on the
            # stale snapshot. release_expired_lease is a CAS — it succeeds
            # only when the lease is STILL this worker's AND still expired
            # at write time. Acting first (transitioning to RECOVERING,
            # replaying a finished attempt) and releasing afterwards let a
            # worker that renewed between the snapshot and the release
            # keep running while its job was yanked to RECOVERING — two
            # owners, duplicated side effects. Now a renewed lease means
            # this recovery pass skips the job entirely.
            if not self.store.release_expired_lease(
                job.id, worker, reason="expired lease claimed for recovery"
            ):
                continue
            # The dead worker's stage container may still be running and
            # writing; remove it before anything re-dispatches the stage.
            self._reap_stage_containers(job.id)
            try:
                result = self._recover_one(job.id, worker)
            except KeyError:
                continue
            except JobStateError:
                continue
            except RuntimeError:
                # expected_version mismatch: another controller took over.
                logger.debug("lease recovery for job %s superseded", job.id, exc_info=True)
                continue
            if result is not None:
                recovered.append(result)
        return recovered


_default_lock = threading.Lock()
_default_service: Optional[JobService] = None


def get_default_job_service() -> JobService:
    global _default_service
    with _default_lock:
        if _default_service is None:
            _default_service = JobService()
        return _default_service
