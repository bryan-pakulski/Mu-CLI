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
    mode = str(execution.get("publish_mode") or "").strip().lower()
    if not mode:
        from .job_settings import rule
        mode = str(rule("publish.mode") or "").strip().lower()
    return mode or "none"


def merge_metadata(service, job_id: str, **updates: Any) -> Job:
    """Atomic merge of top-level metadata keys (no lost concurrent updates)."""
    return service.store.patch_metadata(job_id, updates)


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
    """Persist the stage handoff; artifact failure fails the planning stage."""
    from .core.artifacts import artifacts_for
    from .core.plan_ops import record_planner_plan
    art = artifacts_for(job).ensure()
    art.write_text("plans/planner_raw.txt", str(raw_text or ""))
    if record_planner_plan(job, plan) is None and not art.read_text("plan.md"):
        art.write_text("plan.md", _render_plan_markdown(plan))
    return art.pointers()


def ledger_cost(job: Job) -> Optional[float]:
    """Total spend in the job's cost ledger (``costs.jsonl``, written by every stage relay);
    None when the job has no ledger yet."""
    from .core.artifacts import artifacts_for
    art = artifacts_for(job)
    if not os.path.isfile(art.costs):
        return None
    return float(sum(art.stage_costs().values()))


def sync_job_cost(service, job_id: str) -> float:
    """Make ``job.cost_usd`` equal the cost ledger (the single source of truth).

    Idempotent, so it is safe after paused, failed or restarted stages and never double
    counts; a job without a ledger keeps its stored total."""
    job = service.get(job_id)
    total = ledger_cost(job)
    if total is None:
        return float(job.cost_usd or 0.0)
    if abs(total - float(job.cost_usd or 0.0)) > 1e-9:
        service.store.update_runtime_fields(job_id, cost_usd=total)
    return total


def _generate_and_record(service, job_id: str, generate_fn: GenerateFn, job: Job, prompt: str, model):
    """Run a stage; its spend (also when it pauses or fails) lands in ``job.cost_usd``."""
    reported = 0.0
    try:
        text, meta = generate_fn(job, prompt, job_workspace(job), model)
        reported = max(0.0, float((meta or {}).get("cost_usd") or 0))
        return text, meta
    finally:
        try:
            current = service.get(job_id)
            if ledger_cost(current) is None and reported:
                # A generator that keeps no ledger (tests, legacy): count what it reported.
                service.store.update_runtime_fields(job_id, cost_usd=float(current.cost_usd or 0) + reported)
            else:
                sync_job_cost(service, job_id)
        except Exception:  # noqa: BLE001 - cost bookkeeping never masks the stage outcome
            logger.debug("job cost sync failed for %s", job_id, exc_info=True)


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
    try:
        from .sandbox.detect import planner_hint
        hint = str(planner_hint(job) or "").strip()
    except Exception:  # noqa: BLE001 - toolchain detection is a hint, never a failure
        hint = ""
    if hint:
        lines.extend(["", hint])
    lines.extend([
        "",
        "Always produce concrete, testable acceptance_criteria derived from the ticket.",
        "This stage runs unattended: no one answers questions. Where the ticket is unclear, choose with your best "
        "judgement and list each assumption you made in `assumptions`.",
        "Respond with JSON only:",
        '{"summary": "...", "steps": ["..."], "acceptance_criteria": ["..."], '
        '"validation_commands": ["exact shell command run from the workspace root"], "assumptions": ["..."]}',
        "Validation commands must be deterministic, non-interactive test/lint/build commands",
        "(for example `python3 -m pytest tests/test_x.py -q`). No pipes, redirects or chained commands.",
    ])
    return "\n".join(lines)


def parse_plan(text: str) -> Dict[str, Any]:
    data = extract_json(text)
    summary = str(data.get("summary") or "").strip()
    if not data and str(text or "").strip():
        summary = str(text).strip()[:4000]
    assumptions = _str_list(data.get("assumptions"))
    return {
        "summary": summary[:4000],
        "steps": _str_list(data.get("steps")),
        "acceptance_criteria": _str_list(data.get("acceptance_criteria")),
        "validation_commands": _str_list(data.get("validation_commands"), limit=20),
        "parsed": bool(data),
        # Jobs run unattended: the planner's assumptions land in the plan's "Risks / out of
        # scope" section, where the human reviewing the plan sees them.
        **({"risks": [f"Assumption: {a}" for a in assumptions]} if assumptions else {}),
    }


