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


def _phase_class(status: str) -> str:
    if status in {"preparing", "running", "recovering"}:
        return "active"
    if status == "verifying":
        return "verify"
    if status in {"queued", "needs_human"}:
        return "waiting"
    if status in {"failed", "environment_error", "timed_out", "budget_exceeded", "conflicted"}:
        return "stopped"
    if status == "ready_for_review":
        return "review"
    return "terminal" if status in {"cancelled", "merged"} else "other"


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
    intervals = analysis.get("phase_intervals") or []
    tokens = s.get("tokens") or {}
    runtime_trace = analysis.get("runtime_trace") or {}
    billing_modes = list(s.get("billing_modes") or [])
    cost_label = "estimated" if "estimated_token" in billing_modes else "attributed"

    lines = [
        f"Job Trace · {job.title}",
        f"{job.id} · {job.status.value}{' · archived' if analysis['job'].get('archived') else ''}",
        "",
        f"Wall time:        {_duration(s['elapsed_seconds'])}",
        f"Active execution: {_duration(s['active_seconds'])}",
        f"Passive time:     {_duration(s.get('passive_seconds', 0))}",
        f"Stopped/errors:   {_duration(s.get('stopped_seconds', 0))}",
        f"Verification:     {_duration(s['verification_seconds'])}",
        f"Attempts/retries: {s['attempts']} / {s['retries']}",
        f"Model cost:       ${float(s.get('model_api_cost_usd', s.get('cost_usd', 0)) or 0):.2f} ({cost_label})",
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

    lines.extend(["", "Harness trace:"])
    if runtime_trace.get("available"):
        rt = runtime_trace.get("summary") or {}
        lines.append(
            f"  available · {runtime_trace.get('run_count', 0)} run(s) · {int(rt.get('iters') or 0)} iterations · "
            f"{runtime_trace.get('trace_url', '')}"
        )
    else:
        lines.append(f"  not available · {runtime_trace.get('reason') or 'no trace recorded'}")

    if phases:
        lines.extend(["", "Time by lifecycle state:"])
        for phase in phases[:10]:
            lines.append(
                f"  {phase['status']:<18} {_phase_class(phase['status']):<8} {_duration(phase['seconds']):>10}  {float(phase['percent']):5.1f}%"
            )

    stopped = [item for item in intervals if item.get("classification") == "stopped"]
    if stopped:
        lines.extend(["", "Stopped/error residences:"])
        for item in stopped[-8:]:
            lines.append(f"  {item.get('status', ''):<18} {_duration(item.get('duration_seconds', 0)):>10}")
            lines.append(f"    {item.get('interpretation') or ''}")
            entry = item.get("entry_event") or {}
            exit_event = item.get("exit_event") or {}
            if entry:
                lines.append(f"    entered: {entry.get('summary') or entry.get('event_type')}")
            if exit_event:
                lines.append(f"    exited:  {exit_event.get('summary') or exit_event.get('event_type')}")

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

    lines.extend(["", f"GUI analyzer: /static/job_trace.html?job={job.id}"])
    body = "\n".join(lines)
    _emit(session, body, allow_prompt)
    return CommandResult(ok=True, message="Job analysis generated.", data={"analysis": analysis})
