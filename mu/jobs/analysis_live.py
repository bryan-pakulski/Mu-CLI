"""Normalize the open tail of a durable-job lifecycle analysis.

Only cancelled/merged jobs are terminal. Retryable failures, review-ready work,
human gates and queued jobs remain live states, so retrospective state residence
must continue to the analysis timestamp rather than freezing at the last event.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict

from .models import JobStatus
from .service import JobService

_ACTIVE = {"preparing", "running", "recovering"}
_WAITING = {"queued", "needs_human"}


def extend_open_residence(service: JobService, job_id: str, analysis: Dict[str, Any]) -> Dict[str, Any]:
    job = service.get(job_id)
    if job.terminal:
        return analysis

    intervals = list(analysis.get("phase_intervals") or [])
    if not intervals:
        return analysis

    last = dict(intervals[-1])
    if str(last.get("status") or "") != job.status.value:
        return analysis

    now = float(service.store._clock())
    started = float(last.get("started_at") or now)
    last["finished_at"] = max(started, now)
    last["duration_seconds"] = max(0.0, now - started)
    intervals[-1] = last
    analysis["phase_intervals"] = intervals

    totals: Dict[str, float] = defaultdict(float)
    occurrences: Dict[str, int] = defaultdict(int)
    for interval in intervals:
        status = str(interval.get("status") or "")
        totals[status] += float(interval.get("duration_seconds") or 0.0)
        occurrences[status] += 1
    elapsed = sum(totals.values())
    breakdown = [
        {
            "status": status,
            "seconds": seconds,
            "occurrences": occurrences[status],
            "percent": (seconds / elapsed * 100.0) if elapsed > 0 else 0.0,
        }
        for status, seconds in totals.items()
    ]
    analysis["phase_breakdown"] = sorted(breakdown, key=lambda item: item["seconds"], reverse=True)

    summary = analysis.setdefault("summary", {})
    active = sum(totals[name] for name in _ACTIVE)
    waiting = sum(totals[name] for name in _WAITING)
    summary["elapsed_seconds"] = elapsed
    summary["active_seconds"] = active
    summary["waiting_seconds"] = waiting
    summary["verification_seconds"] = totals[JobStatus.VERIFYING.value]
    summary["queue_seconds"] = totals[JobStatus.QUEUED.value]
    summary["active_ratio"] = (active / elapsed) if elapsed > 0 else None
    summary["waiting_ratio"] = (waiting / elapsed) if elapsed > 0 else None
    analysis["generated_at"] = now
    return analysis


__all__ = ["extend_open_residence"]
