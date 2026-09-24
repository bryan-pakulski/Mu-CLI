"""Independent acceptance-criteria review (Milestone 3, optional pass).

Deterministic verification proves *validation commands* passed. It says
nothing about whether the ticket's *acceptance criteria* are satisfied — the
receipt used to hard-code ``acceptance_criteria_machine_verified: False``.

This module runs a **read-only** reviewer session (no write/shell-mutation
tools) over the verified diff and asks for a structured verdict per
criterion. It is opt-in per job (``execution.acceptance_review``) because it
costs model tokens. Its verdict is evidence for the human reviewer, and a
``fail`` verdict blocks READY_FOR_REVIEW behind a human gate so "ready" never
hides a criterion the reviewer agent believes is unmet.

The parser is deliberately tolerant: the model may wrap JSON in a code fence
or prose. Anything unparseable becomes verdict ``unknown`` — never a pass.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from utils.config import HISTORY_DIR

from .models import Job
from .service import JobService

logger = logging.getLogger("mucli")

VERDICTS = ("pass", "fail", "unknown")

# Tools a reviewer may use: inspect only. Anything that mutates the worktree,
# the session, or spawns work is excluded — the reviewer must not "fix" the
# thing it is grading.
REVIEWER_TOOL_ALLOWLIST = frozenset({
    "read_file", "get_chunk", "list_dir", "search_for_string", "search_references",
    "retrieve_relevant_context", "get_workspace_details", "flush", "bash",
    "recall", "result_range", "result_head", "result_tail", "result_search",
})

_MAX_DIFF_CHARS = 60_000
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass
class CriterionVerdict:
    criterion: str
    verdict: str  # pass | fail | unknown
    evidence: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"criterion": self.criterion, "verdict": self.verdict, "evidence": self.evidence[:1200]}


@dataclass
class AcceptanceReview:
    job_id: str
    head_sha: str
    verdict: str  # pass | fail | unknown
    total: int
    met: int
    unmet: int
    unknown: int
    criteria: List[CriterionVerdict] = field(default_factory=list)
    summary: str = ""
    model: str = ""
    provider: str = ""
    cost_usd: float = 0.0
    raw_response: str = ""
    error: str = ""
    generated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "head_sha": self.head_sha,
            "verdict": self.verdict,
            "total": self.total,
            "met": self.met,
            "unmet": self.unmet,
            "unknown": self.unknown,
            "criteria": [c.to_dict() for c in self.criteria],
            "summary": self.summary[:2000],
            "model": self.model,
            "provider": self.provider,
            "cost_usd": self.cost_usd,
            "error": self.error[:600],
            "generated_at": self.generated_at,
        }


def build_review_prompt(job: Job, diff_patch: str, diff_stat: str) -> str:
    criteria = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(job.acceptance_criteria))
    patch = diff_patch
    truncated = ""
    if len(patch) > _MAX_DIFF_CHARS:
        patch = patch[:_MAX_DIFF_CHARS]
        truncated = "\n... [diff truncated; use read_file/search tools on the workspace for the rest] ..."
    return (
        "INDEPENDENT ACCEPTANCE REVIEW\n"
        "You are a strict reviewer. You did NOT write this change. Decide, per acceptance "
        "criterion, whether the diff below satisfies it. Use read-only tools to inspect the "
        "workspace when the diff alone is insufficient. Do not modify anything.\n\n"
        f"Ticket: {job.title}\n"
        + (f"Description:\n{job.description}\n\n" if job.description else "\n")
        + f"Acceptance criteria:\n{criteria}\n\n"
        + (f"Validation commands (already passed deterministically):\n"
           + "\n".join(f"- {v}" for v in job.validation_commands) + "\n\n" if job.validation_commands else "")
        + f"Diff stat:\n{diff_stat}\n\nDiff:\n{patch}{truncated}\n\n"
        "Respond with ONLY a JSON object, no prose before or after:\n"
        '{"criteria": [{"criterion": "<text>", "verdict": "pass|fail|unknown", "evidence": "<file:line or reason>"}], '
        '"summary": "<one paragraph>"}\n'
        "Rules: 'pass' only with concrete evidence in the diff or workspace; 'fail' when the criterion is "
        "clearly not met or contradicted; 'unknown' when you cannot tell. Cover every criterion exactly once, in order."
    )


def parse_review_response(text: str, criteria: List[str]) -> tuple[List[CriterionVerdict], str, str]:
    """Returns (verdicts, summary, error). Tolerant of code fences / prose."""
    raw = str(text or "").strip()
    if not raw:
        return [CriterionVerdict(c, "unknown", "empty reviewer response") for c in criteria], "", "empty response"
    candidate = raw
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fence:
        candidate = fence.group(1)
    else:
        m = _JSON_RE.search(raw)
        if m:
            candidate = m.group(0)
    try:
        data = json.loads(candidate)
    except Exception as exc:  # noqa: BLE001
        return (
            [CriterionVerdict(c, "unknown", "reviewer output was not valid JSON") for c in criteria],
            raw[:2000],
            f"json parse failed: {exc}",
        )
    items = data.get("criteria") if isinstance(data, dict) else None
    summary = str((data.get("summary") if isinstance(data, dict) else "") or "")
    verdicts: List[CriterionVerdict] = []
    listed = items if isinstance(items, list) else []
    for index, criterion in enumerate(criteria):
        match: Optional[Dict[str, Any]] = None
        # Prefer positional match; fall back to text match.
        if index < len(listed) and isinstance(listed[index], dict):
            match = listed[index]
        else:
            for item in listed:
                if isinstance(item, dict) and str(item.get("criterion") or "").strip().lower() == criterion.strip().lower():
                    match = item
                    break
        if match is None:
            verdicts.append(CriterionVerdict(criterion, "unknown", "reviewer did not address this criterion"))
            continue
        verdict = str(match.get("verdict") or "").strip().lower()
        if verdict not in VERDICTS:
            verdict = "unknown"
        verdicts.append(CriterionVerdict(criterion, verdict, str(match.get("evidence") or "")))
    return verdicts, summary, ""


def aggregate(verdicts: List[CriterionVerdict]) -> tuple[str, int, int, int]:
    met = sum(1 for v in verdicts if v.verdict == "pass")
    unmet = sum(1 for v in verdicts if v.verdict == "fail")
    unknown = sum(1 for v in verdicts if v.verdict == "unknown")
    if unmet:
        overall = "fail"
    elif unknown or not verdicts:
        overall = "unknown"
    else:
        overall = "pass"
    return overall, met, unmet, unknown


class AcceptanceReviewer:
    """Run one read-only reviewer session and persist the verdict.

    ``generate_fn(job, prompt, workspace) -> (text, meta)`` is injectable so
    tests (and a future non-agentic single-call mode) need no provider.
    The default builds a MuCLI session through ``build_session_fn`` with the
    job's provider/model, restricted to :data:`REVIEWER_TOOL_ALLOWLIST`.
    """

    def __init__(
        self,
        service: JobService,
        *,
        generate_fn: Optional[Callable[[Job, str, str], tuple[str, Dict[str, Any]]]] = None,
        evidence_root: Optional[str] = None,
    ):
        self.service = service
        self.generate_fn = generate_fn or self._agent_generate
        self.root = os.path.abspath(
            os.path.expanduser(evidence_root or os.path.join(HISTORY_DIR, "jobs", "evidence"))
        )

    # ------------------------------------------------------------ default generator

    def _agent_generate(self, job: Job, prompt: str, workspace: str) -> tuple[str, Dict[str, Any]]:
        import copy
        from types import SimpleNamespace

        import mucli as mucli_entry

        from .ui import JobUI
        from .worker import _base_args

        execution = dict(job.execution or {})
        args = copy.copy(_base_args(job))
        args.session = f"job-{job.id[:20]}-review"
        args.workspace = [workspace] if workspace else []
        args.yolo = False
        args.system = (
            "You are an independent acceptance reviewer for an engineering ticket. "
            "Read-only. Never modify files. Answer with the requested JSON only."
        )
        ui = JobUI(self.service, job.id)
        session = mucli_entry.build_session(args, ui, allow_prompt=False)
        session.ui = ui
        session.session_manager.ui = ui
        session.variables["provider_recovery_policy"] = "auto"
        session.variables["agent_mode"] = "default"
        session.variables["yolo"] = False
        session.variables["plan_mode"] = True  # belt: write-side tools blocked
        session.variables["max_iterations"] = int(execution.get("acceptance_review_max_iterations", 12) or 12)
        try:
            from mu.tools.descriptors import TOOLS

            session.disabled_tools = sorted(
                {t.name for t in TOOLS} - REVIEWER_TOOL_ALLOWLIST
            )
        except Exception:  # noqa: BLE001
            pass
        before = dict(getattr(session.session_manager, "token_counts", {}) or {})
        try:
            result = session.send_message(prompt) or {}
        finally:
            try:
                session.shutdown()
            except Exception:  # noqa: BLE001
                pass
        after = dict(getattr(session.session_manager, "token_counts", {}) or {})
        text = ""
        if isinstance(result, dict):
            text = str(result.get("assistant_text") or result.get("text") or "")
        if not text:
            # Fall back to the last assistant text part in history.
            for message in reversed(getattr(session.session_manager, "history", []) or []):
                if message.get("role") == "assistant":
                    parts = [p.get("text", "") for p in message.get("parts", []) if p.get("type") == "text"]
                    if parts:
                        text = "\n".join(parts)
                        break
        cost = float(after.get("total_cost", 0.0) or 0.0) - float(before.get("total_cost", 0.0) or 0.0)
        return text, {
            "provider": str(execution.get("provider") or ""),
            "model": str(execution.get("model") or ""),
            "cost_usd": max(0.0, cost),
        }

    # ------------------------------------------------------------ public API

    def review(self, job_id: str, *, head_sha: str = "") -> AcceptanceReview:
        from .review import JobReviewError, build_job_diff

        job = self.service.get(job_id)
        criteria = list(job.acceptance_criteria)
        if not criteria:
            review = AcceptanceReview(
                job_id=job.id, head_sha=head_sha, verdict="unknown",
                total=0, met=0, unmet=0, unknown=0,
                summary="No acceptance criteria were defined for this ticket.",
            )
            self._persist(job, review)
            return review
        try:
            diff = build_job_diff(self.service, job.id, max_chars=_MAX_DIFF_CHARS + 10_000)
            patch, stat, head = diff.patch, diff.stat, diff.head_sha
        except JobReviewError as exc:
            patch, stat, head = "", "", head_sha
            logger.warning("acceptance review: diff unavailable for %s: %s", job.id, exc)
        workspace = str(job.worktree or "")
        if not workspace or not os.path.isdir(workspace):
            workspace = str((job.metadata or {}).get("repository_root") or job.repository or "")
        prompt = build_review_prompt(job, patch, stat)
        meta: Dict[str, Any] = {}
        error = ""
        text = ""
        try:
            text, meta = self.generate_fn(job, prompt, workspace)
        except Exception as exc:  # noqa: BLE001 — reviewer failure is evidence, not a crash
            error = f"{type(exc).__name__}: {exc}"
            logger.warning("acceptance review failed for %s: %s", job.id, error)
        verdicts, summary, parse_error = parse_review_response(text, criteria)
        overall, met, unmet, unknown = aggregate(verdicts)
        if error:
            overall = "unknown"
        review = AcceptanceReview(
            job_id=job.id, head_sha=head or head_sha, verdict=overall,
            total=len(criteria), met=met, unmet=unmet, unknown=unknown,
            criteria=verdicts, summary=summary, model=str(meta.get("model") or ""),
            provider=str(meta.get("provider") or ""), cost_usd=float(meta.get("cost_usd") or 0.0),
            raw_response=str(text or "")[:20_000], error=error or parse_error,
        )
        self._persist(job, review)
        return review

    def _persist(self, job: Job, review: AcceptanceReview) -> None:
        payload = review.to_dict()
        try:
            job_dir = os.path.join(self.root, job.id)
            os.makedirs(job_dir, exist_ok=True)
            path = os.path.join(job_dir, "acceptance-review.json")
            tmp = f"{path}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({**payload, "raw_response": review.raw_response}, fh, indent=2, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
            payload["evidence_path"] = path
        except OSError as exc:
            logger.warning("acceptance review evidence write failed: %s", exc)
        self.service.store.append_event(
            job.id, "acceptance_review_completed",
            reason=f"verdict={review.verdict} met={review.met}/{review.total}",
            payload=payload,
        )
        try:
            current = self.service.get(job.id)
            metadata = dict(current.metadata or {})
            metadata["acceptance_review"] = payload
            self.service.store.update_runtime_fields(job.id, metadata_json=metadata)
        except Exception:  # noqa: BLE001
            logger.debug("acceptance review metadata mirror failed", exc_info=True)
        if review.cost_usd:
            try:
                current = self.service.get(job.id)
                self.service.store.update_runtime_fields(
                    job.id, cost_usd=float(current.cost_usd or 0.0) + float(review.cost_usd)
                )
            except Exception:  # noqa: BLE001
                pass


def acceptance_review_enabled(job: Job) -> bool:
    execution = dict(job.execution or {})
    value = execution.get("acceptance_review")
    if value is None:
        value = os.environ.get("MUCLI_JOBS_ACCEPTANCE_REVIEW", "")
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


__all__ = [
    "AcceptanceReview", "AcceptanceReviewer", "CriterionVerdict", "REVIEWER_TOOL_ALLOWLIST",
    "acceptance_review_enabled", "aggregate", "build_review_prompt", "parse_review_response",
]
