"""Research sources and the per-session ``ResearchState``.

A source moves through three states:

* ``candidate`` — a search result row. Deduped, carries its snippet and
  metadata, addressed as ``s<n>``; never numbered, never in the bibliography.
* ``read``      — the agent fetched it (``url_grounding``, ``research_fetch``,
  ``read_document``). It gets a stable citation number ``[^n]`` and its
  extracted text is kept (``texts``) for excerpt checks.
* ``cited``     — evidence in a recorded claim rests on it.

Dedup key is the canonical URL (``urls.canonical_url``); a second check on the
extracted text's hash merges mirrors into the earlier source, recording the
alias URL. ``origin`` groups sources that are not independent (registrable
domain by default; ``assess_source(origin=...)`` overrides it).

Credibility is AI-assessed: a read source starts at a conservative prior (half
its type's cap) until ``assess_source`` applies the model's 0–1 rating, which
the per-type cap bounds but never assigns.

Each ``SessionManager`` owns one ``ResearchState`` (``SessionManager.research``).
Tool dispatch binds it for the call (``use_research_state``) so code with no
session handle — ``get_citation_manager()`` — resolves the running session's
state, falling back to a private default for tests and CLI one-offs. Two
concurrent sessions therefore never share live state. All mutations take the
state's lock: research tools may run in parallel within one turn.
"""

from __future__ import annotations

import contextlib
import contextvars
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Iterator, List, Optional, Sequence

from . import ledger, texts
from .ledger import Claim, Evidence, Question
from .urls import canonical_url, content_hash, origin as url_origin

DEFAULT_TOPIC = "general"
SOURCE_STATES = ("candidate", "read", "cited")
STATE_VERSION = 2


class SourceType(Enum):
    """Types of sources that can be cited."""
    WEB = "web"
    ACADEMIC = "academic"
    SOCIAL = "social"
    FORUM = "forum"
    NEWS = "news"
    DOCUMENTATION = "documentation"
    OTHER = "other"


# Hard safety caps per source type — these BOUND the AI's assessment,
# they do not assign a flat rating.
SOURCE_TYPE_CAPS: Dict[SourceType, float] = {
    SourceType.ACADEMIC: 1.0,
    SourceType.DOCUMENTATION: 0.95,
    SourceType.NEWS: 0.85,
    SourceType.WEB: 0.80,
    SourceType.FORUM: 0.65,
    SourceType.SOCIAL: 0.60,
    SourceType.OTHER: 0.60,
}


def coerce_source_type(value: Any) -> SourceType:
    if isinstance(value, SourceType):
        return value
    try:
        return SourceType(str(value or "other").lower())
    except ValueError:
        return SourceType.OTHER


def source_type_cap(source_type: Any) -> float:
    """Return the hard safety cap for a source type (0.0-1.0)."""
    return SOURCE_TYPE_CAPS.get(coerce_source_type(source_type), 0.60)


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


