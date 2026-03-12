from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from mu_cli.webapp.job_state import JobStatus, JobTerminalReason, TERMINAL_STATUSES, set_terminal_reason, transition_job_status


@dataclass(slots=True)
class JobDeps:
    start_background_turn: Callable[[Any, str, str], str]


def list_jobs(runtime: Any) -> list[dict[str, Any]]:
    return list(runtime.background_jobs.values())


def get_job(runtime: Any, job_id: str) -> dict[str, Any] | None:
    return runtime.background_jobs.get(job_id)


def start_job(runtime: Any, session_name: str, text: str, deps: JobDeps) -> str:
    return deps.start_background_turn(runtime, session_name, text)


def request_kill(runtime: Any, job_id: str, reason: str) -> tuple[int, dict[str, Any]]:
    job = runtime.background_jobs.get(job_id)
    if job is None:
        return 404, {"error": "job not found"}
    status = str(job.get("status") or "")
    if status in TERMINAL_STATUSES:
        return 200, {"ok": True, "job_id": job_id, "status": status, "cancel_requested": status == JobStatus.KILLED.value, "message": "job already terminal"}
    job["cancel_requested"] = True
    job["cancel_reason"] = reason
    transition_job_status(job, JobStatus.KILLED.value, reason="kill_requested_via_api")
    set_terminal_reason(job, JobTerminalReason.KILLED)
    events = job.setdefault("events", [])
    if isinstance(events, list):
        events.append(f"cancel_requested: {reason}")
        events.append(f"status: killed ({reason})")
    return 200, {"ok": True, "job_id": job_id, "status": job.get("status"), "cancel_requested": True}


def decide_plan(runtime: Any, job_id: str, decision: str, revised_plan: str) -> tuple[int, dict[str, Any]]:
    job = runtime.background_jobs.get(job_id)
    if job is None:
        return 404, {"error": "job not found"}
    if decision not in {"approve", "deny"}:
        return 400, {"error": "decision must be approve|deny"}
    if decision == "approve" and revised_plan:
        job["plan"] = revised_plan
        events = job.setdefault("events", [])
        if isinstance(events, list):
            events.append("plan: revised_by_user")
    job["plan_approval"] = decision
    return 200, {"ok": True, "job_id": job_id, "decision": decision, "plan": job.get("plan")}
