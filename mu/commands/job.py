"""TUI/CLI control plane for durable engineering jobs."""

from __future__ import annotations

import shlex
from typing import Any, Dict, List

from mu.jobs.core import vm, commands
from mu.jobs.core.vm import get_default_job_service
from mu.jobs.core.commands import ensure_controller_daemon

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
    board = vm.home_vm(get_default_job_service())
    lines = ["Engineering work", ""]
    for section, label in (("needs_you", "Needs you"), ("running", "Running now"), ("jobs", "Jobs")):
        lines.extend([f"{label} ({len(board[section])})", *[
            f"{j['id'][:10]} {j['mini']}  {j['state']} · ${j['cost_usd']:.2f} · {j['title']}"
            for j in board[section]], ""])
    _emit(session, "\n".join(lines), allow_prompt)
    return CommandResult(ok=True, message=f"{len(board['jobs'])} job(s).", data=board)


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
    receipt = vm.receipt_vm(service, job.id)
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
        job = vm.resolve_job(service, token)
        data = vm.job_vm(service, job.id)
    except (KeyError, ValueError) as exc:
        return CommandResult(ok=False, message=str(exc))
    report = data.get("review") or {}
    lines = [f"Job {job.id} · {job.title}", f"State: {data['state']}",
             " → ".join(f"{step['glyph']} {step['label']}" for step in data['pipeline']),
             f"Workspace: {job.worktree or job.repository}", f"Branch: {job.branch or '—'} · ${job.cost_usd:.2f}"]
    if report:
        lines.extend([f"Verdict: {report['verdict']} · {report['merge_readiness']}/100", report['summary']])
        lines.extend(f"  {f['id']} {f['severity']} {f['disposition']}: {f['title']} ({f.get('file') or '—'}:{f.get('line_start') or ''})" for f in report['findings'])
    import json
    approval = (data.get("session") or {}).get("approval")
    if approval:
        lines.extend(["Pending tool: " + approval["tool_name"], json.dumps(approval["tool_args"], indent=2)])
    lines.extend(f"  /job {a['id']} {job.id[:10]} {json.dumps(a['args'])}" for a in data['actions'] if a['enabled'] and a.get('args'))
    lines.append("Available: " + ", ".join(a['id'] for a in data['actions'] if a['enabled']))
    lines.extend(f"  {a['label']}: {a['reason']}" for a in data['actions'] if not a['enabled'])
    _emit(session, "\n".join(lines), allow_prompt)
    return CommandResult(ok=True, message=job.title, data=data)


def _show_receipt(session: Any, token: str, allow_prompt: bool) -> CommandResult:
    service = get_default_job_service()
    try:
        job = _resolve_job(service, token)
        receipt = vm.receipt_vm(service, job.id)
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
    try:
        service = get_default_job_service()
        job = vm.resolve_job(service, token)
        diff = vm.diff_vm(service, job.id)
        _emit(session, f"Diff · {job.title}\n{diff['stat']}\n{diff['patch']}", allow_prompt)
        return CommandResult(ok=True, message="Job diff", data={"diff": diff})
    except (KeyError, ValueError, RuntimeError) as exc:
        return CommandResult(ok=False, message=str(exc))


