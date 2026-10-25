"""Evidence ledger: questions → claims → evidence → sources.

The agent records claims through tools; this module holds the records and the
deterministic rules that grade them. Origins (independence groups), not URLs,
are counted, so three pages on one site support a claim once.

Confidence of a claim:

* ``high``   — ≥ 2 supporting origins, one of them with credibility ≥ 0.7,
  and no contradicting evidence.
* ``medium`` — ≥ 2 supporting origins, or 1 origin with credibility ≥ 0.8;
  contradictions allowed only while outnumbered by supporting origins.
* ``low``    — otherwise. ``kind="inferred"`` caps at ``medium``; the agent may
  lower a claim's confidence (``confidence_cap``) but never raise it.

A question is ``contested`` when any claim has supporting and contradicting
evidence from different origins, ``answered`` when it has claims and every one
is at least ``medium``, otherwise ``open``. ``dropped`` is set by the agent.
The claims judged are the question's own and its live sub-questions'.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

QUESTION_STATUSES = ("open", "answered", "contested", "dropped")
CLAIM_KINDS = ("observed", "derived", "inferred")
STANCES = ("supports", "contradicts", "qualifies")
CONFIDENCE_LEVELS = ("low", "medium", "high")
EXCERPT_MAX_CHARS = 500

HIGH_ORIGIN_CREDIBILITY = 0.7
SINGLE_ORIGIN_CREDIBILITY = 0.8


@dataclass
class Question:
    id: str
    text: str
    parent_id: Optional[str] = None
    dropped: bool = False
    todo_id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "text": self.text, "parent_id": self.parent_id,
                "dropped": self.dropped, "todo_id": self.todo_id}

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Question":
        todo = raw.get("todo_id")
        return cls(
            id=str(raw["id"]),
            text=str(raw.get("text") or "").strip() or str(raw["id"]),
            parent_id=(str(raw["parent_id"]) if raw.get("parent_id") else None),
            dropped=bool(raw.get("dropped")),
            todo_id=int(todo) if isinstance(todo, (int, float)) or str(todo or "").isdigit() else None,
        )


@dataclass
class Evidence:
    citation_id: int
    stance: str
    excerpt: str
    locator: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"citation_id": self.citation_id, "stance": self.stance,
                "excerpt": self.excerpt, "locator": self.locator}

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Evidence":
        stance = str(raw.get("stance") or "supports")
        return cls(
            citation_id=int(raw["citation_id"]),
            stance=stance if stance in STANCES else "supports",
            excerpt=str(raw.get("excerpt") or "")[:EXCERPT_MAX_CHARS],
            locator=(str(raw["locator"]) if raw.get("locator") else None),
        )


@dataclass
class Claim:
    id: str
    question_id: str
    text: str
    kind: str = "observed"
    evidence: List[Evidence] = field(default_factory=list)
    confidence_cap: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "question_id": self.question_id, "text": self.text,
                "kind": self.kind, "evidence": [e.to_dict() for e in self.evidence],
                "confidence_cap": self.confidence_cap}

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Claim":
        kind = str(raw.get("kind") or "observed")
        cap = raw.get("confidence_cap")
        evidence = []
        for item in raw.get("evidence") or []:
            try:
                evidence.append(Evidence.from_dict(item))
            except (KeyError, TypeError, ValueError):
                continue
        return cls(
            id=str(raw["id"]),
            question_id=str(raw["question_id"]),
            text=str(raw.get("text") or ""),
            kind=kind if kind in CLAIM_KINDS else "observed",
            evidence=evidence,
            confidence_cap=cap if cap in CONFIDENCE_LEVELS else None,
        )


# ------------------------------------------------------------------ rules

# ``origin_of(citation_id) -> (origin, credibility)`` lets the rules stay free
# of the source model.
OriginLookup = Callable[[int], Optional[tuple]]


def _origins(claim: Claim, stance: str, origin_of: OriginLookup) -> Dict[str, float]:
    """Distinct origins backing ``stance`` → best credibility among them."""
    out: Dict[str, float] = {}
    for ev in claim.evidence:
        if ev.stance != stance:
            continue
        found = origin_of(ev.citation_id)
        if not found:
            continue
        origin, credibility = found
        out[origin] = max(out.get(origin, 0.0), float(credibility or 0.0))
    return out


def _lower(a: str, b: Optional[str]) -> str:
    if b is None:
        return a
    return a if CONFIDENCE_LEVELS.index(a) <= CONFIDENCE_LEVELS.index(b) else b


def claim_confidence(claim: Claim, origin_of: OriginLookup) -> str:
    supports = _origins(claim, "supports", origin_of)
    contradicts = _origins(claim, "contradicts", origin_of)
    n = len(supports)
    if n >= 2 and not contradicts and max(supports.values()) >= HIGH_ORIGIN_CREDIBILITY:
        level = "high"
    elif (n >= 2 or (n == 1 and max(supports.values()) >= SINGLE_ORIGIN_CREDIBILITY)) and (
        len(contradicts) < n
    ):
        level = "medium"
    else:
        level = "low"
    if claim.kind == "inferred":
        level = _lower(level, "medium")
    return _lower(level, claim.confidence_cap)


def claim_contested(claim: Claim, origin_of: OriginLookup) -> bool:
    supports = set(_origins(claim, "supports", origin_of))
    contradicts = set(_origins(claim, "contradicts", origin_of))
    return bool(supports and contradicts and (supports | contradicts) != (supports & contradicts))


def question_status(question: Question, claims: List[Claim], origin_of: OriginLookup) -> str:
    if question.dropped:
        return "dropped"
    if any(claim_contested(c, origin_of) for c in claims):
        return "contested"
    if claims and all(claim_confidence(c, origin_of) != "low" for c in claims):
        return "answered"
    return "open"


def claim_origins(claim: Claim, origin_of: OriginLookup) -> Dict[str, List[str]]:
    return {
        stance: sorted(_origins(claim, stance, origin_of))
        for stance in STANCES
    }
