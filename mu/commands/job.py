"""TUI/CLI control plane for durable engineering jobs.

Commands operate on the same SQLite-backed JobService used by GUI/mobile.
They do not require the GUI server to be running.
"""

from __future__ import annotations

import shlex
from typing import Any, Dict, List

from mu.jobs import JobSpec, JobStateError, JobStatus, get_default_job_service

from . import CommandResult, command


def _emit(session: Any, text: str, allow_prompt: bool, *, error: bool = False) -> None:
    ui = getattr(session, "ui", None)
    if not allow_prompt or ui is None:
        return
    method = "show_error" if error else "show_info"
    if hasattr(ui, method):
        getattr(ui, method)(text)


def _format_job(job) -> str:
    budget = f"${job.cost_usd:.2f}"
    if job.max_cost_usd is not None:
        budget += f" / ${job.max_cost_usd:.2f}"
    attention = (
        f" · needs you: {job.attention_reason.value}"
        if job.needs_attention
        else ""
    )
    return f"{job.id[:10]}  {job.status.value:<17}  {budget:<15}  {job.title}{attention}"


def _list_jobs(session: Any, status: str, allow_prompt: bool) -> CommandResult:
    service = get_default_job_service()
    statuses = [status] if status else None
    try:
        jobs = service.list(statuses=statuses)
    except ValueError as exc:
        message = str(exc)
        _emit(session, message, allow_prompt, error=True)
        return CommandResult(ok=False, message=message)
    lines = ["Durable engineering jobs", ""]
    if not jobs:
        lines.append("No jobs found.")
    else:
        lines.append("ID          STATUS             COST             TITLE")
        lines.append("─" * 76)
        lines.extend(_format_job(job) for job in jobs)
    body = "\n".join(lines)
    _emit(session, body, allow_prompt)
    return CommandResult(ok=True, message=f"{len(jobs)} job(s).", data={"jobs": [job.to_dict() for job in jobs]})