def _jira_job(session: Any, rest: str, allow_prompt: bool) -> CommandResult:
    """/job jira pull KEY [KEY...] | /job jira pull --jql "project = X" """

    try:
        tokens = shlex.split(rest or "")
    except ValueError as exc:
        _emit(session, f"Invalid arguments: {exc}", allow_prompt, error=True)
        return CommandResult(ok=False, message=str(exc), data={"error": str(exc)})
    if not tokens or tokens[0] not in {"pull", "import"}:
        message = 'Usage: /job jira pull KEY [KEY...] | /job jira pull --jql "<JQL>"'
        _emit(session, message, allow_prompt, error=True)
        return CommandResult(ok=False, message=message, data={"error": message})
    keys, jql, args = [], "", tokens[1:]
    i = 0
    while i < len(args):
        if args[i] == "--jql" and i + 1 < len(args):
            jql = args[i + 1]
            i += 2
            continue
        keys.append(args[i])
        i += 1
    try:
        result = commands.import_jira(
            get_default_job_service(),
            keys=keys, jql=jql, defaults=_session_defaults(session),
        )
    except (RuntimeError, TypeError, ValueError) as exc:
        _emit(session, f"Jira import failed: {exc}", allow_prompt, error=True)
        return CommandResult(ok=False, message=str(exc), data={"error": str(exc)})
    lines = [f"Jira import: {len(result['created'])} created, {len(result['skipped'])} skipped"]
    lines += [f"  + {c['key']} -> {c['job_id'][:10]} {c['title']}" for c in result["created"]]
    lines += [f"  = {s['key']} already has job {str(s.get('job_id', ''))[:10]}" for s in result["skipped"]]
    lines += [f"  ! {e['key']}: {e['error']}" for e in result["errors"]]
    _emit(session, "\n".join(lines), allow_prompt, error=bool(result["errors"]) and not result["created"])
    return CommandResult(ok=not (result["errors"] and not result["created"]), message="\n".join(lines), data=result)


def _session_defaults(session: Any) -> Dict[str, Any]:
    provider = getattr(getattr(session, "provider", None), "name", "") or ""
    model = getattr(getattr(session, "provider", None), "model_name", "") or ""
    variables = getattr(session, "variables", {}) or {}
    folders = list(getattr(getattr(session, "folder_context", None), "folders", []) or [])
    return vm.session_defaults(workspace=folders[0] if folders else "", provider=provider,
                               model=model, variables=variables)