def run_plan_stage(service, job_id: str, *, generate_fn: GenerateFn) -> Job:
    job = service.get(job_id)
    model = resolve_stage_model(job.execution, "plan")
    text, meta = _generate_and_record(service, job_id, generate_fn, job, build_plan_prompt(job), model)
    if job.worker_id and not service.store.assert_lease(job_id, job.worker_id):
        return service.get(job_id)
    plan = parse_plan(text)
    plan["model"] = {"provider": model.get("provider", ""), "model": model.get("model", "")}
    plan["cost_usd"] = float((meta or {}).get("cost_usd", 0.0) or 0.0)
    service.store.append_event(job_id, "plan_created", reason="planning stage completed", payload=plan)
    artifact_pointers = _write_plan_artifact(job, plan, text)
    job = merge_metadata(service, job_id, artifacts=artifact_pointers)
    try:
        from .dynamic_checks import apply_plan_contract  # task 5 hook

        job = apply_plan_contract(service, job_id, plan)
    except ImportError:
        pass
    return job


# ----------------------------------------------------------------- review

_BRIEF_READ_TOOLS = {"read_file": "filename", "get_chunk": "filename", "list_dir": "path",
                     "search_for_string": "query", "search_references": "query", "retrieve_relevant_context": "query"}


def stage_brief(job: Job, stage: str, *, limit: int = 4000) -> str:
    """A compact hand-off from a finished stage to the next (no re-exploring the repo):
    what it inspected, the commands it ran, and its final message. Saved as
    ``briefs/<stage>.md``; empty when the stage left no transcript."""
    from .core.artifacts import artifacts_for
    art = artifacts_for(job)
    transcript = art.read_json(f"logs/{stage}-transcript.json", []) if art.exists() else []
    if not isinstance(transcript, list) or not transcript:
        return ""
    inspected, commands, final = [], [], ""
    for message in transcript:
        if not isinstance(message, dict):
            continue
        for part in message.get("parts") or []:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "tool_call":
                args = part.get("tool_args") if isinstance(part.get("tool_args"), dict) else {}
                name = str(part.get("tool_name") or "")
                if name in _BRIEF_READ_TOOLS and args.get(_BRIEF_READ_TOOLS[name]):
                    value = str(args[_BRIEF_READ_TOOLS[name]]).replace("/workspace/", "")
                    entry = f"{name}: {value}"[:200]
                    if entry not in inspected:
                        inspected.append(entry)
                elif name == "bash" and args.get("command"):
                    command = str(args["command"])[:200]
                    if command not in commands:
                        commands.append(command)
            elif part.get("type") == "text" and message.get("role") == "assistant" and str(part.get("text") or "").strip():
                final = str(part["text"]).strip()
    lines = [f"Brief from the {stage} stage (what it already established; don't redo it):"]
    if inspected:
        lines += ["Inspected:"] + [f"- {item}" for item in inspected[-40:]]
    if commands:
        lines += ["Commands run:"] + [f"- {item}" for item in commands[-15:]]
    if final:
        lines += ["Final message:", final[-1500:]]
    text = "\n".join(lines)[:limit]
    try:
        art.write_text(f"briefs/{stage}.md", text)
    except (OSError, ValueError):
        pass
    return text


