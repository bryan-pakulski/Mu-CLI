"""Artifact-based plan and report-only review stages, executed in sandboxes."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from .models import (
    AttentionReason,
    Job,
    JobStatus,
    resolve_stage_model,
)

logger = logging.getLogger("mucli")

GenerateFn = Callable[[Job, str, str, Dict[str, Any]], Tuple[str, Dict[str, Any]]]

PIPELINE_STAGED = "staged"
VERDICT_APPROVE = "approve"
VERDICT_REQUEST_CHANGES = "request_changes"


def is_staged(job: Job) -> bool:
    return str((job.execution or {}).get("pipeline") or "").strip().lower() == PIPELINE_STAGED


def publish_mode(job: Job) -> str:
    execution = dict(job.execution or {})
    if not execution.get("git_enabled", True):
        return "none"
    return str(execution.get("publish_mode") or "none").strip().lower() or "none"


def merge_metadata(service, job_id: str, **updates: Any) -> Job:
    job = service.get(job_id)
    meta = dict(job.metadata or {})
    meta.update(updates)
    return service.store.update_runtime_fields(job_id, metadata_json=meta)


def _render_plan_markdown(plan: Dict[str, Any]) -> str:
    """Interim plan.md renderer (Phase 1); Phase 2 PlanDoc supersedes it."""
    lines = ["## Summary", str(plan.get("summary") or "").strip(), "", "## Approach"]
    lines += [f"{i}. {step}" for i, step in enumerate(plan.get("steps") or [], 1)]
    lines += ["", "## Acceptance criteria"]
    lines += [f"- [AC-{i}] {ac}" for i, ac in enumerate(plan.get("acceptance_criteria") or [], 1)]
    lines += ["", "## Checks"]
    lines += [f"- suite: check{i}   cmd: {cmd}"
              for i, cmd in enumerate(plan.get("validation_commands") or [], 1)]
    return "\n".join(lines).rstrip() + "\n"


def _write_plan_artifact(job: Job, plan: Dict[str, Any], raw_text: str) -> Dict[str, str]:
    """Write plan.md (+ raw planner output) to the job artifact folder.

    Best-effort: an artifact I/O failure must not fail the plan stage while
    ``metadata.plan`` remains the compatibility copy (removed in Phase 9).
    """
    try:
        from .core.artifacts import artifacts_for

        from .core.plan_ops import record_planner_plan

        art = artifacts_for(job).ensure()
        art.write_text("plans/planner_raw.txt", str(raw_text or ""))
        if record_planner_plan(job, plan) is None and not art.read_text("plan.md"):
            # Invalid planner output: keep a readable plan.md for the editor.
            art.write_text("plan.md", _render_plan_markdown(plan))
        return art.pointers()
    except (OSError, ValueError):
        logger.debug("plan artifact write failed", exc_info=True)
        return {}


def job_workspace(job: Job) -> str:
    workspace = str(job.worktree or "")
    if not workspace or not os.path.isdir(workspace):
        workspace = str((job.metadata or {}).get("repository_root") or job.repository or "")
    return workspace


# ---------------------------------------------------------------- parsing

_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def extract_json(text: str) -> Dict[str, Any]:
    """Best-effort: fenced JSON block first, then the outermost {...} span."""
    raw = str(text or "")
    candidates: List[str] = [m.group(1) for m in _FENCE_RE.finditer(raw)]
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        candidates.append(raw[start:end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            return value
    return {}


def _str_list(value: Any, *, limit: int = 50) -> List[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text[:1000])
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------------- plan

def build_plan_prompt(job: Job) -> str:
    lines = [
        "You are the PLANNING stage of an automated engineering pipeline.",
        "Inspect the workspace read-only and produce an implementation plan for the ticket below.",
        "",
        f"Title: {job.title}",
    ]
    if job.description:
        lines.extend(["", "Description:", job.description])
    if job.acceptance_criteria:
        lines.extend(["", "Acceptance criteria already supplied:", *[f"- {c}" for c in job.acceptance_criteria]])
    if job.validation_commands:
        lines.extend(["", "Validation commands already supplied:", *[f"- {c}" for c in job.validation_commands]])
    feedback = str((job.metadata or {}).get("replan_feedback") or "").strip()
    if feedback:
        lines.extend(["", "A human rejected the previous plan. Address this feedback:", feedback])
    lines.extend([
        "",
        "Always produce concrete, testable acceptance_criteria derived from the ticket.",
        "Respond with JSON only:",
        '{"summary": "...", "steps": ["..."], "acceptance_criteria": ["..."], '
        '"validation_commands": ["exact shell command run from the workspace root"]}',
        "Validation commands must be deterministic, non-interactive test/lint/build commands",
        "(for example `python3 -m pytest tests/test_x.py -q`). No pipes, redirects or chained commands.",
    ])
    return "\n".join(lines)


def parse_plan(text: str) -> Dict[str, Any]:
    data = extract_json(text)
    summary = str(data.get("summary") or "").strip()
    if not data and str(text or "").strip():
        summary = str(text).strip()[:4000]
    return {
        "summary": summary[:4000],
        "steps": _str_list(data.get("steps")),
        "acceptance_criteria": _str_list(data.get("acceptance_criteria")),
        "validation_commands": _str_list(data.get("validation_commands"), limit=20),
        "parsed": bool(data),
    }


def run_plan_stage(service, job_id: str, *, generate_fn: GenerateFn) -> Job:
    job = service.get(job_id)
    model = resolve_stage_model(job.execution, "plan")
    text, meta = generate_fn(job, build_plan_prompt(job), job_workspace(job), model)
    if job.worker_id and not service.store.assert_lease(job_id, job.worker_id):
        return service.get(job_id)
    cost = max(0, float((meta or {}).get("cost_usd") or 0))
    if cost:
        service.store.update_runtime_fields(job_id, cost_usd=float(service.get(job_id).cost_usd or 0) + cost)
    plan = parse_plan(text)
    plan["model"] = {"provider": model.get("provider", ""), "model": model.get("model", "")}
    plan["cost_usd"] = float((meta or {}).get("cost_usd", 0.0) or 0.0)
    service.store.append_event(job_id, "plan_created", reason="planning stage completed", payload=plan)
    artifact_pointers = _write_plan_artifact(job, plan, text)
    job = merge_metadata(service, job_id, plan=plan, artifacts=artifact_pointers)
    try:
        from .dynamic_checks import apply_plan_contract  # task 5 hook

        job = apply_plan_contract(service, job_id, plan)
    except ImportError:
        pass
    return job


# ----------------------------------------------------------------- review

def build_stage_review_prompt(job: Job, patch: str, stat: str, round_no: int) -> str:
    from .core.artifacts import artifacts_for
    from .core.plan_ops import approved_plan_text
    art = artifacts_for(job)
    checks = art.read_json("checks/summary.json", {}) or {}
    return "\n".join([
        "You are the REVIEW stage. Produce a report; a human decides what happens next.",
        "Inspect the workspace read-only. Check results below may include failures.",
        f"Title: {job.title}", job.description or "",
        "Approved plan:", approved_plan_text(job) or art.read_text("plan.md") or "",
        "Acceptance criteria:", *job.acceptance_criteria,
        "Checks:", json.dumps(checks), "Diff stat:", stat, "Patch:", patch[:120000],
        "Respond with JSON only, with this schema:",
        '{"summary":"overview and risks", "plan_coverage":[{"criterion_id":"AC-1",'
        '"status":"met|partial|unmet|unverifiable","evidence":"file:line or test"}],'
        '"findings":[{"id":"F-1","severity":"blocker|major|minor|nit",'
        '"category":"correctness|security|regression|tests|perf|maintainability|scope",'
        '"file":"path","line_start":1,"line_end":1,"title":"short",'
        '"detail":"why it matters","suggestion":"fix","confidence":"high|medium|low"}],'
        '"out_of_scope_changes":[]}',
        "Do not choose a workflow action or mark findings dismissed/fixed. The host computes readiness.",
    ])


def parse_stage_review(text: str) -> Dict[str, Any]:
    data = extract_json(text)
    verdict = str(data.get("verdict") or "").strip().lower().replace("-", "_").replace(" ", "_")
    if verdict in {"approved", "pass", "accept", "lgtm"}:
        verdict = VERDICT_APPROVE
    if verdict in {"changes_requested", "reject", "fail", "request_change"}:
        verdict = VERDICT_REQUEST_CHANGES
    feedback = _str_list(data.get("feedback"))
    if verdict not in {VERDICT_APPROVE, VERDICT_REQUEST_CHANGES}:
        verdict = VERDICT_REQUEST_CHANGES
        feedback = feedback or ["Reviewer response could not be parsed; re-check the change against the ticket."]
    return {
        "verdict": verdict,
        "summary": str(data.get("summary") or "").strip()[:4000],
        "feedback": feedback,
        "parsed": bool(data),
    }


def _diff(service, job_id: str) -> Tuple[str, str, str]:
    try:
        from .review import build_job_diff

        diff = build_job_diff(service, job_id)
        return diff.patch, diff.stat, diff.head_sha
    except Exception as exc:  # noqa: BLE001 — non-git / missing base: review without diff
        logger.info("stage review: diff unavailable for %s: %s", job_id, exc)
        return "", "", ""


def apply_review_verdict(service, job_id: str, review: Dict[str, Any]) -> int:
    """Always stop at REVIEW_DONE, irrespective of the model's opinion."""
    from .core.artifacts import artifacts_for
    from .core.review_report import ReviewReport
    job = service.get(job_id)
    if job.status != JobStatus.REVIEWING:
        return 0
    if job.worker_id and not service.store.assert_lease(job_id, job.worker_id):
        return 6
    art = artifacts_for(job).ensure()
    report = ReviewReport.from_legacy(review, job_id=job.id)
    report.job_id = job.id
    checks = art.read_json("checks/summary.json", {}) or {}
    report.checks_context = {"new_failures": int(checks.get("new_failures") or 0),
                             "preexisting_failures": int(checks.get("preexisting_failures") or 0)}
    for finding in report.findings:
        finding.disposition = "open"
    report.recompute()
    rounds = list((job.metadata or {}).get("review_rounds") or [])
    payload = {**report.to_dict(), "round": len(rounds) + 1}
    art.write_json("review.json", report.to_dict())
    art.write_json(f"logs/review.round{len(rounds) + 1}.json", payload)
    rounds.append(payload)
    merge_metadata(service, job_id, review_rounds=rounds)
    service.store.append_event(job_id, "review_completed", reason=report.verdict, payload=payload)
    service.require_human(job_id, AttentionReason.REVIEW_DONE,
                          report.summary or "Review report ready. Choose push, re-prompt, recheck, or discard.",
                          payload={"verdict": report.verdict, "merge_readiness": report.merge_readiness})
    return 0


