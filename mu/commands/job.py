"""TUI/CLI control plane for durable engineering jobs."""

from __future__ import annotations

import shlex
from typing import Any, Dict, List

from mu.jobs import JobSpec, JobStateError, get_default_job_service
from mu.jobs.board import BOARD_ORDER, build_job_board
from mu.jobs.host import ensure_controller_daemon
from mu.jobs.receipt import JobReceiptBuilder
from mu.jobs.review import JobReviewError, JobReviewService, build_job_diff

from . import CommandResult, command


BOARD_LABELS = {
    "needs_you": "Needs you",
    "running": "Running",
    "queued": "Queued",
    "ready": "Ready for review",
    "failed": "Failed",
    "done": "Done",
}


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
        budget += f"/${job.max_cost_usd:.2f}"
    attention = f" · {job.attention_reason.value}" if job.needs_attention else ""
    return f"{job.id[:10]}  {job.status.value:<17}  {budget:<13}  {job.title}{attention}"


def attention_banner(*, limit: int = 5) -> str:
    """One-glance "N jobs need you" text for TUI startup and `/jobs`.
    Empty string when nothing is waiting or the job store is unavailable
    (never raises — startup must not depend on the jobs plane)."""
    try:
        summary = get_default_job_service().attention_summary()
    except Exception:
        return ""
    count = int(summary.get("count", 0) or 0)
    if count <= 0:
        return ""
    lines = [f"\u26a0 {count} engineering job{'s' if count != 1 else ''} need you \u2014 /job show <id> \u00b7 /job respond <id> ..."]
    for item in summary.get("jobs", [])[:limit]:
        why = str(item.get("attention_reason") or item.get("status") or "").replace("_", " ")
        detail = str(item.get("attention_detail") or "").strip().splitlines()[0:1]
        detail_text = f" \u2014 {detail[0][:80]}" if detail else ""
        lines.append(f"  {str(item.get('id', ''))[:10]}  {why:<20} {str(item.get('title', ''))[:48]}{detail_text}")
    if count > limit:
        lines.append(f"  \u2026 {count - limit} more")
    return "\n".join(lines)


def _board_jobs(session: Any, allow_prompt: bool) -> CommandResult:
    board = build_job_board(get_default_job_service())
    lines = ["Engineering work", ""]
    banner = attention_banner()
    if banner:
        lines.extend([banner, ""])
    for section in BOARD_ORDER:
        jobs = getattr(board, section)
        if not jobs:
            continue
        lines.extend([f"{BOARD_LABELS[section]} ({len(jobs)})", "─" * 76])
        lines.extend(_format_job(job) for job in jobs)
        lines.append("")
    if not any(board.counts.values()):
        lines.append("No jobs found.")
    body = "\n".join(lines).rstrip()
    _emit(session, body, allow_prompt)
    return CommandResult(ok=True, message=f"{sum(board.counts.values())} job(s).", data=board.to_dict())


def _list_jobs(session: Any, status: str, allow_prompt: bool) -> CommandResult:
    if not status:
        return _board_jobs(session, allow_prompt)
    service = get_default_job_service()
    try:
        jobs = service.list(statuses=[status])
    except ValueError as exc:
        _emit(session, str(exc), allow_prompt, error=True)
        return CommandResult(ok=False, message=str(exc))
    lines = [f"Jobs · {status}", "", "ID          STATUS             COST           TITLE", "─" * 76]
    lines.extend(_format_job(job) for job in jobs)
    if not jobs:
        lines.append("No jobs found.")
    body = "\n".join(lines)
    _emit(session, body, allow_prompt)
    return CommandResult(ok=True, message=f"{len(jobs)} job(s).", data={"jobs": [job.to_dict() for job in jobs]})