@dataclass
class Source:
    sid: int
    url: str
    title: str
    source_type: SourceType = SourceType.WEB
    canonical_url: str = ""
    state: str = "candidate"
    citation_id: Optional[int] = None
    authors: List[str] = field(default_factory=list)
    date: Optional[str] = None
    site: Optional[str] = None
    accessed_date: str = field(default_factory=_today)
    snippet: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    credibility_score: float = 0.0
    assessed: bool = False
    origin: str = ""
    question_ids: List[str] = field(default_factory=list)
    topic: str = DEFAULT_TOPIC
    content_hash: Optional[str] = None
    text_key: Optional[str] = None
    result_ref: Optional[str] = None
    aliases: List[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.url:
            raise ValueError("Source URL is required")
        self.title = (self.title or "").strip() or self.url
        self.source_type = coerce_source_type(self.source_type)
        if not self.canonical_url:
            self.canonical_url = canonical_url(self.url) or self.url
        if not self.origin:
            self.origin = url_origin(self.url) or self.canonical_url

    @property
    def candidate_id(self) -> str:
        return f"s{self.sid}"

    @property
    def ref(self) -> str:
        return f"[^{self.citation_id}]" if self.citation_id else self.candidate_id

    def text(self) -> Optional[str]:
        return texts.get(self.text_key)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sid": self.sid,
            "citation_id": self.citation_id,
            "state": self.state,
            "title": self.title,
            "url": self.url,
            "canonical_url": self.canonical_url,
            "source_type": self.source_type.value,
            "authors": list(self.authors),
            "date": self.date,
            "site": self.site,
            "accessed_date": self.accessed_date,
            "snippet": self.snippet,
            "metadata": dict(self.metadata),
            "credibility_score": self.credibility_score,
            "assessed": self.assessed,
            "origin": self.origin,
            "question_ids": list(self.question_ids),
            "topic": self.topic,
            "content_hash": self.content_hash,
            "text_key": self.text_key,
            "result_ref": self.result_ref,
            "aliases": list(self.aliases),
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Source":
        state = str(raw.get("state") or "candidate")
        cid = raw.get("citation_id")
        return cls(
            sid=int(raw["sid"]),
            url=str(raw.get("url") or "").strip(),
            title=str(raw.get("title") or ""),
            source_type=coerce_source_type(raw.get("source_type")),
            canonical_url=str(raw.get("canonical_url") or ""),
            state=state if state in SOURCE_STATES else "candidate",
            citation_id=int(cid) if cid else None,
            authors=[str(a) for a in (raw.get("authors") or []) if a],
            date=raw.get("date") or None,
            site=raw.get("site") or None,
            accessed_date=str(raw.get("accessed_date") or _today()),
            snippet=str(raw.get("snippet") or ""),
            metadata=dict(raw.get("metadata") or {}),
            credibility_score=float(raw.get("credibility_score") or 0.0),
            assessed=bool(raw.get("assessed")),
            origin=str(raw.get("origin") or ""),
            question_ids=[str(q) for q in (raw.get("question_ids") or [])],
            topic=str(raw.get("topic") or DEFAULT_TOPIC),
            content_hash=raw.get("content_hash") or None,
            text_key=raw.get("text_key") or None,
            result_ref=raw.get("result_ref") or None,
            aliases=[str(a) for a in (raw.get("aliases") or [])],
        )


class ResearchError(ValueError):
    """A ledger operation was refused; the message is shown to the agent."""


_REF = re.compile(r"^\s*(?:\[\^?(\d+)\]|#?(\d+)|[sS](\d+))\s*$")


class ResearchState:
    """One session's sources, questions and claims."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.clear()

    # ------------------------------------------------------------- basics

    def clear(self) -> None:
        with self._lock:
            self._sources: Dict[int, Source] = {}
            self._by_canonical: Dict[str, int] = {}
            self._by_hash: Dict[str, int] = {}
            self._by_cid: Dict[int, int] = {}
            self._next_sid = 1
            self._next_cid = 1
            self._questions: Dict[str, Question] = {}
            self._claims: Dict[str, Claim] = {}
            self._next_claim = 1
            self._current_question: Optional[str] = None
            self._current_topic = DEFAULT_TOPIC

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    def _index(self, source: Source) -> None:
        self._sources[source.sid] = source
        self._by_canonical.setdefault(source.canonical_url, source.sid)
        for alias in source.aliases:
            self._by_canonical.setdefault(canonical_url(alias) or alias, source.sid)
        if source.content_hash:
            self._by_hash.setdefault(source.content_hash, source.sid)
        if source.citation_id:
            self._by_cid[source.citation_id] = source.sid
            self._next_cid = max(self._next_cid, source.citation_id + 1)
        self._next_sid = max(self._next_sid, source.sid + 1)

    # ------------------------------------------------------------ lookups

    def all_sources(self, state: Optional[str] = None) -> List[Source]:
        with self._lock:
            out = sorted(self._sources.values(), key=lambda s: s.sid)
        return [s for s in out if state is None or s.state == state]

    def numbered_sources(self) -> List[Source]:
        """Read and cited sources in citation-number order."""
        return sorted((s for s in self.all_sources() if s.citation_id),
                      key=lambda s: s.citation_id)

    def get_source(self, citation_id: int) -> Optional[Source]:
        with self._lock:
            sid = self._by_cid.get(int(citation_id))
            return self._sources.get(sid) if sid else None

    def find(self, ref: Any) -> Optional[Source]:
        """Resolve ``3``, ``"[^3]"``, ``"#3"`` (citation), ``"s12"`` (candidate) or a URL."""
        with self._lock:
            if isinstance(ref, int):
                return self.get_source(ref)
            text = str(ref or "").strip()
            m = _REF.match(text)
            if m:
                if m.group(3):
                    return self._sources.get(int(m.group(3)))
                return self.get_source(int(m.group(1) or m.group(2)))
            if text:
                sid = self._by_canonical.get(canonical_url(text) or text)
                return self._sources.get(sid) if sid else None
        return None

    @property
    def source_count(self) -> int:
        return len(self.numbered_sources())

    def candidate_count(self) -> int:
        return len(self.all_sources("candidate"))

    # ------------------------------------------------------------- topics

    def set_topic(self, topic: str) -> str:
        """Select (or create) the root question named ``topic``.

        Kept as the short form of ``research_question(action="add")``: sources
        read afterwards are filed under that question.
        """
        topic = (topic or "").strip() or DEFAULT_TOPIC
        with self._lock:
            self._current_topic = topic
            if topic == DEFAULT_TOPIC:
                self._current_question = None
                return topic
            for q in self._questions.values():
                if q.parent_id is None and q.text.lower() == topic.lower():
                    self._current_question = q.id
                    return topic
            self.add_question(topic)
        return topic

    def get_current_topic(self) -> str:
        with self._lock:
            q = self._questions.get(self._current_question or "")
            return q.text if q else self._current_topic

    def current_question(self) -> Optional[Question]:
        with self._lock:
            return self._questions.get(self._current_question or "")

    def group_label(self, source: Source) -> str:
        """The root question a source was filed under, else its legacy topic."""
        with self._lock:
            for qid in source.question_ids:
                root = self._root_question(qid)
                if root is not None:
                    return root.text
        return source.topic or DEFAULT_TOPIC

    def list_topics(self) -> List[str]:
        seen: List[str] = []
        for s in self.numbered_sources():
            label = self.group_label(s)
            if label not in seen:
                seen.append(label)
        return seen

    def _file_under_current(self, source: Source) -> None:
        if self._current_question and self._current_question not in source.question_ids:
            source.question_ids.append(self._current_question)
        if source.topic == DEFAULT_TOPIC and self._current_topic != DEFAULT_TOPIC:
            source.topic = self._current_topic

    # ----------------------------------------------------------- sources

    def add_candidate(
        self,
        *,
        url: str,
        title: str = "",
        source_type: Any = SourceType.WEB,
        snippet: str = "",
        authors: Optional[Sequence[str]] = None,
        date: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Source]:
        """Register a search result row; returns the (possibly existing) source."""
        url = (url or "").strip()
        if not url:
            return None
        key = canonical_url(url) or url
        with self._lock:
            sid = self._by_canonical.get(key)
            if sid is not None:
                existing = self._sources[sid]
                if not existing.snippet and snippet:
                    existing.snippet = snippet[:500]
                if not existing.authors and authors:
                    existing.authors = [str(a) for a in authors if a]
                if not existing.date and date:
                    existing.date = str(date)
                self._file_under_current(existing)
                return existing
            source = Source(
                sid=self._next_sid,
                url=url,
                title=title,
                source_type=source_type,
                canonical_url=key,
                snippet=(snippet or "")[:500],
                authors=[str(a) for a in (authors or []) if a],
                date=str(date) if date else None,
                metadata=dict(metadata or {}),
            )
            self._file_under_current(source)
            self._index(source)
            return source

    def mark_read(
        self,
        *,
        url: str,
        title: str = "",
        source_type: Any = SourceType.WEB,
        text: str = "",
        html_canonical: Optional[str] = None,
        authors: Optional[Sequence[str]] = None,
        date: Optional[str] = None,
        site: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        result_ref: Optional[str] = None,
    ) -> Source:
        """Promote (or create) the source for ``url`` to ``read``.

        A candidate keeps its ID and gains a citation number; metadata from the
        search (authors, date) is inherited unless the page supplies better.
        A page whose text matches an already-read source is merged into it.
        """
        url = (url or "").strip()
        if not url:
            raise ResearchError("url is required")
        key = canonical_url(url, html_canonical) or url
        digest = content_hash(text)
        tkey = texts.put(text) if text else None
        with self._lock:
            sid = self._by_canonical.get(key)
            if sid is None:
                sid = self._by_canonical.get(canonical_url(url) or url)
            if sid is None and digest:
                sid = self._by_hash.get(digest)
                if sid is not None:
                    mirror = self._sources[sid]
                    if url not in mirror.aliases and url != mirror.url:
                        mirror.aliases.append(url)
                    self._by_canonical.setdefault(key, sid)
            if sid is None:
                source = Source(sid=self._next_sid, url=url, title=title,
                                source_type=source_type, canonical_url=key)
            else:
                source = self._sources[sid]
                if source.canonical_url != key and key not in self._by_canonical:
                    self._by_canonical[key] = sid
            if title and (source.title == source.url or not source.title):
                source.title = title.strip()
            elif title and source.state == "candidate":
                source.title = title.strip()
            if authors:
                source.authors = [str(a) for a in authors if a]
            if date:
                source.date = str(date)
            if site:
                source.site = site
            if metadata:
                source.metadata.update(metadata)
            if tkey:
                source.text_key = tkey
                source.content_hash = digest or source.content_hash
            if result_ref:
                source.result_ref = result_ref
            source.accessed_date = _today()
            if source.state == "candidate":
                source.state = "read"
                if source.source_type == SourceType.OTHER or sid is None:
                    source.source_type = coerce_source_type(source_type)
            if not source.citation_id:
                source.citation_id = self._next_cid
                self._next_cid += 1
            if not source.assessed and source.credibility_score == 0.0:
                source.credibility_score = round(source_type_cap(source.source_type) / 2.0, 3)
            self._file_under_current(source)
            self._index(source)
            return source

    def assess_source(self, ref: Any, importance: float, rationale: str = "",
                      origin: Optional[str] = None) -> Source:
        """Apply the model's evidence assessment, bounded by the type's cap."""
        with self._lock:
            source = self.find(ref)
            if source is None or not source.citation_id:
                raise ResearchError(
                    f"No read source {ref!r}. Read it first (url_grounding / research_fetch); "
                    "candidates from search results cannot be assessed."
                )
            requested = max(0.0, min(1.0, float(importance)))
            cap = source_type_cap(source.source_type)
            source.credibility_score = min(requested, cap)
            source.assessed = True
            source.metadata["model_importance"] = requested
            source.metadata["credibility_cap"] = cap
            if rationale.strip():
                source.metadata["assessment_rationale"] = rationale.strip()[:1000]
            if origin and origin.strip():
                source.origin = origin.strip().lower()[:200]
            return source

    # ---------------------------------------------------------- questions

    def questions(self) -> List[Question]:
        with self._lock:
            return list(self._questions.values())

    def get_question(self, qid: str) -> Optional[Question]:
        with self._lock:
            return self._questions.get(str(qid or "").strip())

    def _root_question(self, qid: str) -> Optional[Question]:
        q = self._questions.get(qid)
        seen = set()
        while q is not None and q.parent_id and q.id not in seen:
            seen.add(q.id)
            parent = self._questions.get(q.parent_id)
            if parent is None:
                break
            q = parent
        return q

    def add_question(self, text: str, parent_id: Optional[str] = None,
                     todo_id: Optional[int] = None, select: bool = True) -> Question:
        text = (text or "").strip()
        if not text:
            raise ResearchError("question text is required")
        with self._lock:
            if parent_id:
                parent = self._questions.get(parent_id)
                if parent is None:
                    raise ResearchError(f"No question {parent_id!r}")
                n = 1 + sum(1 for q in self._questions.values() if q.parent_id == parent_id)
                qid = f"{parent_id}.{n}"
                while qid in self._questions:
                    n += 1
                    qid = f"{parent_id}.{n}"
            else:
                n = 1 + sum(1 for q in self._questions.values() if q.parent_id is None)
                qid = f"q{n}"
                while qid in self._questions:
                    n += 1
                    qid = f"q{n}"
            q = Question(id=qid, text=text[:500], parent_id=parent_id or None, todo_id=todo_id)
            self._questions[qid] = q
            if select:
                self._current_question = qid
            return q

    def update_question(self, qid: str, *, text: Optional[str] = None,
                        todo_id: Optional[int] = None, dropped: Optional[bool] = None,
                        select: bool = False) -> Question:
        with self._lock:
            q = self._questions.get(str(qid or "").strip())
            if q is None:
                raise ResearchError(f"No question {qid!r}")
            if text is not None and text.strip():
                q.text = text.strip()[:500]
            if todo_id is not None:
                q.todo_id = int(todo_id)
            if dropped is not None:
                q.dropped = bool(dropped)
                if q.dropped and self._current_question == q.id:
                    self._current_question = q.parent_id
            if select:
                self._current_question = q.id
            return q

    def question_claims(self, qid: str) -> List[Claim]:
        """Claims recorded directly on ``qid``."""
        with self._lock:
            return [c for c in self._claims.values() if c.question_id == qid]

    def subtree_claims(self, qid: str) -> List[Claim]:
        """Claims on ``qid`` and on its live (not dropped) sub-questions."""
        with self._lock:
            dropped = {q.id for q in self._questions.values() if q.dropped and q.id != qid}

            def live(claim_qid: str) -> bool:
                return not any(claim_qid == d or claim_qid.startswith(d + ".") for d in dropped)

            return [c for c in self._claims.values()
                    if (c.question_id == qid or c.question_id.startswith(qid + ".")) and live(c.question_id)]

    def question_status(self, q: Question) -> str:
        """Status over the question's subtree: a parent is answered by its sub-questions."""
        return ledger.question_status(q, self.subtree_claims(q.id), self._origin_of)

    # ------------------------------------------------------------- claims

    def claims(self) -> List[Claim]:
        with self._lock:
            return list(self._claims.values())

    def get_claim(self, claim_id: str) -> Optional[Claim]:
        with self._lock:
            return self._claims.get(str(claim_id or "").strip())

    def _origin_of(self, citation_id: int) -> Optional[tuple]:
        source = self.get_source(citation_id)
        if source is None:
            return None
        return source.origin, source.credibility_score

    def claim_confidence(self, claim: Claim) -> str:
        return ledger.claim_confidence(claim, self._origin_of)

    def claim_contested(self, claim: Claim) -> bool:
        return ledger.claim_contested(claim, self._origin_of)

    def claim_origins(self, claim: Claim) -> Dict[str, List[str]]:
        return ledger.claim_origins(claim, self._origin_of)

    def _check_evidence(self, raw: Dict[str, Any]) -> tuple:
        """Validate one evidence item → (Evidence, Source); raises ResearchError."""
        from .extract import excerpt_match

        if not isinstance(raw, dict):
            raise ResearchError("each evidence item must be an object")
        ref = raw.get("citation_id", raw.get("source"))
        source = self.find(ref)
        if source is None:
            raise ResearchError(f"No source {ref!r}")
        if source.state == "candidate" or not source.citation_id:
            raise ResearchError(
                f"Source {source.candidate_id} is an unread search result. Read it with "
                "url_grounding or research_fetch before citing it."
            )
        stance = str(raw.get("stance") or "supports").strip().lower()
        if stance not in ledger.STANCES:
            raise ResearchError(f"stance must be one of {', '.join(ledger.STANCES)}")
        excerpt = " ".join(str(raw.get("excerpt") or "").split())
        if not excerpt:
            raise ResearchError(f"An excerpt from [^{source.citation_id}] is required")
        if len(excerpt) > ledger.EXCERPT_MAX_CHARS:
            raise ResearchError(
                f"Excerpt for [^{source.citation_id}] is {len(excerpt)} chars; quote at most "
                f"{ledger.EXCERPT_MAX_CHARS}."
            )
        body = source.text()
        if body is None:
            raise ResearchError(
                f"The text of [^{source.citation_id}] is no longer available; read it again "
                "with url_grounding before citing it."
            )
        match = excerpt_match(excerpt, body)
        if not match["ok"]:
            nearest = match.get("nearest") or ""
            hint = f' Nearest passage: "{nearest}"' if nearest else ""
            raise ResearchError(
                f"Excerpt not found in [^{source.citation_id}] (best match "
                f"{match['ratio']:.2f} < 0.90). Quote the source text exactly.{hint}"
            )
        locator = str(raw.get("locator") or "").strip()[:120] or None
        return Evidence(citation_id=source.citation_id, stance=stance,
                        excerpt=excerpt, locator=locator), source

    def record_claim(self, question_id: str, text: str, kind: str = "observed",
                     evidence: Optional[List[Dict[str, Any]]] = None,
                     confidence: Optional[str] = None) -> Claim:
        """Add a claim; every evidence item is validated before anything changes."""
        with self._lock:
            q = self._questions.get(str(question_id or "").strip())
            if q is None:
                known = ", ".join(sorted(self._questions)) or "none yet"
                raise ResearchError(
                    f"No question {question_id!r} (known: {known}). Add it with research_question."
                )
            text = " ".join(str(text or "").split())
            if not text:
                raise ResearchError("claim text is required")
            kind = str(kind or "observed").strip().lower()
            if kind not in ledger.CLAIM_KINDS:
                raise ResearchError(f"kind must be one of {', '.join(ledger.CLAIM_KINDS)}")
            if confidence is not None and confidence not in ledger.CONFIDENCE_LEVELS:
                raise ResearchError("confidence may only be low, medium or high")
            checked = [self._check_evidence(item) for item in (evidence or [])]
            if not checked:
                raise ResearchError("a claim needs at least one evidence item")
            claim = Claim(id=f"c{self._next_claim}", question_id=q.id, text=text[:1000],
                          kind=kind, evidence=[ev for ev, _ in checked],
                          confidence_cap=confidence)
            self._next_claim += 1
            self._claims[claim.id] = claim
            for _ev, source in checked:
                self._cite(source, q.id)
            return claim

    def add_evidence(self, claim_id: str, item: Dict[str, Any]) -> Claim:
        with self._lock:
            claim = self._claims.get(str(claim_id or "").strip())
            if claim is None:
                raise ResearchError(f"No claim {claim_id!r}")
            ev, source = self._check_evidence(item)
            claim.evidence.append(ev)
            self._cite(source, claim.question_id)
            return claim

    def _cite(self, source: Source, qid: str) -> None:
        source.state = "cited"
        if qid not in source.question_ids:
            source.question_ids.append(qid)

    # ----------------------------------------------------------- coverage

    def coverage(self, question_id: Optional[str] = None) -> Dict[str, Any]:
        """Per-question claims, origins, contradictions, confidence and gaps."""
        with self._lock:
            qs = list(self._questions.values())
            if question_id:
                qs = [q for q in qs if q.id == question_id or q.id.startswith(question_id + ".")]
            rows = []
            for q in qs:
                claims = self.subtree_claims(q.id)
                origins: set = set()
                contradictions = 0
                levels = {"high": 0, "medium": 0, "low": 0}
                for c in claims:
                    o = self.claim_origins(c)
                    origins.update(o["supports"])
                    contradictions += sum(1 for e in c.evidence if e.stance == "contradicts")
                    levels[self.claim_confidence(c)] += 1
                unread = [
                    s for s in self.all_sources("candidate") if q.id in s.question_ids
                ][:5]
                rows.append({
                    "id": q.id,
                    "text": q.text,
                    "parent_id": q.parent_id,
                    "todo_id": q.todo_id,
                    "status": self.question_status(q),
                    "claims": len(claims),
                    "own_claims": sum(1 for c in claims if c.question_id == q.id),
                    "confidence": levels,
                    "origins": len(origins),
                    "contradictions": contradictions,
                    "unread_candidates": [
                        {"id": s.candidate_id, "title": s.title, "url": s.url} for s in unread
                    ],
                })
            open_without_claims = [r["id"] for r in rows if r["status"] != "dropped" and not r["claims"]]
            counts = {state: len(self.all_sources(state)) for state in SOURCE_STATES}
            return {
                "current_question": self._current_question,
                "questions": rows,
                "open_without_claims": open_without_claims,
                "sources": counts,
                "claims": len(self._claims) if not question_id else sum(r["own_claims"] for r in rows),
            }

    # -------------------------------------------------------- persistence

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "version": STATE_VERSION,
                "current_question": self._current_question,
                "current_topic": self._current_topic,
                "next_sid": self._next_sid,
                "next_cid": self._next_cid,
                "next_claim": self._next_claim,
                "sources": [s.to_dict() for s in self.all_sources()],
                "questions": [q.to_dict() for q in self._questions.values()],
                "claims": [c.to_dict() for c in self._claims.values()],
            }

    def is_empty(self) -> bool:
        with self._lock:
            return not (self._sources or self._questions or self._claims)

    def load_dict(self, data: Any) -> None:
        """Replace state from a snapshot.

        Accepts the v2 dict and the earlier list-of-sources shape (each record
        becomes a cited source keeping its citation number). Invalid records
        are skipped rather than failing the session load.
        """
        with self._lock:
            self.clear()
            if isinstance(data, list):
                self._load_sources_list(data)
                return
            if not isinstance(data, dict):
                return
            for raw in data.get("sources") or []:
                try:
                    source = Source.from_dict(raw)
                except (KeyError, TypeError, ValueError):
                    continue
                if source.sid in self._sources or (
                    source.citation_id and source.citation_id in self._by_cid
                ):
                    continue
                self._index(source)
            for raw in data.get("questions") or []:
                try:
                    q = Question.from_dict(raw)
                except (KeyError, TypeError, ValueError):
                    continue
                self._questions[q.id] = q
            for raw in data.get("claims") or []:
                try:
                    c = Claim.from_dict(raw)
                except (KeyError, TypeError, ValueError):
                    continue
                if c.question_id in self._questions:
                    self._claims[c.id] = c
                    try:
                        self._next_claim = max(self._next_claim, int(c.id.lstrip("c")) + 1)
                    except ValueError:
                        pass
            for attr, key in (("_next_sid", "next_sid"), ("_next_cid", "next_cid"),
                              ("_next_claim", "next_claim")):
                try:
                    setattr(self, attr, max(getattr(self, attr), int(data.get(key) or 0)))
                except (TypeError, ValueError):
                    pass
            current = data.get("current_question")
            self._current_question = current if current in self._questions else None
            self._current_topic = str(data.get("current_topic") or DEFAULT_TOPIC)

    def _load_sources_list(self, records: list) -> None:
        for raw in records:
            if not isinstance(raw, dict):
                continue
            try:
                cid = int(raw.get("id"))
                url = str(raw.get("url") or "").strip()
                if cid < 1 or not url or cid in self._by_cid:
                    continue
                source = Source(
                    sid=cid,
                    url=url,
                    title=str(raw.get("title") or ""),
                    source_type=coerce_source_type(raw.get("source_type")),
                    state="cited",
                    citation_id=cid,
                    authors=[str(a) for a in (raw.get("authors") or []) if a],
                    date=raw.get("date") or None,
                    accessed_date=str(raw.get("accessed_date") or _today()),
                    metadata=dict(raw.get("metadata") or {}),
                    credibility_score=float(raw.get("credibility_score") or 0.0),
                    topic=str(raw.get("topic") or "").strip() or DEFAULT_TOPIC,
                )
            except (TypeError, ValueError):
                continue
            if source.canonical_url in self._by_canonical:
                continue
            self._index(source)
            self._current_topic = source.topic

    # ------------------------------------------------------------ outputs

    def compile_bibliography(self, include: str = "read", question_id: Optional[str] = None) -> str:
        from .report import bibliography

        return bibliography(self, include=include, question_id=question_id)


