"""TUI retrospective analysis for durable engineering jobs."""

from __future__ import annotations

from typing import Any

from mu.jobs import get_default_job_service
from mu.jobs.performance import build_job_performance

from . import CommandResult, command
from .job import _resolve_job


def _emit(session: Any, text: str, allow_prompt: bool, *, error: bool = False) -> None:
    ui = getattr(session, "ui", None)
    if not allow_prompt or ui is None:
        return
    method = "show_error" if error else "show_info"
    if hasattr(ui, method):
        getattr(ui, method)(text)


def _duration(value: float) -> str:
    seconds = max(0, int(round(float(value or 0))))
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


@command(
    "/jobtrace",
    "/job-analysis",
    help="Retrospectively analyse performance of a durable engineering job.",
)
def job_analysis_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    token = str(args or "").strip()
    if not token:
        message = "Usage: /jobtrace <job-id>"
        _emit(session, message, allow_prompt, error=True)
        return CommandResult(ok=False, message=message)

    service = get_default_job_service()
    try:
        job = _resolve_job(service, token)
        analysis = build_job_performance(service, job.id, timeline_limit=1000)
    except (KeyError, ValueError) as exc:
        message = f"Job not found: {token}" if isinstance(exc, KeyError) else str(exc)
        _emit(session, message, allow_prompt, error=True)
        return CommandResult(ok=False, message=message)

    s = analysis["summary"]
    tools = analysis.get("tools") or []
    verifications = analysis.get("verifications") or []
    phases = analysis.get("phase_breakdown") or []
    tokens = s.get("tokens") or {}

    lines = [
        f"Job Trace · {job.title}",
        f"{job.id} · {job.status.value}{' · archived' if analysis['job'].get('archived') else ''}",
        "",
        f"Wall time:        {_duration(s['elapsed_seconds'])}",
        f"Active time:      {_duration(s['active_seconds'])}",
        f"Waiting time:     {_duration(s['waiting_seconds'])}",
        f"Verification:     {_duration(s['verification_seconds'])}",
        f"Attempts/retries: {s['attempts']} / {s['retries']}",
        f"Cost:             ${float(s['cost_usd'] or 0):.2f}",
        f"Tool calls:       {s['tool_calls']} ({s['unique_tools']} unique)",
        f"Human gates:      {s['human_gates']}",
        f"Failures:         {s['failures']}",
        f"Verification:     {s['verification_passes']} passed / {s['verification_failures']} failed",
        f"Changed files:    {s['changed_files']} (+{s['additions']} / -{s['deletions']})",
    ]
    if s.get("first_pass_verification") is not None:
        lines.append(f"First verify:      {'PASS' if s['first_pass_verification'] else 'FAIL'}")
    if tokens:
        lines.append("Tokens:            " + " · ".join(f"{k}={int(v):,}" for k, v in list(tokens.items())[:8]))

    if phases:
        lines.extend(["", "Time by lifecycle state:"])
        for phase in phases[:8]:
            lines.append(
                f"  {phase['status']:<18} {_duration(phase['seconds']):>10}  {float(phase['percent']):5.1f}%"
            )

    if tools:
        lines.extend(["", "Top tools:"])
        for item in tools[:10]:
            lines.append(f"  {item['name']:<28} {item['count']:>5}")

    if verifications:
        lines.extend(["", "Verification history:"])
        for index, run in enumerate(verifications[-8:], 1):
            mark = "PASS" if run["passed"] else "FAIL"
            lines.append(
                f"  {index:>2}. {mark:<4} {run['checks_passed']}/{run['checks']} checks · {_duration(run['duration_seconds'])}"
            )

    lines.extend([
        "",
        f"GUI analyzer: /static/job_trace.html?job={job.id}",
    ])
    body = "\n".join(lines)
    _emit(session, body, allow_prompt)
    return CommandResult(ok=True, message="Job analysis generated.", data={"analysis": analysis})