def _summary_row(service, job) -> list[str]:
    """Five-line digest: what happened, what it cost, what it needs."""
    receipt = JobReceiptBuilder(service).build(job.id)
    outcome = receipt.get("outcome") or {}
    git = receipt.get("git") or {}
    verification = receipt.get("verification") or {}
    drift = receipt.get("base_drift") or {}
    acceptance = receipt.get("acceptance_review") or {}
    checks = verification.get("checks") or []
    passed = sum(1 for c in checks if c.get("passed"))
    if job.status.value == "ready_for_review":
        headline = "READY TO REVIEW"
    elif job.needs_attention:
        headline = f"NEEDS YOU · {job.attention_reason.value.replace('_', ' ')}"
    else:
        headline = job.status.value.replace("_", " ").upper()
    verify_text = (
        f"{verification.get('status')} ({passed}/{len(checks)} checks)"
        if verification else "not run"
    )
    if acceptance:
        verify_text += f" · acceptance {acceptance.get('verdict', '?')} {acceptance.get('met', 0)}/{acceptance.get('total', 0)}"
    merge_text = ""
    if drift:
        if drift.get("mergeable") is False:
            merge_text = f" · CONFLICTS with {drift.get('base_ref')}"
        elif drift.get("drifted"):
            merge_text = f" · {drift.get('commits_behind', 0)} behind {drift.get('base_ref')}, merges"
    lines = [
        f"{job.id[:10]}  {headline}  ·  {job.title}",
        f"    {_fmt_elapsed(outcome.get('elapsed_seconds', 0))} · ${float(job.cost_usd or 0):.2f} · "
        f"{outcome.get('attempts', 0)} attempt(s) · {len(git.get('changed_files') or [])} files "
        f"+{git.get('additions', 0)}/-{git.get('deletions', 0)}",
        f"    verify: {verify_text}{merge_text}",
        f"    branch: {job.branch or '—'} @ {str(git.get('head_sha') or '—')[:12]}",
    ]
    if job.needs_attention and job.attention_detail:
        lines.append(f"    → {job.attention_detail.strip().splitlines()[0][:110]}")
    elif job.status.value == "ready_for_review":
        lines.append(f"    → /job diff {job.id[:10]} · /job changes {job.id[:10]} <feedback>")
    else:
        last_error = ""
        for attempt in reversed(receipt.get("attempts") or []):
            if attempt.get("error"):
                last_error = str(attempt["error"]).splitlines()[0][:110]
                break
        lines.append(f"    → {last_error or 'no action needed'}")
    return lines


def _summary(session: Any, raw: str, allow_prompt: bool) -> CommandResult:
    """`/job summary [N|all]` — the <2-minute view: every non-archived job
    as a five-line block, attention first, then ready, then the rest."""
    service = get_default_job_service()
    token = str(raw or "").strip().lower()
    limit = 10
    if token == "all":
        limit = 200
    elif token.isdigit():
        limit = max(1, min(int(token), 200))
    jobs = service.list_unarchived(limit=200)
    order = {"needs_human": 0, "conflicted": 0, "ready_for_review": 1, "running": 2, "verifying": 2,
             "preparing": 2, "recovering": 2, "queued": 3}
    jobs.sort(key=lambda j: (order.get(j.status.value, 4), -(j.updated_at or 0)))
    jobs = jobs[:limit]
    if not jobs:
        _emit(session, "No jobs.", allow_prompt)
        return CommandResult(ok=True, message="0 job(s).", data={"jobs": []})
    attention = sum(1 for j in jobs if j.status.value in {"needs_human", "conflicted"})
    ready = sum(1 for j in jobs if j.status.value == "ready_for_review")
    cost = sum(float(j.cost_usd or 0) for j in jobs)
    lines = [f"Engineering work summary · {len(jobs)} job(s) · {attention} need you · {ready} ready · ${cost:.2f} total", ""]
    for job in jobs:
        lines.extend(_summary_row(service, job))
        lines.append("")
    _emit(session, "\n".join(lines).rstrip(), allow_prompt)
    return CommandResult(
        ok=True,
        message=f"{len(jobs)} job(s) · {attention} need you · {ready} ready",
        data={"jobs": [j.to_dict() for j in jobs], "attention": attention, "ready": ready, "cost_usd": cost},
    )


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


def _fmt_elapsed(seconds: float) -> str:
    total = max(0, int(seconds or 0))
    minutes, sec = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m {sec}s" if hours else f"{minutes}m {sec}s"


