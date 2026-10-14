"""Review phase of a durable job. Every completed review waits for a human decision.

Launched through ``mu.jobs.worker --phase review`` (``python -m
mu.jobs.review_worker`` remains a compatibility shim).
"""

from __future__ import annotations

import os
from typing import Optional

from .lease import run_leased
from .models import AttentionReason, JobStatus
from .receipt import refresh_receipt as _refresh_receipt
from .service import JobStateError, get_default_job_service


def run_review(job_id: str, worker_id: str, *, lease_ttl_seconds: int = 45, generate_fn=None) -> int:
    def body(service, job, lease) -> int:
        return _review(service, job, lease, generate_fn=generate_fn)

    return run_leased(
        get_default_job_service(), job_id, worker_id, phase="review",
        lease_ttl_seconds=lease_ttl_seconds, body=body, statuses=(JobStatus.REVIEWING,),
        reject_event="review_worker_rejected", release_reason="review worker exited",
    )


def _review(service, job, lease, *, generate_fn=None) -> int:
    from . import stages

    job_id = job.id
    try:
        try:
            code = stages.run_review_stage(
                service, job_id, generate_fn=generate_fn or stages.default_generate(service)
            )
        except JobStateError:
            raise
        except Exception as exc:  # noqa: BLE001 - reviewer failure parks on a human
            if not lease.owned():
                return 6
            from .sandbox.runner import SandboxUnavailable
            from .sandbox.environments import EnvironmentBlocked
            if isinstance(exc, SandboxUnavailable):
                service.transition(job_id, JobStatus.FAILED, reason=str(exc),
                                   payload={"stage": "provisioning", "exception_type": "SandboxUnavailable"})
                _refresh_receipt(service, job_id)
                return 3
            from .sandbox.runner import StageLimitReached
            if isinstance(exc, StageLimitReached):
                # A reached cap pauses the reviewer resumably; the human approves more or cancels.
                service.require_human(job_id, AttentionReason.LIMIT_REACHED, str(exc)[:2000],
                                      payload={"stage": "review", "limit": exc.detail})
                _refresh_receipt(service, job_id)
                return 26
            blocked = isinstance(exc, EnvironmentBlocked)
            service.require_human(
                job_id,
                AttentionReason.ENVIRONMENT_FAILURE if blocked else AttentionReason.PROVIDER_ERROR,
                str(exc)[:2000] if blocked else f"Review stage failed: {str(exc)[:400]}",
                payload={"stage": "review"},
            )
            code = 26
        _refresh_receipt(service, job_id)
        return code
    except JobStateError:
        raise
    except Exception as exc:  # noqa: BLE001
        try:
            service.store.append_event(
                job_id, "review_worker_error", reason=str(exc), payload={"process_id": os.getpid()}
            )
            _refresh_receipt(service, job_id)
        except Exception:  # noqa: BLE001
            pass
        return 1


def main(argv: Optional[list] = None) -> int:
    from .worker import main as worker_main
    return worker_main(argv, phase="review")


if __name__ == "__main__":
    raise SystemExit(main())