def budget_patch(patch: str, limit: Optional[int] = None) -> str:
    """The review prompt's patch, bounded by ``limits.review_patch_chars``.

    Whole per-file diffs are included in order until the budget is spent; a single file
    larger than half the budget is cut; the rest are listed so the reviewer reads them
    from the workspace. Keeps the review stage far from the model's context window."""
    if limit is None:
        from .job_settings import rule_or
        limit = int(rule_or("limits.review_patch_chars", 60000))
    if len(patch) <= limit:
        return patch
    files = re.split(r"(?m)^(?=diff --git )", patch)
    kept, omitted, used = [], [], 0
    for chunk in (f for f in files if f):
        header = chunk.split("\n", 1)[0]
        if used + len(chunk) <= limit:
            kept.append(chunk)
            used += len(chunk)
        elif len(chunk) > limit // 2 and used + limit // 2 <= limit:
            kept.append(chunk[: limit // 2] + f"\n[... diff of this file cut at {limit // 2:,} characters; read the file ...]\n")
            used += limit // 2
        else:
            omitted.append(f"{header[len('diff --git '):]} ({len(chunk):,} characters)")
    if omitted:
        kept.append("[Diffs not included (read them in the workspace):\n- " + "\n- ".join(omitted) + "]\n")
    return "".join(kept)


def build_stage_review_prompt(job: Job, patch: str, stat: str, round_no: int) -> str:
    from .core.artifacts import artifacts_for
    from .core.plan_ops import approved_plan_text
    art = artifacts_for(job)
    checks = art.read_json("checks/summary.json", {}) or {}
    unverified = []
    if checks.get("verified") is False:
        from .checks import unverified_causes
        unverified = ["IMPORTANT: some checks could not run, so the change is UNVERIFIED (" + unverified_causes(checks)
                      + "). Do not treat the checks as passing; mark criteria you cannot confirm as unverifiable. "
                      "The verdict is capped at needs work."]
    return "\n".join([
        "You are the REVIEW stage. Produce a report; a human decides what happens next.",
        "Inspect the workspace read-only. Check results below may include failures.",
        *unverified,
        f"Title: {job.title}", job.description or "",
        "Approved plan:", approved_plan_text(job) or art.read_text("plan.md") or "",
        "Acceptance criteria:", *job.acceptance_criteria,
        stage_brief(job, "implement"),
        "Checks:", json.dumps(checks), "Diff stat:", stat, "Patch:", budget_patch(patch),
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
    if checks.get("verified") is False:
        # Unrunnable checks cap the verdict at needs_work (job_templates.md §5).
        report.checks_context["unverified"] = int(checks.get("unrunnable") or 1)
    for finding in report.findings:
        finding.disposition = "open"
    report.recompute()
    round_no = len(service.store.list_events_by_type(job_id, ("review_completed",))) + 1
    payload = {**report.to_dict(), "round": round_no}
    art.write_json("review.json", report.to_dict())
    art.write_json(f"logs/review.round{round_no}.json", payload)
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
    round_no = len(service.store.list_events_by_type(job_id, ("review_completed",))) + 1
    prompt = build_stage_review_prompt(job, patch, stat, round_no)
    text, meta = _generate_and_record(service, job_id, generate_fn, job, prompt, model)
    if job.worker_id and not service.store.assert_lease(job_id, job.worker_id):
        return 6
    raw = extract_json(text)
    review = raw if "findings" in raw else parse_stage_review(text)
    malformed = "findings" in raw and (not isinstance(raw["findings"], list) or any(not isinstance(f, dict) or f.get("severity") not in {"blocker", "major", "minor", "nit"} for f in raw["findings"]))
    if not raw or malformed:
        review = {"summary": "The reviewer did not return a valid report.", "findings": [
            {"id": "F-1", "severity": "blocker", "title": "Invalid reviewer output", "detail": str(text)[:4000]}]}
    review.update({
        "head_sha": head,
        "reviewed_head": head,
        "model": {"provider": model.get("provider", ""), "model": model.get("model", "")},
        "cost_usd": float((meta or {}).get("cost_usd", 0.0) or 0.0),
    })
    return apply_review_verdict(service, job_id, review)


def review_feedback_lines(payload: Dict[str, Any]) -> List[str]:
    lines = ["", f"Reviewer requested changes (round {payload.get('round', '?')}):"]
    if payload.get("summary"):
        lines.append(str(payload["summary"]))
    lines.extend(f"- {item}" for item in payload.get("feedback") or [])
    return lines


# ------------------------------------------------------- default generator

class StageInteractionError(RuntimeError):
    """An unattended stage session hit a human gate (approval/question)."""


# Tools an unattended plan/review stage may use: inspect only. Anything that
# mutates the worktree or session, runs commands or spawns work is excluded.
STAGE_TOOL_ALLOWLIST = frozenset({
    "read_file", "get_chunk", "list_dir", "search_for_string", "search_references",
    "retrieve_relevant_context", "get_workspace_details", "flush",
    "recall", "result_range", "result_head", "result_tail", "result_search",
})


def default_generate(service) -> GenerateFn:
    """Every production agent stage runs in the shared sandbox runtime."""
    from .sandbox.runner import generate
    return generate(service)
