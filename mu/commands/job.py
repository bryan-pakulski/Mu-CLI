"""TUI/CLI control plane for durable engineering jobs."""

from __future__ import annotations

import json
import os
import re
import shlex
from typing import Any, Dict, List, Optional, Tuple

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


def _short(text: Any, width: int) -> str:
    """Truncate to ``width`` characters with an ellipsis (80-column friendly)."""
    text = str(text or "")
    return text if len(text) <= width else text[: max(0, width - 1)] + "\u2026"


def _origin(job_or_row: Any) -> str:
    if isinstance(job_or_row, dict):
        return str(job_or_row.get("origin_session") or "")
    return str((getattr(job_or_row, "metadata", None) or {}).get("origin_session") or "")


def _format_job(job) -> str:
    budget = f"${job.cost_usd:.2f}"
    if job.max_cost_usd is not None:
        budget += f"/${job.max_cost_usd:.2f}"
    attention = f" · {job.attention_reason.value}" if job.needs_attention else ""
    origin = _short(_origin(job) or "-", 14)
    return f"{job.id[:10]}  {job.status.value:<17}  {budget:<13}  {origin:<14}  {job.title}{attention}"


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
    lines = ["Engineering work \u00b7 all sessions", ""]
    for section, label in (("needs_you", "Needs you"), ("running", "Running now"), ("jobs", "Jobs")):
        lines.extend([f"{label} ({len(board[section])})", *[
            f"{j['id'][:10]} {j['mini']}  {j['state']} · ${j['cost_usd']:.2f} · "
            f"{_short(_origin(j) or '-', 16)} · {j['title']}"
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
    lines = [f"Jobs · {status}", "", "ID          STATUS             COST           SESSION         TITLE", "─" * 78]
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
    """/job jira pull KEY [KEY...] | /job jira pull --jql "project = X" [--session NAME] [--in PATH] [--force]
    [--template NAME [--remember]]

    ``--force`` starts a new job even for tickets whose latest job is done."""

    try:
        tokens = shlex.split(rest or "")
    except ValueError as exc:
        _emit(session, f"Invalid arguments: {exc}", allow_prompt, error=True)
        return CommandResult(ok=False, message=str(exc), data={"error": str(exc)})
    if not tokens or tokens[0] not in {"pull", "import"}:
        message = ('Usage: /job jira pull KEY [KEY...] | /job jira pull --jql "<JQL>" [--session NAME] [--in PATH] [--force] '
                   '[--template NAME [--remember]]')
        _emit(session, message, allow_prompt, error=True)
        return CommandResult(ok=False, message=message, data={"error": message})
    try:
        session_name, defaults, args = _resolve_target(session, tokens[1:], required=False)
    except (OSError, ValueError) as exc:
        _emit(session, f"Jira import failed: {exc}", allow_prompt, error=True)
        return CommandResult(ok=False, message=str(exc), data={"error": str(exc)})
    keys, jql, force = [], "", False
    template, remember = "", False
    i = 0
    while i < len(args):
        if args[i] in ("--force", "-f"):
            force = True
            i += 1
            continue
        if args[i] == "--remember":
            remember = True
            i += 1
            continue
        if args[i].startswith("--template="):
            template = template_name(args[i].split("=", 1)[1])
            i += 1
            continue
        if args[i] == "--template" and i + 1 < len(args):
            template = template_name(args[i + 1])
            i += 2
            continue
        if args[i] == "--jql" and i + 1 < len(args):
            jql = args[i + 1]
            i += 2
            continue
        keys.append(args[i])
        i += 1
    if remember and not template:
        message = "--remember needs --template NAME"
        _emit(session, f"Jira import failed: {message}", allow_prompt, error=True)
        return CommandResult(ok=False, message=message, data={"error": message})
    defaults = dict(defaults)
    if template:
        # "Run in" for every imported ticket (job_templates.md §3.2).
        defaults["execution"] = {**dict(defaults.get("execution") or {}), "template": template,
                                 **({"remember_template": True} if remember else {})}
        # An unknown template fails the whole import ("Unknown template '<name>'", core check).
    try:
        result = commands.import_jira(
            get_default_job_service(),
            keys=keys, jql=jql, defaults=_with_origin(dict(defaults), session_name),
            **({"force": True} if force else {}),
        )
    except (RuntimeError, TypeError, ValueError) as exc:
        _emit(session, f"Jira import failed: {exc}", allow_prompt, error=True)
        return CommandResult(ok=False, message=str(exc), data={"error": str(exc)})
    lines = [f"Jira import: {len(result['created'])} created, {len(result['skipped'])} skipped"]
    lines += [f"  + {c['key']} -> {c['job_id'][:10]} {c['title']}" for c in result["created"]]
    lines += [f"  = {s['key']} already has job {str(s.get('job_id', ''))[:10]}"
              + (" (done; add --force to run it again)" if "latest job is done" in str(s.get("reason") or "") else "")
              for s in result["skipped"]]
    lines += [f"  ! {e['key']}: {e['error']}" for e in result["errors"]]
    _emit(session, "\n".join(lines), allow_prompt, error=bool(result["errors"]) and not result["created"])
    return CommandResult(ok=not (result["errors"] and not result["created"]), message="\n".join(lines), data=result)


# Jobs are one management plane across sessions. These mirror the GUI's
# saved-session rules (``/api/jobs/sessions``) without importing the web layer.
_SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_JOB_WORKER_RE = re.compile(r"^job-[0-9a-f]{12,}")


def _sessions_root() -> str:
    from utils import config as _config  # resolved per call so MUCLI_HOME/test overrides apply

    return os.path.join(_config.HISTORY_DIR, "sessions")


def _saved_session(name: str) -> Optional[Dict[str, Any]]:
    """Workspace/model/policy of a saved user session (``None`` if missing or a job worker)."""
    name = str(name or "").strip()
    if not _SESSION_NAME_RE.match(name) or _JOB_WORKER_RE.match(name):
        return None
    path = os.path.join(_sessions_root(), name, "session.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    provider_config = dict(data.get("provider_config") or {})
    folders = [str(f) for f in (dict(data.get("folder_context") or {}).get("folders") or []) if f]
    variables = dict(data.get("variables") or {})
    return {"name": name, "folders": folders, "workspace": folders[0] if folders else "",
            "provider": str(provider_config.get("provider") or ""),
            "model": str(provider_config.get("model") or ""), "variables": variables,
            "agent_mode": str(variables.get("agent_mode") or "default"),
            "yolo": bool(variables.get("yolo", False)),
            "session_type": str(variables.get("session_type") or "workspace"),
            "updated_at": os.path.getmtime(path)}


def _current_session_name(session: Any) -> str:
    manager = getattr(session, "session_manager", None)
    return str(getattr(manager, "current_session_name", "") or "")


def list_saved_sessions(current: str = "") -> List[Dict[str, Any]]:
    """Every saved user session (job workers excluded), current first then most recent."""
    root = _sessions_root()
    try:
        names = sorted(os.listdir(root))
    except OSError:
        names = []
    out = []
    for name in names:
        saved = _saved_session(name)
        if saved is not None:
            saved["current"] = name == current
            out.append(saved)
    out.sort(key=lambda s: (not s["current"], -s["updated_at"]))
    return out


def _resolve_session(session: Any, name: str, *, required: bool = True) -> Tuple[str, Dict[str, Any]]:
    """``(session_name, job defaults)`` for ``--session NAME`` or the current CLI session.

    The current session uses its live state; any other saved session supplies the
    workspace, model and policy from its session file (it need not be loaded)."""
    current = _current_session_name(session)
    name = str(name or "").strip()
    if not name:
        if not current:
            if required:
                raise ValueError("no current session; pass --session <name> (see /job sessions)")
            return "", _session_defaults(session)
        name = current
    if name == current:
        return name, _session_defaults(session)
    saved = _saved_session(name)
    if saved is None:
        raise ValueError(f"session '{name}' does not exist (see /job sessions)")
    return name, vm.session_defaults(workspace=saved["workspace"], provider=saved["provider"],
                                     model=saved["model"], variables=saved["variables"])


def _pop_session_option(parts: List[str]) -> Tuple[str, List[str]]:
    """Strip ``--session NAME`` / ``--session=NAME`` from argv-style ``parts``."""
    rest: List[str] = []
    name = ""
    index = 0
    while index < len(parts):
        token = parts[index]
        if token == "--session":
            if index + 1 >= len(parts):
                raise ValueError("missing value for --session")
            name = parts[index + 1]
            index += 2
            continue
        if token.startswith("--session="):
            name = token.split("=", 1)[1]
            index += 1
            continue
        rest.append(token)
        index += 1
    return name.strip(), rest


def _with_origin(payload: Dict[str, Any], name: str) -> Dict[str, Any]:
    if name:
        payload["metadata"] = {**dict(payload.get("metadata") or {}), "origin_session": name}
    return payload


# ``--in PATH`` targets a workspace without a chat session: jobs are grouped under
# one hidden, reusable session per workspace (kept out of chat session lists).
WORKSPACE_SESSION_KIND = "job_workspace"


def workspace_session(path: str) -> Tuple[str, str]:
    """``(session_name, workspace)`` of the hidden session for ``path``, created on first use.

    ``path`` may be a repository URL (``https://``, ``ssh://``, ``git@host:org/repo``,
    ``file://``, optional ``@branch``): it is cloned (or refreshed) under
    ``$MUCLI_HOME/repos`` and the clone becomes the workspace."""
    import hashlib

    raw = str(path or "").strip()
    if not raw:
        raise ValueError("missing value for --in")
    if commands.is_repository_url(raw):
        raw = commands.ensure_repository_clone(raw).path  # CloneError (a ValueError) is actionable
    workspace = os.path.realpath(os.path.expanduser(raw))
    if not os.path.isdir(workspace):
        raise ValueError(f"--in {raw}: not a directory")
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", os.path.basename(workspace)).strip("-._")[:40] or "workspace"
    name = f"jobs-{slug}-{hashlib.sha1(workspace.encode('utf-8')).hexdigest()[:8]}"
    folder = os.path.join(_sessions_root(), name)
    target = os.path.join(folder, "session.json")
    if not os.path.isfile(target):
        os.makedirs(folder, exist_ok=True)
        data = {
            "provider_config": {},
            "folder_context": {"folders": [workspace]},
            "variables": {"session_type": "workspace", "internal_session_kind": WORKSPACE_SESSION_KIND,
                          "job_workspace": workspace},
            "history": [],
        }
        tmp = f"{target}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        os.replace(tmp, target)
    return name, workspace


_IN_FLAGS = ("--in", "--repo", "--repository")


def _pop_in_option(parts: List[str]) -> Tuple[str, List[str]]:
    """Strip ``--in PATH|URL`` / ``--in=...`` (alias ``--repo``/``--repository``) from ``parts``."""
    rest: List[str] = []
    path = ""
    index = 0
    while index < len(parts):
        token = parts[index]
        if token in _IN_FLAGS:
            if index + 1 >= len(parts):
                raise ValueError(f"missing value for {token}")
            path = parts[index + 1]
            index += 2
            continue
        if token.startswith(tuple(f"{flag}=" for flag in _IN_FLAGS)):
            path = token.split("=", 1)[1]
            index += 1
            continue
        rest.append(token)
        index += 1
    return path.strip(), rest


def _resolve_target(session: Any, parts: List[str], *, required: bool = True) -> Tuple[str, Dict[str, Any], List[str]]:
    """``(origin session, job defaults, remaining parts)`` from ``--session``/``--in``.

    ``--in PATH`` sets the workspace and needs no chat session: the job is filed
    under the hidden session for that workspace (or ``--session NAME`` if given);
    model and policy still come from the current session when there is one."""
    workspace_path, parts = _pop_in_option(parts)
    session_name, parts = _pop_session_option(parts)
    if not workspace_path:
        name, defaults = _resolve_session(session, session_name, required=required)
        return name, defaults, parts
    clone = (commands.ensure_repository_clone(workspace_path)
             if commands.is_repository_url(workspace_path) else None)
    hidden, workspace = workspace_session(clone.path if clone else workspace_path)
    if session_name:
        name, defaults = _resolve_session(session, session_name)
    else:
        name, defaults = hidden, _session_defaults(session)
    defaults = {**defaults, "workspace_root": workspace}
    if clone is not None:
        # Start from the freshly fetched remote tip, not the clone's (possibly stale) checkout.
        defaults.update(base_branch=clone.branch, base_sha=clone.base_sha, metadata={
            **dict(defaults.get("metadata") or {}), "repository_url": clone.url,
            "repository_clone": {"path": clone.path, "branch": clone.branch, "fetched_sha": clone.base_sha},
            **({"repository_warnings": list(clone.warnings)} if clone.warnings else {})})
    return name, defaults, parts


def _list_sessions(session: Any, allow_prompt: bool) -> CommandResult:
    """`/job sessions` — sessions a job can be created in."""
    current = _current_session_name(session)
    sessions = list_saved_sessions(current)
    lines = ["Job sessions \u00b7 /job create <title> --session <name>", ""]
    for item in sessions:
        marker = "*" if item["current"] else " "
        model = "\u00b7".join(x for x in (item["provider"], item["model"]) if x) or "no model"
        workspace = item["workspace"] or "(no workspace)"
        lines.append(f"{marker} {_short(item['name'], 22):<22}  {_short(model, 24):<24}  {_short(workspace, 26)}")
    if not sessions:
        lines.append("No saved sessions.")
    body = "\n".join(lines)
    _emit(session, body, allow_prompt)
    return CommandResult(ok=True, message=f"{len(sessions)} session(s).",
                         data={"current": current or None, "sessions": sessions})


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
        **{key: defaults[key] for key in ("base_branch", "base_sha") if defaults.get(key)},
        **({"metadata": dict(defaults["metadata"])} if defaults.get("metadata") else {}),
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
            # Retired 2026-10-15: jobs always run unattended (mu.jobs.unattended). Accepted, ignored.
            index += 1
            continue
        if name == "remember":
            # "Remember for this workspace": the Toolchains row repository -> template:<name>.
            execution["remember_template"] = True
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
            # Normally consumed by _resolve_target (hidden session per clone); a direct
            # caller gets the URL/path passed through for the service to resolve.
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
        elif name in {"env", "environment"}:
            try:
                binding = commands.parse_environment_spec(value)
            except ValueError as exc:
                raise ValueError(f"--env {value}: {exc}") from None
            execution.setdefault("environments", []).append(binding)
        elif name in {"template", "run-in"}:
            execution["template"] = template_name(value)
        elif name in {"agent", "agents"}:
            try:
                execution["stage_agents"] = {**dict(execution.get("stage_agents") or {}),
                                             **vm.parse_agent_spec(value)}
            except ValueError as exc:
                raise ValueError(f"--agent {value}: {exc}") from None
        else:
            raise ValueError(f"unknown option --{name}")
        index += 1
    if execution.get("remember_template") and not execution.get("template"):
        raise ValueError("--remember needs --template NAME")
    values["title"] = " ".join(title_parts).strip()
    return values


def template_name(value: str) -> str:
    """``--template NAME`` (``template:NAME`` accepted); ``default``/``none`` = Default runtime."""
    text = str(value or "").strip()
    if text.lower().startswith("template:"):
        text = text[len("template:"):].strip()
    if text.lower() in {"", "default", "none", "runtime"}:
        return ""
    return text


def _create_job(session: Any, raw: str, allow_prompt: bool) -> CommandResult:
    try:
        session_name, defaults, parts = _resolve_target(session, shlex.split(raw or ""))
        payload = _with_origin(_parse_create(shlex.join(parts), defaults), session_name)
        service = get_default_job_service()
        result = commands.dispatch(service, {"type": "create", "args": payload})
        job = service.get(result.job_id)
    except (OSError, TypeError, ValueError) as exc:
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


def _dashboard_create(session: Any, title: str, agents: str = "", run_in: str = "") -> str:
    """New job from the dashboard: the current session's defaults plus the per-stage agents and
    the "Run in" choice (``template:<name>`` / ``environment:<name>``)."""
    session_name, defaults = _resolve_session(session, "")
    parts = [title] + (["--agent", agents] if agents else []) + _run_in_flags(run_in)
    payload = _with_origin(_parse_create(shlex.join(parts), defaults), session_name)
    service = get_default_job_service()
    result = commands.dispatch(service, {"type": "create", "args": payload})
    ensure_controller_daemon()
    return result.job_id


def _run_in_flags(run_in: str) -> List[str]:
    value = str(run_in or "").strip()
    if not value or value == "default":
        return []
    if value.startswith("template:"):
        return ["--template", value[len("template:"):]]
    return ["--env", value]


def _dashboard_sources(session: Any) -> Dict[str, Any]:
    """"Run in" choices for the dashboard's new-job prompt (the current session's workspace)."""
    try:
        _name, defaults = _resolve_session(session, "", required=False)
    except (OSError, ValueError):
        defaults = {}
    return vm.environments_vm(repository=str(defaults.get("workspace_root") or ""), include_images=False)


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
            version = detail.strip()
            if not version:
                # Default to the latest plan version (what the dashboard approves).
                plan = vm.job_vm(service, job.id, parts=["plan"]).get("plan") or {}
                version = str(plan.get("version") or "")
                if not version:
                    raise ValueError(f"job {job.id[:10]} has no plan to approve yet")
            try:
                args = {"version": int(version)}
            except ValueError:
                raise ValueError(f"plan version must be a number, got {version!r}") from None
        elif kind == "savePlanDraft":
            from pathlib import Path
            args = {"plan": Path(detail).read_text(), "version": vm.job_vm(service, job.id, parts=["plan"])["plan"]["version"]}
        elif kind in ("approveTool", "denyTool"):
            # The core command answers one specific request (event_id) so a stale
            # click cannot approve a newer tool call; the terminal targets the one
            # pending now. ``deny-tool <id> <reason>`` passes the reason to the agent.
            args = {"message": detail} if kind == "denyTool" and detail else {}
        elif kind in ("approveEnvironment", "rejectEnvironment"):
            args = {"reason": detail} if kind == "rejectEnvironment" and detail else {}
        elif kind == "raiseLimit":
            args = _raise_limit_args(detail)
        if kind in ("approveTool", "denyTool") and "event_id" not in args:
            pending = commands.facts_for_job(service, job).pending_approval
            if not pending:
                raise ValueError(f"job {job.id[:10]} has no pending tool request")
            args["event_id"] = pending["event_id"]
        if kind in ("approveEnvironment", "rejectEnvironment") and "fingerprint" not in args:
            pending = commands.facts_for_job(service, job).environment_approval
            if not pending:
                raise ValueError(f"job {job.id[:10]} has no pending build environment request")
            args["fingerprint"] = pending.get("fingerprint", "")
        result = commands.dispatch(service, {"type": kind, "job_id": job.id, "args": args})
        ensure_controller_daemon()
        if kind == "raiseLimit":
            raised = result.data.get("limit") or {}
            text = (f"Raised the {raised.get('scope')} {raised.get('kind')} limit of {job.id[:10]} from "
                    f"{raised.get('old_text')} to {raised.get('new_text')}"
                    + ("; the paused stage continues." if result.data.get("resumed") else "."))
            _emit(session, text, allow_prompt)
            return CommandResult(ok=True, message=text, data=result.to_dict())
        _emit(session, f"{kind}: {job.id[:10]}", allow_prompt)
        return CommandResult(ok=True, message=kind, data=result.to_dict())
    except (KeyError, ValueError, RuntimeError, OSError) as exc:
        _emit(session, str(exc), allow_prompt, error=True)
        return CommandResult(ok=False, message=str(exc))


_LIMIT_KIND_ALIASES = {"cost": "cost", "budget": "cost", "usd": "cost", "token": "tokens", "tokens": "tokens",
                       "time": "time", "runtime": "time", "iteration": "iterations", "iterations": "iterations",
                       "iters": "iterations"}
RAISE_LIMIT_USAGE = ("usage: /job raise-limit <id> <cost|tokens|time|iterations> <amount> "
                     "[--stage plan|implement|review] [--scope job|stage]  (amount: 25, 25usd, 2h, 30m, 5M tokens)")


def _raise_limit_args(detail: str) -> Dict[str, Any]:
    """``cost 25usd`` / ``time 2h --scope stage --stage review`` / ``tokens 5M tokens`` → raiseLimit args."""
    words = shlex.split(detail or "")
    args: Dict[str, Any] = {}
    rest: List[str] = []
    i = 0
    while i < len(words):
        word = words[i]
        if word in ("--stage", "--scope") and i + 1 < len(words):
            args[word[2:]] = words[i + 1]
            i += 2
            continue
        if word.startswith(("--stage=", "--scope=")):
            key, _, value = word[2:].partition("=")
            args[key] = value
        else:
            rest.append(word)
        i += 1
    if len(rest) < 2:
        raise ValueError(RAISE_LIMIT_USAGE)
    kind = _LIMIT_KIND_ALIASES.get(rest[0].lower())
    if not kind:
        raise ValueError(f"unknown limit {rest[0]!r}. " + RAISE_LIMIT_USAGE)
    args.update(kind=kind, amount=" ".join(rest[1:]))
    return args


def _open_dashboard(session: Any, rest: str, allow_prompt: bool, *, explicit: bool) -> CommandResult:
    """`/jobs`, `/job` or `/job ui [id]`: the full-screen keyboard dashboard.

    Only from an interactive terminal; elsewhere (web GUI, scripts, tests)
    `/job` keeps returning the text board and `/job ui` explains why."""
    from mu.ui.jobs import can_open_tui, open_jobs_tui

    if not can_open_tui(session, allow_prompt=allow_prompt):
        if not explicit:
            return _board_jobs(session, allow_prompt)
        message = "The job dashboard needs an interactive terminal; showing the board instead."
        _emit(session, message, allow_prompt, error=True)
        result = _board_jobs(session, allow_prompt)
        return CommandResult(ok=False, message=message, data=result.data)
    try:
        open_jobs_tui(start_job=rest.strip() or None, service=get_default_job_service(),
                      creator=lambda title, agents, run_in="": _dashboard_create(session, title, agents, run_in),
                      sources=lambda: _dashboard_sources(session))
    except Exception as exc:  # noqa: BLE001 - never lose the REPL to a UI failure
        message = f"Job dashboard failed: {exc}"
        _emit(session, message, allow_prompt, error=True)
        return CommandResult(ok=False, message=message)
    return CommandResult(ok=True, message="Closed the job dashboard.")


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


_SETTINGS_USAGE = "Usage: /job settings [set <path> <value> | reset <path>]"


def _job_settings(session: Any, rest: str, allow_prompt: bool) -> CommandResult:
    """`/job settings` lists every run rule; `set <path> <value>` / `reset <path>` change one."""
    action, _, tail = rest.strip().partition(" ")
    action = action.lower()
    tail = tail.strip()

    def fail(message: str) -> CommandResult:
        _emit(session, message, allow_prompt, error=True)
        return CommandResult(ok=False, message=message)

    if action in {"", "show", "list"}:
        text = _settings_text()
        _emit(session, text, allow_prompt)
        return CommandResult(ok=True, message=text, data=vm.job_settings_vm()["rules"])
    if action not in {"set", "reset", "unset"}:
        return fail(_SETTINGS_USAGE)
    path, _, value = tail.partition(" ")
    path, value = path.strip(), value.strip()
    if not path:
        return fail(_SETTINGS_USAGE)
    fields = {f["path"]: f for section in vm.job_settings_vm()["sections"] for f in section["fields"]}
    field = fields.get(path)
    if field is None:
        close = [p for p in fields if path.lower() in p.lower()]
        hint = f" Did you mean: {', '.join(close[:5])}?" if close else " Run /job settings to list them."
        return fail(f"Unknown setting: {path}.{hint}")
    if action == "set" and not value and field["kind"] in ("int", "float", "bool") and not field.get("optional"):
        return fail(f"Usage: /job settings set {path} <value>")
    from mu.ui.jobs.data import parse_rows_text, present_rows_field
    try:
        if action == "set" and field["kind"] == "rows":
            saved = present_rows_field({**field, **commands.set_job_rule_value(path, parse_rows_text(field, _unquote(value)))})
        else:
            saved = commands.set_job_rule(path, value if action == "set" else None)
            if field["kind"] == "rows":
                saved = present_rows_field({**field, **saved})
    except ValueError as exc:
        return fail(f"Invalid value for {path}: {exc}")
    if saved["set"]:
        message = f"{saved['label']} set to {saved['display']}."
    else:
        message = f"{saved['label']} reset to the default ({saved['display']})."
    _emit(session, message, allow_prompt)
    return CommandResult(ok=True, message=message, data=saved)


def _unquote(text: str) -> str:
    """Strip one pair of shell-style quotes around a JSON value (``'[{...}]'``)."""
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"" and text[0] not in text[1:-1]:
        return text[1:-1]
    return text


def _settings_text() -> str:
    """``rules_text`` with ``rows`` rules (toolchains) shown one row per line instead of raw JSON."""
    from mu.ui.jobs.data import present_rows_field, rows_display
    text = vm.job_settings_text()
    for section in vm.job_settings_vm()["sections"]:
        for raw in section["fields"]:
            if raw.get("kind") != "rows":
                continue
            field = present_rows_field(raw)
            rows = [r for r in raw.get("value") or [] if isinstance(r, dict)]
            head = f"  {raw['label']}: {raw['display']}  "
            count = f"{len(rows)} row{'s' if len(rows) != 1 else ''}" if rows else "none"
            lines = text.split("\n")
            for i, line in enumerate(lines):
                if line.startswith(head):
                    extra = [f"      - {rows_display(raw, [r])}" for r in rows]
                    lines[i:i + 1] = [f"  {raw['label']}: {count}  " + line[len(head):]] + extra
                    break
            text = "\n".join(lines).replace(f"{raw['path']} · default {raw['default_display']}",
                                            f"{raw['path']} · default {field['default_display']}")
    return text + ("\nRows are JSON, e.g. /job settings set sandbox.toolchains "
                   "'[{\"repository\": \"/src/app\", \"base_image\": \"rust:1.88\"}]'"
                   "\nToolchain images: /job toolchains   Build: /job toolchains build <base_image>")


_TOOLCHAINS_USAGE = "Usage: /job toolchains [build <base_image>]"


def _toolchains_text(items: List[Dict[str, Any]], *, log_lines: int = 12) -> str:
    from mu.ui.jobs.data import toolchain_line
    if not items:
        return ("No toolchains configured. Add one with /job settings set sandbox.toolchains "
                "'[{\"repository\": \"/src/app\", \"base_image\": \"rust:1.88\"}]'")
    lines = ["Toolchain images (Settings → Environments → Toolchains)", ""]
    for item in items:
        lines.append("  " + toolchain_line(item))
        if item.get("tag"):
            lines.append(f"      image {item['tag']}")
        if item.get("error"):
            lines.append(f"      error: {item['error']}")
        tail = [l for l in str(item.get("log_tail") or "").splitlines() if l.strip()]
        if tail and item.get("status") in ("building", "failed"):
            lines.append(f"      build log (last {min(log_lines, len(tail))} lines):")
            lines += [f"        {l}" for l in tail[-log_lines:]]
    if any(i.get("status") == "building" for i in items):
        lines += ["", "Building in the background; run /job toolchains again for progress."]
    elif any(i.get("status") in ("missing", "failed") for i in items):
        lines += ["", "Build now: /job toolchains build <base_image> (otherwise built on first use)."]
    return "\n".join(lines)


def _job_toolchains(session: Any, rest: str, allow_prompt: bool) -> CommandResult:
    """`/job toolchains` lists derived toolchain images; `build <base_image>` starts a background build."""
    action, _, tail = rest.strip().partition(" ")
    action, tail = action.lower(), _unquote(tail)

    def fail(message: str) -> CommandResult:
        _emit(session, message, allow_prompt, error=True)
        return CommandResult(ok=False, message=message)

    if action in {"", "list", "show", "status"}:
        try:
            items = list(vm.toolchains_vm().get("toolchains") or [])
        except (OSError, ValueError, RuntimeError) as exc:
            return fail(f"Could not read toolchain status: {exc}")
        text = _toolchains_text(items)
        _emit(session, text, allow_prompt)
        return CommandResult(ok=True, message=text, data={"toolchains": items})
    if action not in {"build", "rebuild"}:
        return fail(_TOOLCHAINS_USAGE)
    if not tail:
        return fail("Usage: /job toolchains build <base_image>")
    try:
        state = commands.build_toolchain(tail)
    except (ValueError, RuntimeError, OSError) as exc:
        return fail(f"Cannot build {tail}: {exc}")
    status = str(state.get("status") or "")
    if status == "built":
        message = f"Toolchain image for {tail} is built ({state.get('tag', '')})."
    elif status == "failed":
        message = f"Toolchain build for {tail} failed: {state.get('error') or 'see /job toolchains'}"
    else:
        message = (f"Building toolchain image {state.get('tag', '')} from {tail} in the background. "
                   "Run /job toolchains for progress and the build log.")
    _emit(session, message, allow_prompt, error=status == "failed")
    return CommandResult(ok=status != "failed", message=message, data=state)


def _fmt_bytes(value: Any) -> str:
    size = float(value or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


STEP_ICONS = {"ok": "✓", "todo": "○", "warn": "!", "skipped": "–", "optional": "·", "": "…"}
DOCTOR_ICONS = {"ok": "✓", "warn": "!", "fail": "✗"}


def doctor_text(checks: List[Dict[str, Any]]) -> str:
    """`/job doctor`: one line per check."""
    failed = sum(1 for c in checks if c["status"] == "fail")
    warned = sum(1 for c in checks if c["status"] == "warn")
    lines = [f"Jobs doctor  {len(checks) - failed - warned}/{len(checks)} ok"
             + (f" · {failed} failing" if failed else "") + (f" · {warned} warnings" if warned else ""), ""]
    lines += [f"  {DOCTOR_ICONS.get(c['status'], '?')} {c['name']:<20} {c['detail']}" for c in checks]
    if failed:
        lines += ["", "Fix failing items with /job setup (each step offers the actions that fix it)."]
    return "\n".join(lines)


def setup_text(vm_data: Dict[str, Any]) -> str:
    """`/job setup`: the checklist with each step's actions as `/job setup <step> <action>` commands."""
    lines = [f"Jobs setup  {vm_data['done']}/{vm_data['total']} done · every step checks your machine live; re-run any time", ""]
    for step in vm_data["steps"]:
        icon = STEP_ICONS.get(step["state"], "?")
        lines.append(f"{icon} {step['title']}{' (optional)' if step['optional'] else ''} — {step['detail']}")
        if step["state"] not in ("ok",):
            for action in step["actions"]:
                hint = f"  ({action['hint']})" if action.get("hint") else ""
                if action["kind"] == "terminal":
                    lines.append(f"      run in a terminal: {action['command']}{hint}  then /job setup {step['id']} {action['id']}")
                elif action["kind"] == "nav":
                    lines.append(f"      {action['label']}: /job settings")
                else:
                    lines.append(f"      /job setup {step['id']} {action['id']}  — {action['label']}{hint}")
    job = vm_data.get("job")
    if job:
        state = "running" if job["running"] else ("passed" if job["ok"] else "failed")
        lines += ["", f"{job['title']}: {state}"] + [f"  {line}" for line in job["lines"][-8:]]
    lines.append("")
    lines.append("All set: /job setup finish" if vm_data["can_finish"] else
                 "To do: " + ", ".join(vm_data["blocking"]) if vm_data["blocking"] else "checking…")
    lines.append("Actions on steps that are already ok: /job setup <step> <action> (e.g. /job setup claude test).")
    return "\n".join(lines)


def _job_doctor(session: Any, rest: str, allow_prompt: bool) -> CommandResult:
    deep = "--deep" in rest.split()
    if deep:
        _emit(session, "Running the doctor with the sandbox self-test (starts real containers)…", allow_prompt)
    checks = vm.doctor_vm(deep=deep)
    text = doctor_text(checks)
    failed = any(c["status"] == "fail" for c in checks)
    _emit(session, text, allow_prompt, error=failed)
    return CommandResult(ok=not failed, message=text, data={"checks": checks})


def _job_setup(session: Any, rest: str, allow_prompt: bool) -> CommandResult:
    """`/job setup` (checklist), `/job setup <step> <action> [inputs…]`, `/job setup finish`."""
    parts = shlex.split(rest or "")
    if not parts or parts[0] in ("recheck", "check"):
        data = vm.setup_vm(recheck=True)
        text = setup_text(data)
        _emit(session, text, allow_prompt)
        return CommandResult(ok=True, message=text, data=data)
    if parts[0] == "finish":
        try:
            data = commands.setup_finish()
        except ValueError as exc:
            _emit(session, str(exc), allow_prompt, error=True)
            return CommandResult(ok=False, message=str(exc))
        _emit(session, "Setup complete.", allow_prompt)
        return CommandResult(ok=True, message="Setup complete.", data=data)
    if len(parts) < 2:
        message = "Usage: /job setup <step> <action> [inputs…] — see /job setup for the list"
        _emit(session, message, allow_prompt, error=True)
        return CommandResult(ok=False, message=message)
    step, action_id, inputs = parts[0], parts[1], parts[2:]
    data = vm.setup_vm()
    spec = next((a for s in data["steps"] if s["id"] == step for a in s["actions"] if a["id"] == action_id), None)
    if spec is None:
        message = f"No action {action_id!r} for step {step!r} right now (see /job setup)."
        _emit(session, message, allow_prompt, error=True)
        return CommandResult(ok=False, message=message)
    if spec["kind"] == "prompts" and len(inputs) < len(spec.get("prompts") or []):
        if not allow_prompt:
            message = f"{spec['label']} needs {len(spec['prompts'])} values: " + ", ".join(p["label"] for p in spec["prompts"])
            _emit(session, message, allow_prompt, error=True)
            return CommandResult(ok=False, message=message)
        import getpass
        for prompt in spec["prompts"][len(inputs):]:
            label = f"{prompt['label']}" + (f" [{prompt['initial']}]" if prompt.get("initial") and not prompt.get("secret") else "") + ": "
            value = getpass.getpass(label) if prompt.get("secret") else input(label)
            inputs.append(value or ("" if prompt.get("secret") else str(prompt.get("initial") or "")))
    if spec["kind"] == "terminal" and allow_prompt and spec.get("command"):
        import subprocess
        _emit(session, f"$ {spec['command']}", allow_prompt)
        subprocess.run(spec["command"], shell=True)  # the user's own login flow, in their terminal
    if spec["kind"] == "nav":
        return _job_settings(session, "", allow_prompt)
    try:
        result = commands.setup_action(step, action_id, inputs, wait=True,
                                       on_line=(lambda line: _emit(session, line, allow_prompt)) if spec["kind"] == "job" else None)
    except (ValueError, RuntimeError, OSError) as exc:
        _emit(session, f"{spec['label']}: {exc}", allow_prompt, error=True)
        return CommandResult(ok=False, message=str(exc))
    after = vm.setup_vm()
    job = after.get("job") if spec["kind"] == "job" else None
    ok = bool(job["ok"]) if job else bool(result.get("ok"))
    step_view = next(s for s in after["steps"] if s["id"] == step)
    message = (f"{spec['label']}: {'done' if ok else 'failed'}" if job else str(result.get("message") or "done")) + \
        f"\n{STEP_ICONS.get(step_view['state'], '?')} {step_view['title']} — {step_view['detail']}"
    _emit(session, message, allow_prompt, error=not ok)
    return CommandResult(ok=ok, message=message, data=after)


def agents_text(data: Dict[str, Any]) -> str:
    """`/job agents`: which agent runs each stage by default and whether Claude Code / Codex are ready."""
    defaults = dict(data.get("defaults") or {})
    lines = ["Agent harnesses  (/job create <title> --agent plan=claude,implement=codex)", "",
             "Defaults: " + " · ".join(f"{s} {defaults.get(s, 'mucli')}" for s in ("plan", "implement", "review")), ""]
    for agent in ("claude", "codex"):
        item = dict(data.get(agent) or {})
        state = "ready" if item.get("ready") else "not ready"
        lines.append(f"{item.get('label', agent)}: {state}")
        if item.get("binary"):
            lines.append(f"  binary      {item['binary']}  ({item.get('version') or 'unknown version'})")
        if item.get("credential"):
            lines.append(f"  credential  {item['credential']} from {item.get('credential_source', '')}")
        models = dict(item.get("models") or {})
        lines.append("  models      " + " · ".join(f"{s} {models.get(s) or 'default'}" for s in ("plan", "implement", "review")))
        lines += [f"  ✗ {p}" for p in item.get("problems") or []]
        lines += [f"  ! {w}" for w in item.get("warnings") or []]
        if not item.get("ready"):
            lines += [f"  → {step}" for step in (data.get("setup") or {}).get(agent) or []]
        lines.append("")
    secrets = dict(data.get("secrets") or {})
    lines.append("Secrets: " + " · ".join(f"{k} {v or 'unset'}" for k, v in secrets.items()))
    lines.append("Set one with /job agents secret CLAUDE_CODE_OAUTH_TOKEN <token> (stored 0600 in secrets.env); "
                 "defaults and models live in /job settings (harness.*).")
    return "\n".join(lines)


def _job_publish(session: Any, rest: str, allow_prompt: bool) -> CommandResult:
    """`/job publish` (token status) and `/job publish token <provider> [token]` (no token clears)."""
    parts = rest.split()
    if parts and parts[0].lower() == "token":
        if len(parts) not in (2, 3):
            message = "Usage: /job publish token <github|gitlab|bitbucket|gitea> [token]  (no token clears it)"
            _emit(session, message, allow_prompt, error=True)
            return CommandResult(ok=False, message=message)
        try:
            commands.save_publish_token(parts[1], parts[2] if len(parts) == 3 else "")
        except ValueError as exc:
            _emit(session, str(exc), allow_prompt, error=True)
            return CommandResult(ok=False, message=str(exc))
        message = f"{parts[1].lower()} token {'saved' if len(parts) == 3 else 'cleared'} (stored 0600, host-side only)"
        _emit(session, message, allow_prompt)
        return CommandResult(ok=True, message=message)
    status = vm.publish_tokens_status()
    lines = ["PR/MR provider tokens (used when a job pushes and opens its pull/merge request)", ""]
    for provider, row in status.items():
        where = {"environment": "set in the environment", "stored": "stored in mucli"}.get(row["source"], "not set")
        lines.append(f"  {provider:<9} {row['variable']:<16} {where}")
    lines += ["", "Set one with /job publish token <provider> <token>. Self-hosted hosts: Settings → Push → "
              "PR provider hosts (host=gitea); Gitea/Forgejo is also detected automatically."]
    text = "\n".join(lines)
    _emit(session, text, allow_prompt)
    return CommandResult(ok=True, message=text, data={"tokens": status})


def _job_agents(session: Any, rest: str, allow_prompt: bool) -> CommandResult:
    parts = rest.split()
    if parts and parts[0].lower() == "secret":
        if len(parts) not in (2, 3):
            message = "Usage: /job agents secret <KEY> [value]  (no value clears it)"
            _emit(session, message, allow_prompt, error=True)
            return CommandResult(ok=False, message=message)
        try:
            commands.save_agent_secret(parts[1], parts[2] if len(parts) == 3 else "")
        except ValueError as exc:
            _emit(session, str(exc), allow_prompt, error=True)
            return CommandResult(ok=False, message=str(exc))
        message = f"{parts[1]} {'saved' if len(parts) == 3 else 'cleared'}"
        _emit(session, message, allow_prompt)
        return CommandResult(ok=True, message=message)
    data = vm.agents_status()
    text = agents_text(data)
    _emit(session, text, allow_prompt)
    return CommandResult(ok=True, message=text, data=data)


def _environments_text(data: Dict[str, Any], *, image_limit: int = 15) -> str:
    """`/job environments`: sources a job can snapshot, DinD/GPU status and kept jobs."""
    lines = ["Build environment sources  (/job create <title> --env name[:paths][:docker])", ""]
    envs = list(data.get("environments") or [])
    lines.append(f"Container-mode environments ({len(envs)})")
    for item in envs:
        extra = f" · gpus {item['gpus']}" if item.get("gpus") else ""
        lines.append(f"  {item.get('name', ''):<24} {item.get('status') or '?':<8} {item.get('image', '')}{extra}")
    if not envs:
        lines.append("  none (create one in container mode: /container)")
    templates = list(data.get("templates") or [])
    lines += ["", f"Templates ({len(templates)})  · use template:<name>"]
    lines += [f"  {t.get('name', ''):<24} {t.get('image', '')}" for t in templates] or ["  none"]
    images = list(data.get("images") or [])
    lines += ["", f"Local images ({len(images)})  · use image:<ref>"]
    lines += [f"  {i.get('name', ''):<48} {i.get('size', '')}" for i in images[:image_limit]] or ["  none"]
    if len(images) > image_limit:
        lines.append(f"  … {len(images) - image_limit} more (docker image ls)")
    dind = dict(data.get("docker_in_docker") or {})
    lines += ["", "Docker-in-Docker: " + ("available (Sysbox)" if dind.get("available") else "not installed (needs Sysbox)")]
    if not dind.get("available"):
        lines += [f"  {i}. {step}" for i, step in enumerate(dind.get("install") or [], 1)]
    gpu = dict(data.get("gpu") or {})
    lines.append("GPUs: " + ("available" if gpu.get("supported") else "unavailable") + (f" · {gpu['reason']}" if gpu.get("reason") else ""))
    kept = list(data.get("kept_jobs") or [])
    if kept:
        lines += ["", f"Kept jobs ({len(kept)}) · /job env unkeep <id> releases them"]
        lines += [f"  {k['id'][:10]}  {_fmt_bytes(k.get('bytes')):>9}  {_short(k.get('title'), 40)}  {k.get('bindings') or ''}"
                  for k in kept]
    return "\n".join(lines)


def _job_environment_text(job, info: Dict[str, Any]) -> str:
    """`/job env <id>`: the job's bindings and their snapshot state."""
    source = info.get("source") or "unresolved"
    head = f"Build environments · {job.id[:10]} {_short(job.title, 50)}"
    lines = [head, f"  set from: {source}" + ("  ⬢ kept" if info.get("kept") else "")
             + ("  (refresh pending)" if info.get("refresh_pending") else "")]
    bindings = list(info.get("bindings") or [])
    if not bindings:
        lines.append("  shared runtime image (no build environments resolved yet)")
    for b in bindings:
        flags = [b.get("status") or "?"]
        if b.get("docker"):
            flags.append("docker (Sysbox)")
        if b.get("gpus"):
            flags.append(f"gpus {b['gpus']}")
        if b.get("primary"):
            flags.append("primary")
        lines += ["", f"  {b.get('name')}  {b.get('source')}:{b.get('ref')}  [{', '.join(flags)}]",
                  f"    paths: {', '.join(b.get('paths') or []) or 'whole repository'}"]
        if b.get("image_bytes"):
            lines.append(f"    image: {b.get('job_image') or ''} ({_fmt_bytes(b['image_bytes'])}, home {_fmt_bytes(b.get('home_bytes'))})")
        if b.get("probed_env"):
            lines.append(f"    probed: {', '.join(b['probed_env'])}")
        if b.get("skipped_paths"):
            shown = b["skipped_paths"][:6]
            more = len(b["skipped_paths"]) - len(shown)
            lines.append(f"    skipped from /root: {'; '.join(shown)}" + (f" (+{more} more)" if more > 0 else ""))
        if b.get("secret_paths"):
            lines.append(f"    secret scan blocked: {'; '.join(b['secret_paths'][:6])}")
        if b.get("error"):
            lines.append(f"    error: {_short(b['error'], 300)}")
    lines += ["", "  /job env refresh|keep|unkeep|clear <id> · /job env set <id> <spec> [<spec>...]"]
    return "\n".join(lines)


_ENV_USAGE = ("Usage: /job env <id> | /job env refresh <id> [--yes] | /job env keep|unkeep <id> | "
              "/job env set <id> name[:paths][:docker] ... | /job env clear <id>")


def _job_environments(session: Any, rest: str, allow_prompt: bool) -> CommandResult:
    """`/job environments`: sources, Docker-in-Docker/GPU status and kept jobs."""
    try:
        data = vm.environments_vm(get_default_job_service())
    except (OSError, ValueError, RuntimeError) as exc:
        message = f"Could not list build environments: {exc}"
        _emit(session, message, allow_prompt, error=True)
        return CommandResult(ok=False, message=message)
    text = _environments_text(data)
    _emit(session, text, allow_prompt)
    return CommandResult(ok=True, message=text, data=data)


def _job_env(session: Any, rest: str, allow_prompt: bool) -> CommandResult:
    """`/job env ...`: show or change one job's build environments."""
    def fail(message: str) -> CommandResult:
        _emit(session, message, allow_prompt, error=True)
        return CommandResult(ok=False, message=message)

    try:
        tokens = shlex.split(rest or "")
    except ValueError as exc:
        return fail(str(exc))
    if not tokens:
        return fail(_ENV_USAGE)
    action = tokens[0].lower() if tokens[0].lower() in {"refresh", "keep", "unkeep", "set", "clear", "show"} else "show"
    args = tokens[1:] if action != "show" or tokens[0].lower() == "show" else tokens
    assume_yes = any(a in {"--yes", "-y"} for a in args)
    args = [a for a in args if a not in {"--yes", "-y"}]
    if not args:
        return fail(_ENV_USAGE)
    service = get_default_job_service()
    try:
        job = _resolve_job(service, args[0])
    except (KeyError, ValueError) as exc:
        return fail(str(exc) if not isinstance(exc, KeyError) else f"Job not found: {args[0]}")
    if action == "show":
        info = vm.job_environments_vm(service, job.id)
        text = _job_environment_text(job, info)
        _emit(session, text, allow_prompt)
        return CommandResult(ok=True, message=text, data=info)
    if action == "refresh" and not assume_yes:
        from mu.jobs.core.actions import CONFIRMS
        ui = getattr(session, "ui", None)
        confirm = getattr(ui, "confirm", None) if allow_prompt else None
        if not callable(confirm):
            return fail(f"Refusing to re-snapshot job {job.id[:10]} without confirmation. "
                        "Re-run as `/job env refresh <id> --yes`.")
        if not confirm(CONFIRMS["refreshEnvironments"], default=False):
            message = f"Kept the current build environments of {job.id[:10]}."
            _emit(session, message, allow_prompt)
            return CommandResult(ok=True, message=message)
    if action == "set":
        if len(args) < 2:
            return fail("Usage: /job env set <id> name[:paths][:docker] [...]")
        try:
            specs = [commands.parse_environment_spec(spec) for spec in args[1:]]
        except ValueError as exc:
            return fail(f"Invalid build environment: {exc}")
        cmd = {"type": "setEnvironments", "args": {"environments": specs}}
    elif action == "clear":
        cmd = {"type": "setEnvironments", "args": {"environments": []}}
    elif action == "refresh":
        cmd = {"type": "refreshEnvironments", "args": {}}
    else:
        cmd = {"type": "keepEnvironments", "args": {"keep": action == "keep"}}
    try:
        result = commands.dispatch(service, {**cmd, "job_id": job.id})
    except (KeyError, ValueError, RuntimeError) as exc:
        return fail(f"{cmd['type']} rejected: {exc}")
    if action == "set" or action == "clear":
        summary = (result.data.get("environments") or {}).get("summary") or "repository/session/settings default"
        message = f"Build environments of {job.id[:10]}: {summary}"
    elif action == "refresh":
        message = f"Re-snapshotting the build environments of {job.id[:10]} in the background; stages started after this use the new images."
    else:
        message = (f"Kept the build environments of {job.id[:10]} (exempt from idle retention)." if result.data.get("kept")
                   else f"Build environments of {job.id[:10]} follow idle retention again.")
    _emit(session, message, allow_prompt)
    return CommandResult(ok=True, message=message, data=result.to_dict())


_SHORT_ACTIONS = {"changes", "continue", "discard", "cancel", "resume", "retry", "approve", "replan"}
# Job command ids are camelCase on the wire; the terminal documents the
# lowercase-hyphen spelling (``approve-plan``) and accepts both.
_ACTION_IDS = ("approvePlan", "rejectPlan", "savePlanDraft", "message", "restartStage", "recheck", "reprompt",
               "markFindings", "push", "repush", "markMerged", "approveTool", "denyTool",
               "approveEnvironment", "rejectEnvironment", "raiseLimit")


def _kebab(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "-", name).lower()


_ACTION_ALIASES = {**{_kebab(a): a for a in _ACTION_IDS}, **{a.lower(): a for a in _ACTION_IDS}}

_SUBCOMMANDS = ("ui", "tui", "dashboard", "board", "summary", "digest", "list", "show", "receipt", "diff",
                "sessions", "create", "respond", "jira", "delete", "rm", "settings", "config", "toolchains",
                "toolchain", "environments", "envs", "env", "setup", "doctor", "agents", "agent", "harness",
                "publish", *_SHORT_ACTIONS, *_ACTION_ALIASES)


def _action_kind(head: str) -> str:
    """camelCase command id for ``approve-plan`` / ``approvePlan`` / ``approveplan`` (``""`` if none)."""
    return _ACTION_ALIASES.get(str(head or "").strip().lower(), "")


def _one_shot(session: Any, raw: str, allow_prompt: bool) -> CommandResult:
    """`/job <id>` shows a job; `/job <TICKET-KEY> [--in PATH] [--force]` imports the ticket;
    `/job <title words> [--in PATH]` creates a job with that title.

    Subcommands are matched before this runs. A single unknown word is never
    turned into a job (it is far more likely a mistyped id or subcommand)."""
    from mu.integrations.jira import ISSUE_KEY_RE

    try:
        tokens = shlex.split(raw or "")
    except ValueError:
        return _show_job(session, raw, allow_prompt)
    if not tokens:
        return _board_jobs(session, allow_prompt)
    has_target = any(t in {*_IN_FLAGS, "--session"} or t.startswith((*(f"{f}=" for f in _IN_FLAGS), "--session="))
                     for t in tokens)
    words = [t for t in tokens if not t.startswith("--")]
    first = tokens[0]
    if ISSUE_KEY_RE.match(first):
        keys = [first] + [t for t in tokens[1:] if ISSUE_KEY_RE.match(t)]
        options = [t for t in tokens[1:] if not ISSUE_KEY_RE.match(t)]
        return _jira_job(session, shlex.join(["pull", *keys, *options]), allow_prompt)
    if len(tokens) == 1:
        result = _show_job(session, first, allow_prompt)
        if result.ok:
            return result
        import difflib
        close = difflib.get_close_matches(first.lower(), _SUBCOMMANDS, n=1, cutoff=0.75)
        hint = (f"Did you mean /job {close[0]}?" if close else
                f"To create a job titled {first!r}: /job create {shlex.quote(first)}")
        message = f"No job or subcommand {first!r}. {hint}"
        _emit(session, message, allow_prompt, error=True)
        return CommandResult(ok=False, message=message)
    if not has_target:
        try:
            job = vm.resolve_job(get_default_job_service(), first)
        except (KeyError, ValueError):
            job = None
        if job is not None:
            return _show_job(session, first, allow_prompt)
    import difflib
    close = difflib.get_close_matches(first.lower(), _SUBCOMMANDS, n=1, cutoff=0.8) if words and words[0] == first else []
    if close:
        message = (f"{first!r} looks like /job {close[0]}; not creating a job. "
                   f"To create one with this title: /job create {raw}")
        _emit(session, message, allow_prompt, error=True)
        return CommandResult(ok=False, message=message)
    return _create_job(session, raw, allow_prompt)


@command(
    "/job",
    "/jobs",
    help=(
        "Engineering jobs: /jobs (or /job, /job ui [id]) opens the keyboard dashboard; "
        "/job <TICKET-KEY|title words> [--in PATH|URL] creates a job in one step (a Jira key imports that ticket; "
        "--in PATH targets a workspace and needs no chat session; --in URL clones https/ssh/file repositories, "
        "optional @branch, into ~/.mucli/repos); "
        "board, summary [N|all], list [status], show|receipt|diff <id>, "
        "sessions (every saved session; jobs span all sessions), "
        "create <title> [--in PATH|URL (alias --repo) --session NAME --provider NAME --model NAME --accept TEXT --check CMD "
        "--env name[:paths][:docker] --template NAME [--remember] (run in a container template; --remember makes it the "
        "workspace default) --agent claude|codex|mucli or --agent plan=claude,implement=codex[:model],review=mucli] "
        "(default session = the current one), jira pull KEY... | --jql JQL [--in PATH] [--force] [--template NAME [--remember]] "
        "(--force re-runs done tickets), "
        "agents [secret <KEY> <value>] (Claude Code / Codex setup and status), "
        "setup [<step> <action> | finish] (guided setup checklist; doctor [--deep] checks everything jobs need), "
        "approve <id> [version] (default: latest plan), replan <id> <feedback>, save-plan-draft <id> <file>, "
        "reprompt <id> F-1,F-2 [guidance], "
        "message|resume|restart-stage|retry|recheck|push|mark-merged|mark-findings <id> [args JSON], "
        "approve-tool <id>, deny-tool <id> [reason] (the pending tool request), "
        "approve-environment <id>, reject-environment <id> [reason] (the repository's requested build environment), "
        "raise-limit <id> <cost|tokens|time|iterations> <amount> [--stage S] [--scope job|stage] (approve more of a "
        "cost/token/time/iteration limit; a job paused on that limit continues — e.g. raise-limit <id> cost 25usd, "
        "time 2h, tokens 5M), "
        "delete <id> [--yes] (cancel \u2192 archive \u2192 purge; branch preserved), "
        "settings [set <path> <value> | reset <path>] (run rules: budgets, timeouts, checks, sandbox, push; "
        "rows such as sandbox.toolchains are JSON), "
        "environments (build environment sources) and toolchains [build <base_image>] (per-repository toolchain images), "
        "env <id> | env refresh|keep|unkeep|clear <id> | env set <id> <spec>... (a job's build environments)."
    ),
)
def job_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    raw = str(args or "").strip()
    if not raw:
        return _open_dashboard(session, "", allow_prompt, explicit=False)
    head, _, rest = raw.partition(" ")
    sub = head.lower()
    rest = rest.strip()
    if sub in {"ui", "tui", "dashboard"}:
        return _open_dashboard(session, rest, allow_prompt, explicit=True)
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
    if sub == "sessions":
        return _list_sessions(session, allow_prompt)
    if sub == "create":
        return _create_job(session, rest, allow_prompt)
    if sub == "respond":
        return _respond_job(session, rest, allow_prompt)
    if sub in _SHORT_ACTIONS:
        return _review_action(session, sub, rest, allow_prompt)
    action = _action_kind(head)
    if action:
        return _review_action(session, action, rest, allow_prompt)
    if sub == "jira":
        return _jira_job(session, rest, allow_prompt)
    if sub in {"delete", "rm"}:
        return _delete_job(session, rest, allow_prompt)
    if sub in {"settings", "config"}:
        return _job_settings(session, rest, allow_prompt)
    if sub in {"toolchains", "toolchain"}:
        return _job_toolchains(session, rest, allow_prompt)
    if sub in {"environments", "envs"}:
        return _job_environments(session, rest, allow_prompt)
    if sub == "env":
        return _job_env(session, rest, allow_prompt)
    if sub == "setup":
        return _job_setup(session, rest, allow_prompt)
    if sub == "doctor":
        return _job_doctor(session, rest, allow_prompt)
    if sub in {"agents", "agent", "harness"}:
        return _job_agents(session, rest, allow_prompt)
    if sub == "publish":
        return _job_publish(session, rest, allow_prompt)
    return _one_shot(session, raw, allow_prompt)