def _show_job(session: Any, token: str, allow_prompt: bool) -> CommandResult:
    service = get_default_job_service()
    try:
        job = _resolve_job(service, token)
    except (KeyError, ValueError) as exc:
        message = f"Job not found: {token}" if isinstance(exc, KeyError) else str(exc)
        _emit(session, message, allow_prompt, error=True)
        return CommandResult(ok=False, message=message)
    receipt = JobReceiptBuilder(service).build(job.id)
    execution = job.execution or {}
    git = receipt.get("git") or {}
    verification = receipt.get("verification") or {}
    lines = [
        f"Job {job.id}",
        f"Status: {job.status.value}",
        f"Title: {job.title}",
        f"Repository: {job.repository or '—'}",
        f"Branch: {job.branch or '—'}",
        f"Base → head: {(job.base_sha or '—')[:12]} → {str(git.get('head_sha') or '—')[:12]}",
        f"Runtime: {execution.get('provider') or '—'} / {execution.get('model') or '—'} · {execution.get('agent_mode') or 'default'}",
        f"Cost: ${job.cost_usd:.2f}{f' / ${job.max_cost_usd:.2f}' if job.max_cost_usd is not None else ''}",
        f"Elapsed: {_fmt_elapsed((receipt.get('outcome') or {}).get('elapsed_seconds', 0))}",
        f"Attempts: {(receipt.get('outcome') or {}).get('attempts', 0)}",
        f"Changes: {len(git.get('changed_files') or [])} files · +{git.get('additions', 0)} / -{git.get('deletions', 0)}",
        f"Verification: {verification.get('status') or 'not run'}",
    ]
    drift = receipt.get("base_drift") or {}
    if drift:
        merge_state = {True: "merges cleanly", False: "CONFLICTS", None: "unknown"}[drift.get("mergeable")]
        lines.append(
            f"Base drift: {'yes' if drift.get('drifted') else 'no'} "
            f"({drift.get('commits_behind', 0)} behind {drift.get('base_ref') or job.base_branch}) · {merge_state}"
        )
    acceptance = receipt.get("acceptance_review") or {}
    if acceptance:
        lines.append(
            f"Acceptance review: {acceptance.get('verdict') or 'unknown'} "
            f"({acceptance.get('met', 0)}/{acceptance.get('total', 0)} criteria met)"
        )
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
    return CommandResult(ok=True, message=job.title, data={"job": job.to_dict(), "receipt": receipt})


def _show_receipt(session: Any, token: str, allow_prompt: bool) -> CommandResult:
    service = get_default_job_service()
    try:
        job = _resolve_job(service, token)
        receipt = JobReceiptBuilder(service).build(job.id)
    except (KeyError, ValueError) as exc:
        message = f"Job not found: {token}" if isinstance(exc, KeyError) else str(exc)
        _emit(session, message, allow_prompt, error=True)
        return CommandResult(ok=False, message=message)
    outcome = receipt.get("outcome") or {}
    git = receipt.get("git") or {}
    verification = receipt.get("verification") or {}
    activity = receipt.get("activity") or {}
    lines = [
        f"Work receipt · {job.title}",
        "",
        f"Outcome: {job.status.value}{' · READY TO REVIEW' if outcome.get('ready_for_review') else ''}",
        f"Worked: {_fmt_elapsed(outcome.get('elapsed_seconds', 0))}",
        f"Cost: ${float(outcome.get('cost_usd') or 0):.2f}",
        f"Attempts: {outcome.get('attempts', 0)}",
        "",
        f"Branch: {git.get('branch') or '—'}",
        f"Base: {str(git.get('base_sha') or '—')[:12]}",
        f"Head: {str(git.get('head_sha') or '—')[:12]}",
        f"Changed: {len(git.get('changed_files') or [])} files · +{git.get('additions', 0)} / -{git.get('deletions', 0)}",
        f"Clean worktree: {'yes' if git.get('dirty') is False else 'no/unknown'}",
        "",
        f"Verification: {verification.get('status') or 'not run'}",
    ]
    for check in verification.get("checks") or []:
        icon = "✓" if check.get("passed") else "✗"
        lines.append(f"  {icon} {check.get('command')} · {check.get('duration_seconds', 0):.2f}s")
    lines.extend([
        "",
        f"Activity: {activity.get('tool_calls', 0)} tools · {activity.get('checkpoints', 0)} checkpoints · {activity.get('human_responses', 0)} human responses",
    ])
    body = "\n".join(lines)
    _emit(session, body, allow_prompt)
    return CommandResult(ok=True, message="Work receipt", data={"receipt": receipt})


def _show_diff(session: Any, token: str, allow_prompt: bool) -> CommandResult:
    service = get_default_job_service()
    try:
        job = _resolve_job(service, token)
        diff = build_job_diff(service, job.id)
    except (KeyError, ValueError, JobReviewError) as exc:
        message = f"Job not found: {token}" if isinstance(exc, KeyError) else str(exc)
        _emit(session, message, allow_prompt, error=True)
        return CommandResult(ok=False, message=message)
    body = "\n".join([
        f"Diff · {job.title}",
        f"{diff.base_sha[:12]} → {diff.head_sha[:12]} · {len(diff.files)} files",
        diff.stat or "No changed files.",
        "",
        diff.patch or "No diff.",
    ])
    _emit(session, body, allow_prompt)
    return CommandResult(ok=True, message=f"{len(diff.files)} changed file(s).", data={"diff": diff.to_dict()})


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