def run_review_stage(service, job_id: str, *, generate_fn: GenerateFn) -> int:
    job = service.get(job_id)
    if job.status != JobStatus.REVIEWING:
        return 0
    model = resolve_stage_model(job.execution, "review")
    patch, stat, head = _diff(service, job_id)
    round_no = len((job.metadata or {}).get("review_rounds") or []) + 1
    prompt = build_stage_review_prompt(job, patch, stat, round_no)
    text, meta = generate_fn(job, prompt, job_workspace(job), model)
    if job.worker_id and not service.store.assert_lease(job_id, job.worker_id):
        return 6
    cost = max(0, float((meta or {}).get("cost_usd") or 0))
    if cost:
        service.store.update_runtime_fields(job_id, cost_usd=float(service.get(job_id).cost_usd or 0) + cost)
    raw = extract_json(text)
    review = raw if "findings" in raw else parse_stage_review(text)
    if not raw:
        review = {"summary": "The reviewer did not return a valid report.", "findings": [
            {"id": "F-1", "severity": "blocker", "title": "Invalid reviewer output", "detail": str(text)[:4000]}]}
    review.update({
        "head_sha": head,
        "reviewed_head": head,
        "model": {"provider": model.get("provider", ""), "model": model.get("model", "")},
        "cost_usd": float((meta or {}).get("cost_usd", 0.0) or 0.0),
    })
    return apply_review_verdict(service, job_id, review)