# Earlier name of the session registry, kept for callers that type against it.
CitationManager = ResearchState


# ------------------------------------------------------------------ context

_active_research_state: contextvars.ContextVar[Optional[ResearchState]] = (
    contextvars.ContextVar("mu_active_research_state", default=None)
)
_default_research_state: Optional[ResearchState] = None
_default_lock = threading.Lock()


def current_research_state() -> Optional[ResearchState]:
    """Return the research state bound to the current context, or None."""
    return _active_research_state.get()


@contextlib.contextmanager
def use_research_state(state: ResearchState) -> Iterator[ResearchState]:
    """Bind ``state`` as the active research state for the current context."""
    token = _active_research_state.set(state)
    try:
        yield state
    finally:
        _active_research_state.reset(token)


def get_citation_manager() -> ResearchState:
    """The bound session state, else a private process default (no session)."""
    active = _active_research_state.get()
    if active is not None:
        return active
    global _default_research_state
    with _default_lock:
        if _default_research_state is None:
            _default_research_state = ResearchState()
        return _default_research_state


def reset_citation_manager() -> None:
    """Clear the bound state in place, or replace the private default."""
    active = _active_research_state.get()
    if active is not None:
        active.clear()
        return
    global _default_research_state
    with _default_lock:
        _default_research_state = ResearchState()
