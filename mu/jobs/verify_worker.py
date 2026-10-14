"""Checks phase of a durable job: run the check suites, then apply the readiness policy.

Launched through ``mu.jobs.worker --phase verification`` (``python -m
mu.jobs.verify_worker`` remains a compatibility shim).
"""

from __future__ import annotations

import os
from typing import Optional

from .lease import run_leased
from .models import AttentionReason, JobStatus
from .receipt import refresh_receipt as _refresh_receipt
from .service import get_default_job_service
from .verification import VerificationRun


def verification_feedback(run: VerificationRun) -> dict:
    failed = []
    for check in run.checks:
        if check.passed:
            continue
        failed.append({
            "command": check.command,
            "return_code": check.return_code,
            "timed_out": check.timed_out,
            "error": check.error,
            "stdout": check.stdout[-6000:],
            "stderr": check.stderr[-6000:],
        })
    return {
        "verification_id": run.id,
        "status": run.status,
        "verified_head_sha": run.head_sha,
        "manifest_path": run.manifest_path,
        "summary": run.summary,
        "failed_checks": failed,
        "dirty_status": run.dirty_status[-6000:] if run.dirty else "",
    }


_ENVIRONMENT_PATTERNS = (
    r"ModuleNotFoundError: No module named", r"ImportError: No module named", r"Cannot find module '",
    r"Error: Cannot find package", r"ERR_MODULE_NOT_FOUND", r"getaddrinfo (?:ENOTFOUND|EAI_AGAIN)",
    r"Temporary failure in name resolution", r"Could not resolve host", r"command not found",
    r"node_modules(?:/\.bin)?: No such file", r"Failed to download|failed to fetch|Network is unreachable",
)


def environment_failure(result: dict) -> str:
    """The missing-dependency / no-network symptom in a failing suite's output ('' when none)."""
    import re
    text = f"{result.get('stdout') or ''}\n{result.get('stderr') or ''}"
    for pattern in _ENVIRONMENT_PATTERNS:
        match = re.search(pattern + r"[^\n]{0,120}", text)
        if match:
            return match.group(0).strip()
    return ""


def futile_fix_reason(job, run: VerificationRun) -> str:
    """Why an automatic fix attempt cannot help ('' = schedule one).

    * every newly failing suite fails for an environment reason (missing dependency, no
      network) — the implementer cannot fix the sandbox;
    * the previous fix attempt changed nothing (same HEAD and worktree status as the run
      before it)."""
    failing = [s for s in run.summary.get("suites", []) if s.get("new_failures")]
    symptoms = [(s.get("suite"), environment_failure(s)) for s in failing]
    if failing and all(symptom for _suite, symptom in symptoms):
        detail = "; ".join(f"`{suite}`: {symptom}" for suite, symptom in symptoms[:4])
        envs = sorted({s.get("environment") for s in failing if s.get("environment")})
        where = f"build environment {', '.join(f'`{e}`' for e in envs)}" if envs else "the job sandbox"
        return (f"environment: checks fail because {where} lacks dependencies or network access ({detail}). "
                "A code fix cannot change that; add the dependencies to the environment (or allow the registry "
                "in Extra egress domains), then re-run checks.")
    try:
        from .core.artifacts import artifacts_for
        art = artifacts_for(job)
        runs = art.check_runs()
        if len(runs) >= 2 and run.summary.get("trigger") == "auto":
            previous = art.read_json(f"checks/run-{runs[-2]}/summary.json", {}) or {}
            current = run.summary
            same_head = previous.get("head_sha") and previous.get("head_sha") == current.get("head_sha")
            same_failures = sorted(s.get("suite") for s in previous.get("suites", []) if s.get("new_failures")) == \
                sorted(s.get("suite") for s in failing)
            if same_head and same_failures and previous.get("dirty_worktree") == current.get("dirty_worktree") \
                    and (job.metadata or {}).get("last_fix_head") == current.get("head_sha"):
                return "the previous fix attempt changed nothing (same commit and the same failing suites)"
    except (OSError, ValueError):
        pass
    return ""


def missing_tool_message(summary: dict) -> str:
    """Name the build environment that lacks a tool (or the shared image when there is none)."""
    missing = list(summary.get("missing_tools") or [])
    owners = dict(summary.get("missing_tool_environments") or {})
    docker_suites = list(summary.get("missing_docker_suites") or [])
    parts = []
    for item in docker_suites:
        if item.get("environment"):
            parts.append(f"Suite `{item['suite']}` needs Docker; enable Docker for binding `{item['environment']}`.")
    by_env: dict = {}
    for tool in missing:
        if tool == "docker" and any(d.get("environment") for d in docker_suites):
            continue
        by_env.setdefault(owners.get(tool) or "", []).append(tool)
    for env, tools in by_env.items():
        names = ", ".join(f"`{t}`" for t in tools)
        if env:
            parts.append(f"Checks need {names}, which build environment `{env}` doesn't include. Install it in that "
                         "environment (container mode), or choose another (Change), then re-run checks.")
        else:
            from .job_settings import rule_or
            image = rule_or("sandbox.image", "mucli-job-runtime:latest")
            parts.append(f"Checks need {names}, which the job sandbox image {image} doesn't include. "
                         "Use a toolchain image that has it (Settings → Sandbox), then re-run checks.")
    return " ".join(parts)