def _write_review_artifact(job: Job, review: Dict[str, Any], round_no: int) -> None:
    """Persist review.json (report schema, guide §4.2). Best-effort in Phase 1."""
    try:
        from .core.artifacts import artifacts_for
        from .core.review_report import ReviewReport

        art = artifacts_for(job).ensure()
        payload = dict(review, round=round_no)
        report = ReviewReport.from_legacy(payload, job_id=job.id)
        art.write_json("review.json", report.to_dict())
        art.write_json(f"logs/review_raw.round{round_no}.json", payload)
    except (OSError, ValueError):
        logger.debug("review artifact write failed", exc_info=True)


def review_feedback_lines(payload: Dict[str, Any]) -> List[str]:
    lines = ["", f"Reviewer requested changes (round {payload.get('round', '?')}):"]
    if payload.get("summary"):
        lines.append(str(payload["summary"]))
    lines.extend(f"- {item}" for item in payload.get("feedback") or [])
    return lines


# ------------------------------------------------------- default generator

class StageInteractionError(RuntimeError):
    """An unattended stage session hit a human gate (approval/question)."""


def _stage_tool_allowlist() -> frozenset:
    from .acceptance import REVIEWER_TOOL_ALLOWLIST

    return frozenset(REVIEWER_TOOL_ALLOWLIST) - {"bash"}


STAGE_TOOL_ALLOWLIST = _stage_tool_allowlist()


def default_generate(service) -> GenerateFn:
    """Every production agent stage runs in the shared sandbox runtime."""
    from .sandbox.runner import generate
    return generate(service)
