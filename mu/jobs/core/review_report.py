"""Report-only review model (guide §4, decision D1).

The reviewer writes findings; the host computes verdict and merge readiness
from findings, AC coverage and check results. The model's own verdict is
advisory only and never routes the job.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional

SEVERITIES = ("blocker", "major", "minor", "nit")
CATEGORIES = ("correctness", "security", "regression", "tests", "perf", "maintainability", "scope")
DISPOSITIONS = ("open", "accepted", "dismissed", "fixed")
COVERAGE = ("met", "partial", "unmet", "unverifiable")
VERDICTS = ("ready", "ready_with_nits", "needs_work", "do_not_merge")

# Readiness score is for sorting only (§12 item 3); verdict rules are authoritative.
_PENALTY = {"blocker": 40, "major": 15, "minor": 4, "nit": 1}


def _pick(value: Any, allowed: Iterable[str], default: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in allowed else default


def _int(value: Any) -> Optional[int]:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


@dataclass
class Finding:
    id: str
    severity: str = "minor"
    category: str = "correctness"
    file: Optional[str] = None
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    title: str = ""
    detail: str = ""
    suggestion: str = ""
    confidence: str = "medium"
    disposition: str = "open"

    @classmethod
    def from_dict(cls, raw: Dict[str, Any], index: int) -> "Finding":
        return cls(
            id=str(raw.get("id") or f"F-{index}"),
            severity=_pick(raw.get("severity"), SEVERITIES, "minor"),
            category=_pick(raw.get("category"), CATEGORIES, "correctness"),
            file=(str(raw["file"]) if raw.get("file") else None),
            line_start=_int(raw.get("line_start")),
            line_end=_int(raw.get("line_end")),
            title=str(raw.get("title") or "")[:300],
            detail=str(raw.get("detail") or "")[:4000],
            suggestion=str(raw.get("suggestion") or "")[:4000],
            confidence=_pick(raw.get("confidence"), ("high", "medium", "low"), "medium"),
            disposition=_pick(raw.get("disposition"), DISPOSITIONS, "open"),
        )


@dataclass
class Coverage:
    criterion_id: str
    status: str = "unverifiable"
    evidence: str = ""


@dataclass
class ReviewReport:
    job_id: str
    version: int = 1
    reviewed_head: str = ""
    model: Dict[str, str] = field(default_factory=dict)
    summary: str = ""
    plan_coverage: List[Coverage] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)
    checks_context: Dict[str, int] = field(default_factory=lambda: {"new_failures": 0, "preexisting_failures": 0})
    out_of_scope_changes: List[str] = field(default_factory=list)
    model_verdict: str = ""  # advisory only
    verdict: str = "ready"
    merge_readiness: int = 100
    cost_usd: float = 0.0

    # ---------------------------------------------------------- host scoring
    def recompute(self) -> "ReviewReport":
        """Apply §4.3 rules. Call after any disposition/check change."""
        self.verdict = compute_verdict(self.findings, self.plan_coverage, self.checks_context)
        self.merge_readiness = compute_readiness(self.findings, self.plan_coverage, self.checks_context)
        return self

    def counts(self) -> Dict[str, int]:
        out = {s: 0 for s in SEVERITIES}
        for f in self.findings:
            if f.disposition == "open":
                out[f.severity] += 1
        return out

    def set_disposition(self, ids: Iterable[str], disposition: str) -> List[str]:
        disposition = _pick(disposition, DISPOSITIONS, "")
        if not disposition:
            raise ValueError("disposition must be open/accepted/dismissed/fixed")
        wanted = {str(i) for i in ids}
        changed = []
        for f in self.findings:
            if f.id in wanted:
                f.disposition = disposition
                changed.append(f.id)
        unknown = wanted - set(changed)
        if unknown:
            raise ValueError(f"unknown finding ids: {sorted(unknown)}")
        self.recompute()
        return changed

    def select(self, ids: Iterable[str]) -> List[Finding]:
        wanted = {str(i) for i in ids}
        return [f for f in self.findings if f.id in wanted]

    # ------------------------------------------------------------- serialize
    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["counts"] = self.counts()
        return value

    @classmethod
    def from_dict(cls, raw: Dict[str, Any], *, job_id: str = "") -> "ReviewReport":
        raw = dict(raw or {})
        report = cls(
            job_id=str(raw.get("job_id") or job_id),
            version=int(raw.get("version") or 1),
            reviewed_head=str(raw.get("reviewed_head") or raw.get("head_sha") or ""),
            model={k: str(v) for k, v in dict(raw.get("model") or {}).items()},
            summary=str(raw.get("summary") or "")[:6000],
            plan_coverage=[
                Coverage(str(c.get("criterion_id") or f"AC-{i}"),
                         _pick(c.get("status"), COVERAGE, "unverifiable"),
                         str(c.get("evidence") or ""))
                for i, c in enumerate(raw.get("plan_coverage") or [], 1) if isinstance(c, dict)
            ],
            findings=[Finding.from_dict(f, i) for i, f in enumerate(raw.get("findings") or [], 1)
                      if isinstance(f, dict)],
            checks_context={
                "new_failures": int((raw.get("checks_context") or {}).get("new_failures") or 0),
                "preexisting_failures": int((raw.get("checks_context") or {}).get("preexisting_failures") or 0),
                # Present only for unverified check runs (unrunnable checks; verdict capped).
                **({"unverified": int((raw.get("checks_context") or {})["unverified"])}
                   if int((raw.get("checks_context") or {}).get("unverified") or 0) else {}),
            },
            out_of_scope_changes=[str(x) for x in raw.get("out_of_scope_changes") or []],
            model_verdict=str(raw.get("model_verdict") or raw.get("verdict") or ""),
            cost_usd=float(raw.get("cost_usd") or 0.0),
        )
        return report.recompute()

    @classmethod
    def from_legacy(cls, review: Dict[str, Any], *, job_id: str = "") -> "ReviewReport":
        """Old ``{verdict: approve|request_changes, summary, feedback[]}`` rounds.

        Feedback items carry no anchors (``file: None``); a request_changes
        item becomes a ``major`` finding so the host verdict is ``needs_work``.
        """
        review = dict(review or {})
        if review.get("findings") is not None:
            return cls.from_dict(review, job_id=job_id)
        legacy = str(review.get("verdict") or "").lower()
        severity = "major" if legacy == "request_changes" else "minor"
        findings = [
            {"id": f"F-{i}", "severity": severity, "title": str(item)[:120], "detail": str(item)}
            for i, item in enumerate(review.get("feedback") or [], 1) if str(item).strip()
        ]
        return cls.from_dict({
            "job_id": job_id,
            "reviewed_head": review.get("head_sha") or "",
            "model": review.get("model") or {},
            "summary": review.get("summary") or "",
            "findings": findings,
            "verdict": legacy,
            "cost_usd": review.get("cost_usd") or 0.0,
        }, job_id=job_id)


def compute_verdict(findings: Iterable[Finding], coverage: Iterable[Coverage],
                    checks: Optional[Dict[str, int]] = None) -> str:
    open_sev = {f.severity for f in findings if f.disposition == "open"}
    if "blocker" in open_sev or int((checks or {}).get("new_failures") or 0) > 0:
        return "do_not_merge"
    if "major" in open_sev or any(c.status == "unmet" for c in coverage):
        return "needs_work"
    if int((checks or {}).get("unverified") or 0) > 0:
        # Honest gate (job_templates.md §5): checks that could not run never yield "ready".
        return "needs_work"
    if open_sev & {"minor", "nit"}:
        return "ready_with_nits"
    return "ready"


def compute_readiness(findings: Iterable[Finding], coverage: Iterable[Coverage],
                      checks: Optional[Dict[str, int]] = None) -> int:
    score = 100
    for f in findings:
        if f.disposition == "open":
            score -= _PENALTY.get(f.severity, 0)
    for c in coverage:
        score -= {"unmet": 15, "partial": 5}.get(c.status, 0)
    score -= 25 * int((checks or {}).get("new_failures") or 0)
    if int((checks or {}).get("unverified") or 0) > 0:
        score = min(score - 20, 60)
    return max(0, min(100, score))