def apply_checks_result(service, job_id: str, run: VerificationRun) -> int:
    """Only new failures enter the bounded implementation repair loop."""
    current = service.get(job_id)
    if current.status != JobStatus.VERIFYING:
        return 0
    feedback = verification_feedback(run)
    new_commands = {s["command"] for s in run.summary.get("suites", []) if s.get("new_failures")}
    feedback["failed_checks"] = [c for c in feedback["failed_checks"] if c["command"] in new_commands]
    if run.status == "missing_contract":
        service.require_human(job_id, AttentionReason.VERIFICATION_REQUIRED,
                              "Configure at least one check suite before review and push.", payload=feedback)
        return 21
    missing = list(run.summary.get("missing_tools") or [])
    if missing:
        # The implementer cannot install toolchains, so a repair loop would be wasted spend.
        service.require_human(job_id, AttentionReason.VERIFICATION_REQUIRED, missing_tool_message(run.summary),
            payload={**feedback, "missing_tools": missing,
                     "missing_tool_environments": run.summary.get("missing_tool_environments") or {}})
        _refresh_receipt(service, job_id)
        return 22
    from .job_settings import load_settings
    settings = load_settings().get("checks") or {}
    maximum = max(0, int((current.execution.get("checks") or {}).get("max_fix_attempts", settings.get("max_fix_attempts", 2))))
    core = service.store.core_state(job_id)
    futile = futile_fix_reason(current, run) if run.summary.get("new_failures", 0) > 0 else ""
    if futile:
        service.store.append_event(job_id, "fix_attempt_skipped", reason=futile, payload=feedback)
        if futile.startswith("environment"):
            service.require_human(job_id, AttentionReason.VERIFICATION_REQUIRED, futile, payload=feedback)
            _refresh_receipt(service, job_id)
            return 22
    if run.summary.get("new_failures", 0) > 0 and core.fix_attempts < maximum and not futile:
        service.store.set_fix_attempts(job_id, core.fix_attempts + 1)
        meta = dict(service.get(job_id).metadata or {})
        meta["last_fix_head"] = run.summary.get("head_sha") or ""
        service.store.update_runtime_fields(job_id, metadata_json=meta)
        service.store.append_event(job_id, "verification_failed", reason="new failures; fix attempt scheduled", payload=feedback)
        service.transition(job_id, JobStatus.QUEUED, reason="checks require implementation fixes", payload=feedback)
        return 10
    # The reviewer needs the actual checked worktree. Do not retire it here.
    service.transition(job_id, JobStatus.REVIEWING, reason="checks complete; review report next", payload=feedback)
    _refresh_receipt(service, job_id)
    return 0


def run_verification(job_id: str, worker_id: str, *, lease_ttl_seconds: int = 45) -> int:
    return run_leased(
        get_default_job_service(), job_id, worker_id, phase="verification",
        lease_ttl_seconds=lease_ttl_seconds, body=_verify, statuses=(JobStatus.VERIFYING,),
        reject_event="verification_worker_rejected", release_reason="verification worker exited",
    )


def _verify(service, job, lease) -> int:
    job_id, worker_id = job.id, lease.worker_id
    try:
        from .checks import ChecksStage
        run = ChecksStage(service).verify(job)
        if not lease.owned():
            # Lease expired mid-run: a retried verifier takes over. Do not
            # apply results this worker no longer owns.
            service.store.append_event(
                job_id,
                "verification_lease_lost_midrun",
                reason="heartbeat lost; verifier abandoning result",
                payload={"worker_id": worker_id},
            )
            return 6
        return apply_checks_result(service, job_id, run)
    except Exception as exc:
        from .service import JobStateError
        from .sandbox.environments import EnvironmentBlocked
        if isinstance(exc, JobStateError):
            raise
        if isinstance(exc, EnvironmentBlocked):
            try:
                if lease.owned() and service.get(job_id).status == JobStatus.VERIFYING:
                    service.require_human(job_id, AttentionReason.ENVIRONMENT_FAILURE, str(exc)[:2000],
                                          payload={"stage": "checks"})
                    _refresh_receipt(service, job_id)
            except Exception:
                pass
            return 23
        try:
            service.store.append_event(
                job_id,
                "verification_worker_error",
                reason=str(exc),
                payload={"process_id": os.getpid()},
            )
            if lease.owned() and service.get(job_id).status == JobStatus.VERIFYING:
                service.transition(job_id, JobStatus.FAILED, reason="checks stage failed",
                                   payload={"error": str(exc)[:1000], "exception_type": type(exc).__name__})
            _refresh_receipt(service, job_id)
        except Exception:
            pass
        # A failed check supervisor requires an explicit retry. No agent runs
        # on the host when the container runtime is unavailable.
        return 4


def main(argv: Optional[list] = None) -> int:
    from .worker import main as worker_main
    return worker_main(argv, phase="verification")


if __name__ == "__main__":
    raise SystemExit(main())