def _parse_create(raw: str, defaults: Dict[str, Any]) -> Dict[str, Any]:
    parts = shlex.split(raw or "")
    title_parts: List[str] = []
    execution = dict(defaults.get("execution") or {})
    values: Dict[str, Any] = {
        "workspace_root": defaults.get("workspace_root") or "",
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
            raise ValueError("--repo is no longer supported; jobs use the session's current workspace")
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
    return values


def _create_job(session: Any, raw: str, allow_prompt: bool) -> CommandResult:
    try:
        payload = _parse_create(raw, _session_defaults(session))
        service = get_default_job_service()
        result = commands.dispatch(service, {"type": "create", "args": payload})
        job = service.get(result.job_id)
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
    try:
        service = get_default_job_service()
        job = vm.resolve_job(service, token)
        result = commands.respond(service, job.id, answer)
        ensure_controller_daemon()
        return CommandResult(ok=True, message="Response saved", data=result.to_dict())
    except (KeyError, ValueError, RuntimeError) as exc:
        return CommandResult(ok=False, message=str(exc))


def _review_action(session, action, raw, allow_prompt):
    import json
    token, _, detail = raw.partition(" ")
    try:
        service = get_default_job_service()
        job = vm.resolve_job(service, token)
        aliases = {"continue": "push", "changes": "reprompt", "cancel": "discard", "approve": "approvePlan", "replan": "rejectPlan"}
        kind = aliases.get(action, action)
        args = {"guidance": detail, "reason": detail, "feedback": detail, "text": detail, "override_reason": detail}
        if detail.startswith("{"):
            args = json.loads(detail)
        elif kind == "reprompt":
            ids, _, guidance = detail.partition(" ")
            args = {"finding_ids": [x for x in ids.split(",") if x], "guidance": guidance}
        elif kind == "approvePlan":
            args = {"version": int(detail)}
        elif kind == "savePlanDraft":
            from pathlib import Path
            args = {"plan": Path(detail).read_text(), "version": vm.job_vm(service, job.id, parts=["plan"])["plan"]["version"]}
        result = commands.dispatch(service, {"type": kind, "job_id": job.id, "args": args})
        ensure_controller_daemon()
        _emit(session, f"{kind}: {job.id[:10]}", allow_prompt)
        return CommandResult(ok=True, message=kind, data=result.to_dict())
    except (KeyError, ValueError, RuntimeError, OSError) as exc:
        _emit(session, str(exc), allow_prompt, error=True)
        return CommandResult(ok=False, message=str(exc))


def _delete_job(session: Any, raw: str, allow_prompt: bool) -> CommandResult:
    """`/job delete <id> [--yes]` — single-step cancel → archive → purge.

    Destructive: removes the job record, telemetry, evidence and managed
    worktree. The Git branch is preserved. Interactive sessions confirm via
    ``ui.confirm``; non-interactive callers must pass ``--yes``/``-y``."""
    tokens = [part for part in str(raw or "").split() if part]
    assume_yes = any(part in {"--yes", "-y", "--force"} for part in tokens)
    ids = [part for part in tokens if part not in {"--yes", "-y", "--force"}]
    if not ids:
        message = "Usage: /job delete <id> [--yes]"
        _emit(session, message, allow_prompt, error=True)
        return CommandResult(ok=False, message=message)
    token = ids[0]
    service = get_default_job_service()
    try:
        job = _resolve_job(service, token)
    except (KeyError, ValueError) as exc:
        message = str(exc) if not isinstance(exc, KeyError) else f"Job not found: {token}"
        _emit(session, message, allow_prompt, error=True)
        return CommandResult(ok=False, message=message)

    if not assume_yes:
        ui = getattr(session, "ui", None)
        confirm = getattr(ui, "confirm", None) if allow_prompt else None
        prompt = (
            f"Delete job {job.id[:12]} \u00b7 {job.title!r} ({job.status.value})? "
            "Running work is cancelled first; the record, telemetry, evidence and managed "
            "worktree are removed. The Git branch is preserved."
        )
        if not callable(confirm):
            message = (
                f"Refusing to delete job {job.id[:12]} without confirmation. "
                "Re-run as `/job delete <id> --yes`."
            )
            _emit(session, message, allow_prompt, error=True)
            return CommandResult(ok=False, message=message, data={"job_id": job.id, "deleted": False})
        if not confirm(prompt, default=False):
            message = f"Kept job {job.id[:12]}."
            _emit(session, message, allow_prompt)
            return CommandResult(ok=True, message=message, data={"job_id": job.id, "deleted": False})

    try:
        result = commands.dispatch(service, {"type": "delete", "job_id": job.id}).data
    except (KeyError, RuntimeError, ValueError) as exc:
        message = str(exc) if not isinstance(exc, KeyError) else f"Job not found: {token}"
        _emit(session, message, allow_prompt, error=True)
        return CommandResult(ok=False, message=message, data={"job_id": job.id, "deleted": False})

    parts = [f"Deleted job {job.id[:12]} \u00b7 {job.title}"]
    if result.get("branch_preserved"):
        parts.append(f"branch preserved: {result['branch_preserved']}")
    if result.get("worktree_removed"):
        parts.append("worktree removed")
    warnings = list(result.get("warnings") or [])
    message = " \u00b7 ".join(parts)
    if warnings:
        message += "\n" + "\n".join(f"WARNING: {item}" for item in warnings)
    _emit(session, message, allow_prompt, error=bool(warnings))
    return CommandResult(ok=True, message=message, data=result)


@command(
    "/job",
    "/jobs",
    help=(
        "Engineering jobs: board, summary [N|all], list [status], show|receipt|diff <id>, "
        "create <title> [--provider NAME --model NAME --accept TEXT --check CMD], "
        "approve <id> <version>, savePlanDraft <id> <file>, reprompt <id> F-1,F-2 [guidance], "
        "message|resume|restartStage|retry|recheck|push|markMerged <id> [args JSON], "
        "delete <id> [--yes] (cancel \u2192 archive \u2192 purge; branch preserved)."
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
    if sub in {"changes", "continue", "discard", "cancel", "resume", "retry", "approve", "replan"} or head in {"approvePlan", "rejectPlan", "savePlanDraft", "message", "restartStage", "recheck", "reprompt", "markFindings", "push", "markMerged", "approveTool", "denyTool"}:
        return _review_action(session, head, rest, allow_prompt)
    if sub == "jira":
        return _jira_job(session, rest, allow_prompt)
    if sub in {"delete", "rm"}:
        return _delete_job(session, rest, allow_prompt)
    return _show_job(session, raw, allow_prompt)
