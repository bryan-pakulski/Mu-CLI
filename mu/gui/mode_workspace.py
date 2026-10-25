"""Typed presentation contracts for MuCLI's mode workspaces.

The agent modes deliberately share a harness, but the GUI must not pretend
that their outputs mean the same thing.  This module is the adapter between
mode-owned state (task memory, scratchpad, feature plans, security reports,
and courses) and a small shared piece of UI chrome.

The contract only contains *presentation metadata*.  The underlying mode
payload remains the source of truth and keeps its native shape.  In
particular, no synthetic "accuracy percentage" is generated: when a mode has
not actually measured accuracy or relevance, the contract says so.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence

SCHEMA_VERSION = 1


def _metric(
    metric_id: str,
    label: str,
    value: Any,
    *,
    tone: str = "neutral",
    detail: str = "",
) -> Dict[str, Any]:
    return {
        "id": metric_id,
        "label": label,
        "value": value,
        "tone": tone,
        "detail": detail,
    }


def _view(view_id: str, label: str, count: Optional[int] = None) -> Dict[str, Any]:
    item: Dict[str, Any] = {"id": view_id, "label": label}
    if count is not None:
        item["count"] = count
    return item


def _quality(
    quality_id: str,
    label: str,
    state: str,
    detail: str,
    *,
    value: Optional[Any] = None,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "id": quality_id,
        "label": label,
        "state": state,
        "detail": detail,
    }
    if value is not None:
        result["value"] = value
    return result


def _workspace(
    mode: str,
    title: str,
    objective: str,
    *,
    status_label: str,
    status_tone: str,
    views: Sequence[Dict[str, Any]],
    metrics: Sequence[Dict[str, Any]],
    quality: Sequence[Dict[str, Any]],
    search_placeholder: str,
    provenance: str,
) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
        "title": title,
        "objective": objective,
        "status": {"label": status_label, "tone": status_tone},
        "views": list(views),
        "metrics": list(metrics),
        "quality": list(quality),
        "search_placeholder": search_placeholder,
        "provenance": provenance,
    }


def research_workspace(
    sources: Sequence[Mapping[str, Any]],
    claims: Sequence[Mapping[str, Any]],
    questions: Sequence[Mapping[str, Any]] = (),
    *,
    active: bool = True,
) -> Dict[str, Any]:
    """Research ledger: questions → claims → quoted evidence → read sources.

    ``sources`` are read/cited sources, ``claims`` carry their computed
    ``confidence`` and ``contested`` flags, ``questions`` their ``status``.
    Confidence measures corroboration by independent origins, not truth, so
    claim accuracy stays "unassessed".
    """
    cited = sum(1 for s in sources if s.get("state") == "cited")
    origins = len({str(s.get("origin") or "") for s in sources if s.get("origin")})
    high = sum(1 for c in claims if c.get("confidence") == "high")
    contested = sum(1 for c in claims if c.get("contested"))
    live = [q for q in questions if q.get("status") != "dropped"]
    answered = sum(1 for q in live if q.get("status") == "answered")
    gaps = sum(1 for q in live if not q.get("claims"))
    assessed = sum(1 for s in sources if s.get("assessed"))
    return _workspace(
        "research",
        "Evidence desk",
        "Plan questions, read sources, record claims with quoted evidence, and see where the "
        "evidence is thin or contested. Confidence counts independent origins; it is not a fact-check.",
        status_label="collecting" if active else "idle",
        status_tone="active" if active else "neutral",
        views=(
            _view("overview", "Brief"),
            _view("questions", "Questions", len(live)),
            _view("sources", "Sources", len(sources)),
            _view("bibliography", "Citations", cited),
        ),
        metrics=(
            _metric("questions", "answered", f"{answered}/{len(live)}",
                    tone="good" if live and answered == len(live) else "neutral",
                    detail="Answered: every claim is at medium confidence or better."),
            _metric("claims", "claims", len(claims),
                    detail="Findings recorded with excerpts quoted from sources that were read."),
            _metric("high", "high conf.", high, tone="good" if high else "neutral",
                    detail="Two or more independent origins, one credible, no contradictions."),
            _metric("contested", "contested", contested, tone="risk" if contested else "neutral",
                    detail="Claims with supporting and contradicting evidence from different origins."),
            _metric("gaps", "gaps", gaps, tone="risk" if gaps else "neutral",
                    detail="Open questions nothing has been recorded for yet."),
            _metric("sources", "sources read", len(sources),
                    detail=f"{cited} cited · {origins} independent origin(s) · {assessed} AI-assessed."),
        ),
        quality=(
            _quality(
                "accuracy",
                "Claim accuracy",
                "unassessed",
                "MuCLI checks that each excerpt occurs in the source it cites, not that the source is right.",
            ),
            _quality(
                "relevance",
                "Relevance",
                "question-linked",
                "Every claim answers a recorded question; questions without claims are listed as gaps.",
            ),
            _quality(
                "evidence",
                "Evidence quality",
                "corroboration-graded",
                "Claim confidence counts independent source origins and their AI-assessed credibility, "
                "bounded by a per-type cap.",
            ),
        ),
        search_placeholder="Filter questions, claims, sources, authors or origins",
        provenance="Session research ledger: questions, claims, evidence and read sources",
    )


def security_workspace(
    report: Optional[Mapping[str, Any]],
    findings: Sequence[Mapping[str, Any]],
    *,
    active: bool = True,
) -> Dict[str, Any]:
    proof_verified = sum(1 for finding in findings if finding.get("proof_verified"))
    fixes_verified = sum(
        1 for finding in findings if finding.get("remediation_verified")
    )
    critical_high = sum(
        1
        for finding in findings
        if str(finding.get("severity") or "").lower() in {"critical", "high"}
    )
    approved = sum(1 for finding in findings if finding.get("status") == "approved")
    title = str((report or {}).get("title") or "Verification bench")
    report_status = str((report or {}).get("status") or ("ready" if active else "idle"))
    return _workspace(
        "security",
        title,
        "Prioritise risk, inspect the exploit chain, and gate conclusions on reproducible proof and verified remediation.",
        status_label=report_status,
        status_tone="risk" if critical_high else ("active" if active else "neutral"),
        views=(
            _view("overview", "Risk"),
            _view("findings", "Findings", len(findings)),
            _view("evidence", "Proof", proof_verified),
            _view("remediation", "Fixes", fixes_verified),
        ),
        metrics=(
            _metric(
                "risk",
                "critical / high",
                critical_high,
                tone="risk" if critical_high else "good",
                detail="Findings requiring the fastest attention.",
            ),
            _metric(
                "proof",
                "proof verified",
                proof_verified,
                tone="good" if proof_verified else "neutral",
                detail="Findings with a reproducible verified proof.",
            ),
            _metric(
                "fixes",
                "fixes verified",
                fixes_verified,
                tone="good" if fixes_verified else "neutral",
                detail="Remediations with verification evidence.",
            ),
            _metric(
                "approved",
                "approved",
                approved,
                tone="good" if approved else "neutral",
                detail="Findings that passed the mode's approval gate.",
            ),
        ),
        quality=(
            _quality(
                "accuracy",
                "Finding accuracy",
                "verification-gated",
                "Approval requires both verified exploit proof and verified remediation.",
            ),
            _quality(
                "relevance",
                "Affected surface",
                "traceable",
                "Paths and exploit paths show where each finding applies; no generic relevance score is invented.",
            ),
            _quality(
                "evidence",
                "Evidence chain",
                "measured",
                "Proof and remediation verification are independently visible.",
            ),
        ),
        search_placeholder="Filter risks, paths, classes, or evidence",
        provenance="Security report, proof commands, and remediation records",
    )
