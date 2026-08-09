"""TUI diagnostics for durable engineering jobs."""

from __future__ import annotations

import json
from typing import Any

from mu.jobs import get_default_job_service
from mu.jobs.diagnostics import build_job_diagnostics

from . import CommandResult, command
from .job import _resolve_job


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
        "submitted_path",
        "canonical_path",
        "current_branch",
        "primary_branch",
        "detected_default_branch",
        "source_worktree_clean",
        "head_sha",
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
    help="Show controller, worker, Git-preflight and worker-log diagnostics for a durable engineering job.",
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
        snapshot = build_job_diagnostics(
            service,
            job.id,
            event_limit=500,
            log_tail_bytes=64 * 1024,
        )
    except (KeyError, ValueError) as exc:
        message = f"Job not found: {token}" if isinstance(exc, KeyError) else str(exc)
        _emit(session, message, allow_prompt, error=True)
        return CommandResult(ok=False, message=message)

    lines = [
        f"Job diagnostics · {job.id}",
        f"Status: {snapshot.status}",
        f"Repository: {snapshot.repository or '—'}",
        f"Requested base: {snapshot.base_branch or '—'}",
        f"Resolved SHA: {snapshot.base_sha or '—'}",
        f"Branch: {snapshot.branch or '—'}",
        f"Worktree: {snapshot.worktree or '—'}",
        f"Worker: {snapshot.worker_id or '—'}",
        f"Worker log: {snapshot.worker_log_path}",
        "",
    ]

    if snapshot.latest_failure:
        failure = snapshot.latest_failure
        lines.extend(
            [
                "Latest failure",
                f"  {failure.get('event_type', 'failure')}: {failure.get('reason', '')}",
            ]
        )
        payload = failure.get("payload") if isinstance(failure.get("payload"), dict) else {}
        compact = _compact_payload(payload)
        if compact != "{}":
            lines.extend(f"  {line}" for line in compact.splitlines())
        lines.append("")

    events = snapshot.diagnostics
    if not events:
        lines.append("No diagnostic events recorded yet.")
    else:
        lines.append(f"Execution trace · {len(events)} events")
        for event in reversed(events[-30:]):
            lines.append(f"[{event.get('id')}] {event.get('event_type')}")
            if event.get("reason"):
                lines.append(f"  {event.get('reason')}")
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            compact = _compact_payload(payload)
            if compact != "{}":
                lines.extend(f"  {line}" for line in compact.splitlines())
            lines.append("")

    if snapshot.worker_log_exists:
        lines.extend(
            [
                f"Worker log tail · {snapshot.worker_log_size} bytes"
                + (" · tail truncated" if snapshot.worker_log_truncated else ""),
                snapshot.worker_log_tail.rstrip() or "(empty log)",
            ]
        )
    else:
        lines.append("Worker log has not been created.")

    body = "\n".join(lines).rstrip()
    _emit(session, body, allow_prompt)
    return CommandResult(
        ok=True,
        message=f"{len(events)} diagnostic event(s).",
        data={"diagnostics": snapshot.to_dict()},
    )
