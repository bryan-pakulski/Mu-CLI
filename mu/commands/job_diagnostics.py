"""TUI diagnostics for durable engineering jobs."""

from __future__ import annotations

import json
from typing import Any

from mu.jobs import get_default_job_service

from . import CommandResult, command
from .job import _resolve_job


_DIAGNOSTIC_EVENTS = {
    "worker_lease_acquired",
    "worker_process_started",
    "worker_process_exited",
    "worker_spawn_failed",
    "worktree_preflight_started",
    "repository_inspected",
    "job_base_resolved",
    "worktree_inventory",
    "worktree_add_started",
    "worktree_prepare_failed",
    "checkpoint_failed",
    "runtime_error",
}


def _emit(session: Any, text: str, allow_prompt: bool, *, error: bool = False) -> None:
    ui = getattr(session, "ui", None)
    if not allow_prompt or ui is None:
        return
    method = "show_error" if error else "show_info"
    if hasattr(ui, method):
        getattr(ui, method)(text)


def _compact_payload(payload: dict) -> str:
    important = {}
    for key in (
        "stage",
        "error",
        "repository_input",
        "canonical_path",
        "detected_default_branch",
        "requested_base_branch",
        "resolved_base_ref",
        "base_sha",
        "managed_branch",
        "managed_worktree",
        "worktree",
        "pid",
        "worker_process_id",
        "phase",
        "return_code",
        "exit_code",
        "log_path",
        "command",
        "attempted_refs",
        "stderr",
        "stdout",
    ):
        value = payload.get(key)
        if value not in (None, "", [], {}):
            important[key] = value
    return json.dumps(important, indent=2, ensure_ascii=False, default=str)


@command(
    "/jobdiag",
    "/job-diagnostics",
    help="Show controller, worker and Git-preflight diagnostics for a durable engineering job.",
)
def job_diagnostics_cmd(
    session: Any,
    args: str,
    *,
    allow_prompt: bool = True,
) -> CommandResult:
    token = str(args or "").strip()
    if not token:
        message = "Usage: /jobdiag <job-id>"
        _emit(session, message, allow_prompt, error=True)
        return CommandResult(ok=False, message=message)

    service = get_default_job_service()
    try:
        job = _resolve_job(service, token)
    except (KeyError, ValueError) as exc:
        message = f"Job not found: {token}" if isinstance(exc, KeyError) else str(exc)
        _emit(session, message, allow_prompt, error=True)
        return CommandResult(ok=False, message=message)

    events = []
    for event in service.events(job.id, limit=1000):
        if event.event_type in _DIAGNOSTIC_EVENTS or (
            event.event_type == "status_changed"
            and event.to_status is not None
            and event.to_status.value == "environment_error"
        ):
            events.append(event)

    lines = [
        f"Job diagnostics · {job.id}",
        f"Status: {job.status.value}",
        f"Repository: {job.repository or '—'}",
        f"Requested base: {job.base_branch or '—'}",
        f"Resolved SHA: {job.base_sha or '—'}",
        f"Branch: {job.branch or '—'}",
        f"Worktree: {job.worktree or '—'}",
        "",
    ]
    if not events:
        lines.append("No diagnostic events recorded yet.")
    else:
        for event in reversed(events[-30:]):
            lines.append(f"[{event.id}] {event.event_type}")
            if event.reason:
                lines.append(f"  {event.reason}")
            payload = event.payload if isinstance(event.payload, dict) else {}
            compact = _compact_payload(payload)
            if compact != "{}":
                lines.extend(f"  {line}" for line in compact.splitlines())
            lines.append("")

    body = "\n".join(lines).rstrip()
    _emit(session, body, allow_prompt)
    return CommandResult(
        ok=True,
        message=f"{len(events)} diagnostic event(s).",
        data={
            "job": job.to_dict(),
            "diagnostics": [event.to_dict() for event in events],
        },
    )
