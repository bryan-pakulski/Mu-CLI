"""Lease plumbing shared by every job worker phase.

A worker process owns one job through a lease the controller acquired for it.
``run_leased`` is the common frame: verify ownership, heartbeat while the
phase body runs, and release the lease on exit. The body receives a
lease-fenced service (every status write carries ``lease_owner``), so a worker
whose lease lapsed can never overwrite its replacement's state.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Iterable, Optional


logger = logging.getLogger(__name__)


def heartbeat_loop(service, job_id: str, worker_id: str, ttl: int, stop: threading.Event,
                   lease_lost: Optional[threading.Event] = None) -> None:
    """Renew the lease every ttl/3 seconds; set ``lease_lost`` when renewal fails."""
    interval = max(3.0, float(ttl) / 3.0)
    while not stop.wait(interval):
        try:
            if service.heartbeat(job_id, worker_id, ttl_seconds=ttl):
                continue
        except Exception:  # noqa: BLE001 - an unreadable store counts as a lost lease
            pass
        if lease_lost is not None:
            lease_lost.set()
        return


class StageLease:
    """Heartbeat thread + ownership checks for one leased worker run."""

    def __init__(self, service, job_id: str, worker_id: str, ttl: int, *, phase: str):
        self.service = service
        self.job_id = job_id
        self.worker_id = worker_id
        self.lost = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=heartbeat_loop,
            args=(service, job_id, worker_id, ttl, self._stop, self.lost),
            name=f"mucli-job-{phase}-heartbeat-{job_id[:8]}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def owned(self) -> bool:
        """True while the heartbeat holds and the store still shows this worker's unexpired lease."""
        if self.lost.is_set():
            return False
        try:
            return bool(self.service.store.assert_lease(self.job_id, self.worker_id))
        except Exception:  # noqa: BLE001
            return False

    def close(self, reason: str) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)
        try:
            self.service.release(self.job_id, self.worker_id, reason=reason)
        except Exception:  # noqa: BLE001 - release is best effort; the lease expires anyway
            pass


def run_leased(
    service,
    job_id: str,
    worker_id: str,
    *,
    phase: str,
    lease_ttl_seconds: int,
    body: Callable,
    statuses: Optional[Iterable] = None,
    reject_event: str = "",
    release_reason: str = "",
) -> int:
    """Run ``body(fenced_service, job, lease)`` while holding the job's lease.

    Returns 2 when the worker does not own the lease and 0 when the job is no
    longer in one of ``statuses`` (another worker or a human moved it).
    """
    from .service import JobStateError, LeaseLostError

    fenced = service.fenced(worker_id)
    lease = StageLease(fenced, job_id, worker_id, lease_ttl_seconds, phase=phase)
    try:
        job = fenced.get(job_id)
        if job.worker_id != worker_id:
            if reject_event:
                fenced.store.append_event(job_id, reject_event, reason="worker does not own job lease",
                                          payload={"expected": job.worker_id, "received": worker_id})
            return 2
        if statuses is not None and job.status not in set(statuses):
            return 0
        lease.start()
        return int(body(fenced, job, lease))
    except LeaseLostError:
        # A fenced write lost the lease: the job belongs to someone else now.
        return 0
    except JobStateError as exc:
        return _refused_transition(fenced, job_id, lease, phase, exc)
    finally:
        lease.close(release_reason or f"{phase} worker exited")


def _refused_transition(fenced, job_id: str, lease: "StageLease", phase: str, exc: Exception) -> int:
    """A phase body hit an illegal status move while still owning the job.

    When a human or recovery moved the job (or the lease lapsed) this is a
    benign race: return 0 quietly. When this worker still owns a job that is
    still schedulable, returning quietly would let the controller relaunch
    the same failing phase every tick, so the refusal is logged and the job
    is parked on a human with the reason.
    """
    from .core.states import NEEDS_HUMAN, SCHEDULABLE_STATUSES, can_transition_status
    from .models import AttentionReason

    if not lease.owned():
        return 0
    try:
        job = fenced.get(job_id)
    except Exception:  # noqa: BLE001
        return 0
    status = str(getattr(job.status, "value", job.status))
    if status not in SCHEDULABLE_STATUSES:
        logger.info("job %s %s worker: transition refused after job moved to %s: %s", job_id, phase, status, exc)
        return 0
    logger.error("job %s %s worker: illegal transition from %s: %s", job_id, phase, status, exc)
    detail = (f"The {phase} worker could not record its outcome ({exc}). The job was parked "
              "so it is not relaunched in a loop; inspect the events, then retry or cancel.")
    try:
        fenced.store.append_event(job_id, "worker_transition_refused", reason=str(exc)[:500],
                                  payload={"phase": phase, "status": status, "error": str(exc)})
        if can_transition_status(status, NEEDS_HUMAN):
            fenced.require_human(job_id, AttentionReason.ENVIRONMENT_FAILURE, detail,
                                 payload={"phase": phase, "error": str(exc)})
    except Exception:  # noqa: BLE001 - parking is best effort; the event/log already recorded it
        logger.exception("job %s: could not park after refused transition", job_id)
    return 1
