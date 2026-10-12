"""Durable job v2 staged pipeline: plan -> implement -> verify -> review.

The implementation stage is still the existing worker/runner. This module
adds the two model-driven stages around it:

* **plan** runs before the first implementation attempt (status PLANNING)
  with the ``plan`` stage model and stores ``metadata.plan``.
* **review** runs after deterministic verification passed (status
  REVIEWING) with the ``review`` stage model. ``approve`` advances to
  PUBLISHING (git + publish enabled) or READY_FOR_REVIEW; ``request_changes``
  requeues the implementation with the reviewer's feedback so the whole
  RUNNING -> VERIFYING -> REVIEWING loop re-runs. More than
  ``execution.max_review_rounds`` rejections park the job on a human with
  ``AttentionReason.REVIEW_STALLED``.

``generate_fn(job, prompt, workspace, model) -> (text, meta)`` is injectable
so tests need no provider; :func:`default_generate` builds a read-only MuCLI
session with the resolved stage provider/model.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from .models import (
    DEFAULT_MAX_REVIEW_ROUNDS,
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


def max_review_rounds(job: Job) -> int:
    try:
        value = int((job.execution or {}).get("max_review_rounds") or DEFAULT_MAX_REVIEW_ROUNDS)
    except (TypeError, ValueError):
        value = DEFAULT_MAX_REVIEW_ROUNDS
    return max(1, value)


def merge_metadata(service, job_id: str, **updates: Any) -> Job:
    job = service.get(job_id)
    meta = dict(job.metadata or {})
    meta.update(updates)
    return service.store.update_runtime_fields(job_id, metadata_json=meta)


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
    plan = parse_plan(text)
    plan["model"] = {"provider": model.get("provider", ""), "model": model.get("model", "")}
    plan["cost_usd"] = float((meta or {}).get("cost_usd", 0.0) or 0.0)
    service.store.append_event(job_id, "plan_created", reason="planning stage completed", payload=plan)
    job = merge_metadata(service, job_id, plan=plan)
    try:
        from .dynamic_checks import apply_plan_contract  # task 5 hook

        job = apply_plan_contract(service, job_id, plan)
    except ImportError:
        pass
    return job


# ----------------------------------------------------------------- review

def build_stage_review_prompt(job: Job, patch: str, stat: str, round_no: int) -> str:
    lines = [
        "You are the REVIEW stage of an automated engineering pipeline.",
        f"Review round {round_no}. Deterministic validation already passed.",
        "Decide whether the change fully and correctly implements the ticket.",
        "",
        f"Title: {job.title}",
    ]
    if job.description:
        lines.extend(["", "Description:", job.description])
    if job.acceptance_criteria:
        lines.extend(["", "Acceptance criteria:", *[f"- {c}" for c in job.acceptance_criteria]])
    plan = dict((job.metadata or {}).get("plan") or {})
    if plan.get("summary"):
        lines.extend(["", "Agreed plan:", str(plan["summary"])])
    lines.extend(["", "Diff stat:", stat or "(unavailable)", "", "Patch:", (patch or "(unavailable)")[:120_000]])
    lines.extend([
        "",
        "Respond with JSON only:",
        '{"verdict": "approve" | "request_changes", "summary": "...", '
        '"feedback": ["concrete change the implementer must make"]}',
    ])
    return "\n".join(lines)


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
    job = service.get(job_id)
    if job.status != JobStatus.REVIEWING:
        return 0
    rounds = list((job.metadata or {}).get("review_rounds") or [])
    review = {**review, "round": len(rounds) + 1}
    rounds.append(review)
    job = merge_metadata(service, job_id, review_rounds=rounds)
    service.store.append_event(job_id, "review_completed", reason=review["verdict"], payload=review)

    if review["verdict"] == VERDICT_APPROVE:
        if publish_mode(job) != "none":
            service.transition(job_id, JobStatus.PUBLISHING, reason="reviewer approved; publishing", payload=review)
            return 0
        service.transition(job_id, JobStatus.READY_FOR_REVIEW, reason="reviewer approved", payload=review)
        try:
            from .base_drift import apply_base_drift

            apply_base_drift(service, job_id, source="review")
        except Exception as exc:  # noqa: BLE001 — drift check must not undo READY
            service.store.append_event(job_id, "base_drift_check_failed", reason=str(exc)[:400])
        return 0

    if len(rounds) >= max_review_rounds(job):
        service.require_human(
            job_id,
            AttentionReason.REVIEW_STALLED,
            f"Reviewer requested changes in {len(rounds)} consecutive rounds "
            f"(max {max_review_rounds(job)}): " + "; ".join(review.get("feedback") or [])[:600],
            payload=review,
        )
        return 25
    service.store.append_event(
        job_id, "review_changes_requested",
        reason="reviewer requested changes; re-running implementation",
        payload=review,
    )
    service.transition(job_id, JobStatus.QUEUED, reason="reviewer requested changes", payload=review)
    return 10


def run_review_stage(service, job_id: str, *, generate_fn: GenerateFn) -> int:
    job = service.get(job_id)
    if job.status != JobStatus.REVIEWING:
        return 0
    model = resolve_stage_model(job.execution, "review")
    patch, stat, head = _diff(service, job_id)
    round_no = len((job.metadata or {}).get("review_rounds") or []) + 1
    prompt = build_stage_review_prompt(job, patch, stat, round_no)
    text, meta = generate_fn(job, prompt, job_workspace(job), model)
    review = parse_stage_review(text)
    review.update({
        "head_sha": head,
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


def _stage_tool_allowlist() -> frozenset:
    from .acceptance import REVIEWER_TOOL_ALLOWLIST

    return frozenset(REVIEWER_TOOL_ALLOWLIST) - {"bash"}


STAGE_TOOL_ALLOWLIST = _stage_tool_allowlist()


def default_generate(service) -> GenerateFn:
    """Read-only agent session using the resolved stage provider/model."""

    def _generate(job: Job, prompt: str, workspace: str, model: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        import copy

        import mucli as mucli_entry

        from .acceptance import REVIEWER_TOOL_ALLOWLIST
        from .ui import JobUI
        from .worker import _base_args

        args = copy.copy(_base_args(job))
        args.provider = str(model.get("provider") or args.provider)
        args.model = str(model.get("model") or args.model)
        stage = str(model.get("stage") or "stage")
        args.session = f"job-{job.id[:20]}-{stage}"
        args.workspace = [workspace] if workspace else []
        args.yolo = False
        args.system = (
            f"You are the {stage} stage of an automated engineering pipeline. "
            "Read-only. Never modify files. Answer with the requested JSON only."
        )
        session = mucli_entry.build_session(args, JobUI(service, job.id), allow_prompt=False)
        session.variables["provider_recovery_policy"] = "auto"
        session.variables["internal_session_kind"] = "durable_job"
        session.variables["durable_job_id"] = job.id
        session.variables["yolo"] = False
        session.variables["plan_mode"] = True
        session.variables["max_iterations"] = int((job.execution or {}).get("stage_max_iterations", 16) or 16)
        try:
            from mu.tools.descriptors import TOOLS

            # Stage sessions are unattended and read-only: ``bash`` needs an
            # approval nobody can give (yolo is off), so it is withheld and
            # the read-only search/read tools cover workspace inspection.
            session.disabled_tools = sorted({t.name for t in TOOLS} - STAGE_TOOL_ALLOWLIST)
        except Exception:  # noqa: BLE001
            pass
        before = dict(getattr(session.session_manager, "token_counts", {}) or {})
        from mu.ui.exceptions import InteractionRequired

        try:
            session.send_message(prompt)
        except InteractionRequired as gate:
            # InteractionRequired is a BaseException; left alone it escapes
            # every ``except Exception`` stage handler and kills the worker
            # without a recorded outcome (job stuck in PLANNING).
            raise StageInteractionError(
                f"{stage} stage needed human input ({gate.kind}): {gate.detail}"
            ) from None
        finally:
            try:
                session.shutdown()
            except Exception:  # noqa: BLE001
                pass
        after = dict(getattr(session.session_manager, "token_counts", {}) or {})
        text = ""
        for message in reversed(getattr(session.session_manager, "history", []) or []):
            if message.get("role") == "assistant":
                parts = [p.get("text", "") for p in message.get("parts", []) if p.get("type") == "text"]
                if parts:
                    text = "\n".join(parts)
                    break
        cost = float(after.get("total_cost", 0.0) or 0.0) - float(before.get("total_cost", 0.0) or 0.0)
        return text, {"provider": args.provider, "model": args.model, "cost_usd": max(0.0, cost)}

    return _generate
