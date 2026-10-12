"""Leased sandbox reviewer. Every completed review waits for a human decision."""

from __future__ import annotations

import argparse
import os
import threading

from .models import AttentionReason, JobStatus
from .service import JobStateError, get_default_job_service
from .verify_worker import _heartbeat, _refresh_receipt


def run_review(job_id: str, worker_id: str, *, lease_ttl_seconds: int = 45, generate_fn=None) -> int:
    from . import stages

    service = get_default_job_service()
    heartbeat_stop = threading.Event()
    lease_lost = threading.Event()
    heartbeat_thread = threading.Thread(
        target=_heartbeat,
        args=(service, job_id, worker_id, lease_ttl_seconds, heartbeat_stop, lease_lost),
        name=f"mucli-job-review-heartbeat-{job_id[:8]}",
        daemon=True,
    )
    try:
        job = service.get(job_id)
        if job.worker_id != worker_id:
            service.store.append_event(
                job_id,
                "review_worker_rejected",
                reason="worker does not own job lease",
                payload={"expected": job.worker_id, "received": worker_id},
            )
            return 2
        if job.status != JobStatus.REVIEWING:
            return 0
        heartbeat_thread.start()
        try:
            code = stages.run_review_stage(
                service, job_id, generate_fn=generate_fn or stages.default_generate(service)
            )
        except Exception as exc:  # noqa: BLE001 - reviewer failure parks on a human
            if lease_lost.is_set():
                return 6
            service.require_human(
                job_id,
                AttentionReason.PROVIDER_ERROR,
                f"Review stage failed: {str(exc)[:400]}",
                payload={"stage": "review"},
            )
            code = 26
        _refresh_receipt(service, job_id)
        return code
    except JobStateError:
        return 0
    except Exception as exc:  # noqa: BLE001
        try:
            service.store.append_event(
                job_id, "review_worker_error", reason=str(exc), payload={"process_id": os.getpid()}
            )
            _refresh_receipt(service, job_id)
        except Exception:  # noqa: BLE001
            pass
        return 1
    finally:
        heartbeat_stop.set()
        if heartbeat_thread.is_alive():
            heartbeat_thread.join(timeout=1.0)
        try:
            service.release(job_id, worker_id, reason="review worker exited")
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the model review stage for one durable job")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--lease-ttl", type=int, default=45)
    args = parser.parse_args()
    return run_review(args.job_id, args.worker_id, lease_ttl_seconds=args.lease_ttl)


if __name__ == "__main__":
    raise SystemExit(main())
