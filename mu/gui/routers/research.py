"""Research-mode introspection: the session's research ledger.

``GET /api/research/state`` returns the questions, claims, sources and
coverage of the requested session's ``ResearchState`` (never a registry shared
with another session), plus a claims × origins matrix and the bibliography.
``findings`` mirrors the claims in the shape earlier clients (Android) read.

Read-only — research state changes go through the chat path so the agent's
chain of evidence stays coherent.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Request

from ..mode_workspace import research_workspace
from ..mode_session import mode_session

router = APIRouter()
_logger = logging.getLogger(__name__)

CANDIDATE_LIMIT = 200


def _source_to_dict(state, source) -> Dict[str, Any]:
    return {
        "id": source.citation_id,
        "source_id": source.candidate_id,
        "state": source.state,
        "title": source.title,
        "url": source.url,
        "source_type": source.source_type.value,
        "origin": source.origin,
        "topic": state.group_label(source),
        "question_ids": list(source.question_ids),
        "authors": list(source.authors or []),
        "date": source.date,
        "site": source.site,
        "accessed_date": source.accessed_date,
        "snippet": source.snippet,
        "credibility_score": round(source.credibility_score, 2),
        "assessed": source.assessed,
        "metadata": source.metadata or {},
    }


def _claim_to_dict(state, claim) -> Dict[str, Any]:
    return {
        "id": claim.id,
        "question_id": claim.question_id,
        "text": claim.text,
        "kind": claim.kind,
        "confidence": state.claim_confidence(claim),
        "contested": state.claim_contested(claim),
        "origins": state.claim_origins(claim),
        "evidence": [ev.to_dict() for ev in claim.evidence],
    }


def _finding(claim: Dict[str, Any]) -> Dict[str, Any]:
    cites = "".join(
        f"[^{e['citation_id']}]" for e in claim["evidence"] if e["stance"] != "contradicts"
    )
    return {
        "id": claim["id"],
        "content": claim["text"],
        "tags": [claim["question_id"], claim["confidence"], claim["kind"]]
        + (["contested"] if claim["contested"] else []),
        "source": cites,
        "kind": claim["kind"],
        "record_type": "claim",
        "evidence_state": "source_linked",
        "created_at": 0,
        "updated_at": 0,
        "hits": 0,
    }


def research_state_payload(state, *, active: bool) -> Dict[str, Any]:
    from mu.research.report import matrix

    coverage = state.coverage()
    sources = [_source_to_dict(state, s) for s in state.numbered_sources()]
    candidates = [_source_to_dict(state, s) for s in state.all_sources("candidate")][:CANDIDATE_LIMIT]
    claims = [_claim_to_dict(state, c) for c in state.claims()]
    findings = [_finding(c) for c in claims]
    return {
        "active": True,
        "questions": coverage["questions"],
        "current_question": coverage["current_question"],
        "claims": claims,
        "coverage": {
            "sources": coverage["sources"],
            "claims": coverage["claims"],
            "open_without_claims": coverage["open_without_claims"],
        },
        "matrix": matrix(state),
        "sources": sources,
        "source_count": len(sources),
        "candidates": candidates,
        "candidate_count": coverage["sources"]["candidate"],
        "bibliography": state.compile_bibliography() if sources else "",
        "current_topic": state.get_current_topic(),
        "topics": state.list_topics(),
        "findings": findings,
        "finding_count": len(findings),
        "workspace": research_workspace(sources, claims, coverage["questions"], active=active),
    }


def _blockers(session) -> list:
    states = getattr(getattr(session, "session_manager", None), "mode_state", None)
    return [b.to_dict() for b in states.get("blockers").open_blockers()] if states is not None else []


def _empty_payload() -> Dict[str, Any]:
    return {
        "active": False,
        "questions": [],
        "current_question": None,
        "claims": [],
        "coverage": {"sources": {"candidate": 0, "read": 0, "cited": 0}, "claims": 0,
                     "open_without_claims": []},
        "matrix": {"origins": [], "rows": []},
        "sources": [],
        "source_count": 0,
        "candidates": [],
        "candidate_count": 0,
        "bibliography": "",
        "current_topic": "general",
        "topics": [],
        "findings": [],
        "finding_count": 0,
        "workspace": research_workspace([], [], [], active=False),
    }


@router.get("/state")
async def get_research_state(request: Request) -> Dict[str, Any]:
    from mu.research import ResearchState

    session = mode_session(request)
    sm = getattr(session, "session_manager", None)
    state = getattr(sm, "research", None)
    if session is None or not isinstance(state, ResearchState):
        return _empty_payload()
    mode_active = sm.variables.get("agent_mode", "default") == "research"
    try:
        with state.lock:
            payload = research_state_payload(state, active=mode_active)
        payload["blockers"] = _blockers(session)
        return payload
    except Exception as exc:  # noqa: BLE001 — never 500 the panel
        _logger.warning("research: state unavailable: %s", exc)
        return _empty_payload()


@router.get("/report")
async def get_research_report(request: Request) -> Dict[str, Any]:
    """The report skeleton (``research_report``) as markdown, for the expanded reading view."""
    from mu.research import ResearchState
    from mu.research.report import report_skeleton

    session = mode_session(request)
    state = getattr(getattr(session, "session_manager", None), "research", None)
    if not isinstance(state, ResearchState):
        return {"markdown": ""}
    with state.lock:
        return {"markdown": report_skeleton(state)}