def _resolve_job(service, token: str):
    token = str(token or "").strip()
    if not token:
        raise KeyError(token)
    try:
        return service.get(token)
    except KeyError:
        matches = [job for job in service.list(limit=1000) if job.id.startswith(token)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(f"job id prefix {token!r} is ambiguous")
        raise


def _show_job(session: Any, token: str, allow_prompt: bool) -> CommandResult:
    service = get_default_job_service()
    try:
        job = _resolve_job(service, token)
    except (KeyError, ValueError) as exc:
        message = f"Job not found: {token}" if isinstance(exc, KeyError) else str(exc)
        _emit(session, message, allow_prompt, error=True)
        return CommandResult(ok=False, message=message)
    attempts = service.attempts(job.id)
    events = service.events(job.id)
    lines = [
        f"Job {job.id}",
        f"Status: {job.status.value}",
        f"Title: {job.title}",
        f"Repository: {job.repository or '—'}",
        f"Base: {job.base_branch}{('@' + job.base_sha[:12]) if job.base_sha else ''}",
        f"Cost: ${job.cost_usd:.2f}{f' / ${job.max_cost_usd:.2f}' if job.max_cost_usd is not None else ''}",
        f"Attempts: {len(attempts)}",
        f"Events: {len(events)}",
    ]
    if job.description:
        lines.extend(["", job.description])
    if job.attention_reason.value:
        lines.extend(["", f"Needs you: {job.attention_reason.value}", job.attention_detail])
    if job.acceptance_criteria:
        lines.extend(["", "Acceptance criteria:", *[f"  • {item}" for item in job.acceptance_criteria]])
    if job.validation_commands:
        lines.extend(["", "Validation:", *[f"  $ {item}" for item in job.validation_commands]])
    body = "\n".join(lines)
    _emit(session, body, allow_prompt)
    return CommandResult(ok=True, message=job.title, data={"job": job.to_dict(), "attempts": [a.to_dict() for a in attempts]})


def _parse_create(raw: str) -> JobSpec:
    parts = shlex.split(raw or "")
    title_parts: List[str] = []
    values: Dict[str, Any] = {
        "acceptance_criteria": [],
        "validation_commands": [],
    }
    index = 0
    while index < len(parts):
        token = parts[index]
        if not token.startswith("--"):
            title_parts.append(token)
            index += 1
            continue
        name = token[2:]
        if "=" in name:
            name, value = name.split("=", 1)
        else:
            if index + 1 >= len(parts):
                raise ValueError(f"missing value for --{name}")
            value = parts[index + 1]
            index += 1
        if name in {"repo", "repository"}:
            values["repository"] = value
        elif name in {"description", "desc"}:
            values["description"] = value
        elif name == "base":
            values["base_branch"] = value
        elif name == "base-sha":
            values["base_sha"] = value
        elif name == "accept":
            values["acceptance_criteria"].append(value)
        elif name in {"check", "validate"}:
            values["validation_commands"].append(value)
        elif name == "cost":
            values["max_cost_usd"] = float(value)
        elif name == "runtime":
            values["max_runtime_seconds"] = int(value)
        elif name == "iterations":
            values["max_iterations"] = int(value)
        elif name == "retries":
            values["max_retries"] = int(value)
        elif name == "subagents":
            values["max_subagents"] = int(value)
        else:
            raise ValueError(f"unknown option --{name}")
        index += 1
    values["title"] = " ".join(title_parts).strip()
    return JobSpec(**values).normalized()


def _create_job(session: Any, raw: str, allow_prompt: bool) -> CommandResult:
    try:
        spec = _parse_create(raw)
        job = get_default_job_service().create(spec)
    except (TypeError, ValueError) as exc:
        message = f"Could not create job: {exc}"
        _emit(session, message, allow_prompt, error=True)
        return CommandResult(ok=False, message=message)
    message = f"Queued job {job.id[:10]}: {job.title}"
    _emit(session, message, allow_prompt)
    return CommandResult(ok=True, message=message, data={"job": job.to_dict()})


def _control_job(session: Any, action: str, raw: str, allow_prompt: bool) -> CommandResult:
    token, _, detail = str(raw or "").strip().partition(" ")
    service = get_default_job_service()
    try:
        job = _resolve_job(service, token)
        if action == "cancel":
            job = service.cancel(job.id, reason=detail or "cancelled from TUI")
        elif action == "resume":
            job = service.resume(job.id, detail=detail)
        elif action == "retry":
            job = service.retry(job.id, reason=detail or "retry requested from TUI")
        else:
            raise ValueError(f"unknown action {action}")
    except (KeyError, ValueError, JobStateError) as exc:
        message = str(exc) if not isinstance(exc, KeyError) else f"Job not found: {token}"
        _emit(session, message, allow_prompt, error=True)
        return CommandResult(ok=False, message=message)
    message = f"Job {job.id[:10]} → {job.status.value}"
    _emit(session, message, allow_prompt)
    return CommandResult(ok=True, message=message, data={"job": job.to_dict()})


@command(
    "/job",
    "/jobs",
    help=(
        "Durable engineering jobs: list [status], show <id>, create <title> "
        "[--repo PATH --accept TEXT --check CMD --cost USD --runtime SEC], "
        "cancel|resume|retry <id>."
    ),
)
def job_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    raw = str(args or "").strip()
    if not raw:
        return _list_jobs(session, "", allow_prompt)
    head, _, rest = raw.partition(" ")
    sub = head.lower()
    rest = rest.strip()
    if sub == "list":
        return _list_jobs(session, rest, allow_prompt)
    if sub == "show":
        return _show_job(session, rest, allow_prompt)
    if sub == "create":
        return _create_job(session, rest, allow_prompt)
    if sub in {"cancel", "resume", "retry"}:
        return _control_job(session, sub, rest, allow_prompt)
    # /job <id> is a convenient shorthand for show.
    return _show_job(session, raw, allow_prompt)