def _respond_job(session: Any, raw: str, allow_prompt: bool) -> CommandResult:
    token, _, answer = str(raw or "").strip().partition(" ")
    service = get_default_job_service()
    try:
        job = _resolve_job(service, token)
        if not answer.strip():
            raise JobReviewError("Response text or approval decision is required.")
        review = JobReviewService(service)
        if job.attention_reason.value == "approval_required":
            decision, _, detail = answer.strip().partition(" ")
            updated = review.respond(job.id, decision=decision, detail=detail)
        else:
            context = {}
            for event in reversed(service.events(job.id)):
                if event.to_status and event.to_status.value == "needs_human":
                    context = dict(event.payload or {})
                    break
            shape = str(context.get("shape") or "input")
            value: Any = answer.strip()
            selected: List[Any] = []
            decision = ""
            if shape == "confirm":
                normalized = answer.strip().lower()
                value = normalized in {"y", "yes", "true", "confirm", "approve"}
            elif shape in {"choices", "choice"}:
                selected = [answer.strip()]
            updated = review.respond(job.id, detail=answer.strip(), decision=decision, value=value, selected=selected)
    except (KeyError, ValueError, JobStateError, JobReviewError) as exc:
        message = str(exc) if not isinstance(exc, KeyError) else f"Job not found: {token}"
        _emit(session, message, allow_prompt, error=True)
        return CommandResult(ok=False, message=message)
    host = ensure_controller_daemon()
    message = f"Response saved · job {updated.id[:10]} → {updated.status.value}"
    if not host.running:
        message += f" · WARNING: {host.detail}"
    _emit(session, message, allow_prompt, error=not host.running)
    return CommandResult(ok=host.running, message=message, data={"job": updated.to_dict()})


def _review_action(session: Any, action: str, raw: str, allow_prompt: bool) -> CommandResult:
    token, _, detail = str(raw or "").strip().partition(" ")
    service = get_default_job_service()
    try:
        job = _resolve_job(service, token)
        review = JobReviewService(service)
        if action == "changes":
            updated = review.request_changes(job.id, detail)
        elif action == "continue":
            updated = review.continue_job(job.id, detail)
        elif action == "discard":
            updated = review.discard(job.id, detail)
        elif action == "cancel":
            updated = service.cancel(job.id, reason=detail or "cancelled from TUI")
        elif action == "resume":
            updated = service.resume(job.id, detail=detail)
        elif action == "retry":
            updated = service.retry(job.id, reason=detail or "retry requested from TUI")
        else:
            raise ValueError(f"unknown action {action}")
    except (KeyError, ValueError, JobStateError, JobReviewError) as exc:
        message = str(exc) if not isinstance(exc, KeyError) else f"Job not found: {token}"
        _emit(session, message, allow_prompt, error=True)
        return CommandResult(ok=False, message=message)
    host = ensure_controller_daemon() if updated.status.value == "queued" else None
    message = f"Job {updated.id[:10]} → {updated.status.value}"
    if host and not host.running:
        message += f" · WARNING: {host.detail}"
    _emit(session, message, allow_prompt, error=bool(host and not host.running))
    return CommandResult(ok=not host or host.running, message=message, data={"job": updated.to_dict()})


@command(
    "/job",
    "/jobs",
    help=(
        "Engineering jobs: board, summary [N|all], list [status], show|receipt|diff <id>, "
        "create <title> [--repo PATH --provider NAME --model NAME --accept TEXT --check CMD], "
        "respond <id> <answer>, changes <id> <feedback>, continue|discard <id> [detail]."
    ),
)
def job_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    raw = str(args or "").strip()
    if not raw:
        return _board_jobs(session, allow_prompt)
    head, _, rest = raw.partition(" ")
    sub = head.lower()
    rest = rest.strip()
    if sub == "board":
        return _board_jobs(session, allow_prompt)
    if sub in {"summary", "digest"}:
        return _summary(session, rest, allow_prompt)
    if sub == "list":
        return _list_jobs(session, rest, allow_prompt)
    if sub == "show":
        return _show_job(session, rest, allow_prompt)
    if sub == "receipt":
        return _show_receipt(session, rest, allow_prompt)
    if sub == "diff":
        return _show_diff(session, rest, allow_prompt)
    if sub == "create":
        return _create_job(session, rest, allow_prompt)
    if sub == "respond":
        return _respond_job(session, rest, allow_prompt)
    if sub in {"changes", "continue", "discard", "cancel", "resume", "retry"}:
        return _review_action(session, sub, rest, allow_prompt)
    return _show_job(session, raw, allow_prompt)
