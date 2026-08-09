"""TUI/CLI control plane for durable engineering jobs."""

from __future__ import annotations

import shlex
from typing import Any, Dict, List

from mu.jobs import JobSpec, JobStateError, get_default_job_service
from mu.jobs.host import ensure_controller_daemon

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
    attention = f" · needs you: {job.attention_reason.value}" if job.needs_attention else ""
    return f"{job.id[:10]}  {job.status.value:<17}  {budget:<15}  {job.title}{attention}"


def _list_jobs(session: Any, status: str, allow_prompt: bool) -> CommandResult:
    service = get_default_job_service()
    try:
        jobs = service.list(statuses=[status] if status else None)
    except ValueError as exc:
        _emit(session, str(exc), allow_prompt, error=True)
        return CommandResult(ok=False, message=str(exc))
    lines = ["Durable engineering jobs", ""]
    if jobs:
        lines.extend(["ID          STATUS             COST             TITLE", "─" * 76])
        lines.extend(_format_job(job) for job in jobs)
    else:
        lines.append("No jobs found.")
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
    execution = job.execution or {}
    lines = [
        f"Job {job.id}",
        f"Status: {job.status.value}",
        f"Title: {job.title}",
        f"Repository: {job.repository or '—'}",
        f"Base: {job.base_branch}{('@' + job.base_sha[:12]) if job.base_sha else ''}",
        f"Runtime: {execution.get('provider') or '—'} / {execution.get('model') or '—'} · {execution.get('agent_mode') or 'default'} · {execution.get('session_type') or 'workspace'}",
        f"Write approval: {'automatic' if execution.get('auto_approve_writes') else 'human gate'}",
        f"Cost: ${job.cost_usd:.2f}{f' / ${job.max_cost_usd:.2f}' if job.max_cost_usd is not None else ''}",
        f"Attempts: {len(attempts)}",
        f"Events: {len(service.events(job.id))}",
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


def _session_defaults(session: Any) -> Dict[str, Any]:
    provider = getattr(getattr(session, "provider", None), "name", "") or ""
    model = getattr(getattr(session, "provider", None), "model_name", "") or ""
    variables = getattr(session, "variables", {}) or {}
    folders = list(getattr(getattr(session, "folder_context", None), "folders", []) or [])
    return {
        "repository": folders[0] if folders else "",
        "execution": {
            "provider": str(provider),
            "model": str(model),
            "agent_mode": str(variables.get("agent_mode", "default") or "default"),
            "session_type": str(variables.get("session_type", "workspace") or "workspace"),
            "auto_approve_writes": bool(variables.get("yolo", False)),
        },
    }


def _parse_create(raw: str, defaults: Dict[str, Any]) -> JobSpec:
    parts = shlex.split(raw or "")
    title_parts: List[str] = []
    execution = dict(defaults.get("execution") or {})
    values: Dict[str, Any] = {
        "repository": defaults.get("repository") or "",
        "acceptance_criteria": [],
        "validation_commands": [],
        "execution": execution,
    }
    index = 0
    while index < len(parts):
        token = parts[index]
        if not token.startswith("--"):
            title_parts.append(token)
            index += 1
            continue
        name = token[2:]
        if name in {"auto-approve", "no-auto-approve"}:
            execution["auto_approve_writes"] = name == "auto-approve"
            index += 1
            continue
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
        elif name == "provider":
            execution["provider"] = value
        elif name == "model":
            execution["model"] = value
        elif name == "mode":
            execution["agent_mode"] = value
        elif name == "session-type":
            execution["session_type"] = value
        else:
            raise ValueError(f"unknown option --{name}")
        index += 1
    values["title"] = " ".join(title_parts).strip()
    return JobSpec(**values).normalized()


def _create_job(session: Any, raw: str, allow_prompt: bool) -> CommandResult:
    try:
        spec = _parse_create(raw, _session_defaults(session))
        job = get_default_job_service().create(spec)
    except (TypeError, ValueError) as exc:
        message = f"Could not create job: {exc}"
        _emit(session, message, allow_prompt, error=True)
        return CommandResult(ok=False, message=message)

    host = ensure_controller_daemon()
    message = f"Queued job {job.id[:10]}: {job.title}"
    if host.running:
        message += f" · {host.detail}"
    else:
        message += f" · WARNING: {host.detail}; job remains queued"
    _emit(session, message, allow_prompt, error=not host.running)
    return CommandResult(ok=host.running, message=message, data={"job": job.to_dict(), "controller": host.__dict__})


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
    host = ensure_controller_daemon() if job.status.value == "queued" else None
    message = f"Job {job.id[:10]} → {job.status.value}"
    if host and not host.running:
        message += f" · WARNING: {host.detail}"
    _emit(session, message, allow_prompt, error=bool(host and not host.running))
    return CommandResult(ok=not host or host.running, message=message, data={"job": job.to_dict()})


@command(
    "/job",
    "/jobs",
    help=(
        "Durable engineering jobs: list [status], show <id>, create <title> "
        "[--repo PATH --provider NAME --model NAME --mode MODE --auto-approve "
        "--accept TEXT --check CMD --cost USD --runtime SEC], cancel|resume|retry <id>."
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
    return _show_job(session, raw, allow_prompt)
