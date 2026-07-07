"""Persistent memory and turn-local scratchpad stores for agentic sessions."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# Valid lifecycle statuses for memory entries.
ACTIVE = "active"
DONE = "done"
SUPERSEDED = "superseded"
ARCHIVED = "archived"
STALE = "stale"
ALLOWED_STATUSES = {ACTIVE, DONE, SUPERSEDED, ARCHIVED, STALE}

# Status weights for eviction scoring. Lower weight = evicted sooner.
STATUS_EVIC_WEIGHTS: Dict[str, float] = {
    ACTIVE: 1.0,
    STALE: 0.8,
    DONE: 0.5,
    SUPERSEDED: 0.3,
    ARCHIVED: 0.1,
}


@dataclass
class MemoryEntry:
    id: int
    content: str
    tags: List[str] = field(default_factory=list)
    source: str = ""
    kind: str = "observation"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    hits: int = 0
    status: str = ACTIVE
    superseded_by: Optional[int] = None
    supersedes: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "tags": self.tags,
            "source": self.source,
            "kind": self.kind,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "hits": self.hits,
            "status": self.status,
            "superseded_by": self.superseded_by,
            "supersedes": self.supersedes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryEntry":
        return cls(
            id=int(data.get("id", 0)),
            content=str(data.get("content", "")),
            tags=list(data.get("tags", [])),
            source=str(data.get("source", "")),
            kind=str(data.get("kind") or "observation"),
            created_at=float(data.get("created_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
            hits=int(data.get("hits", 0)),
            status=str(data.get("status") or ACTIVE),
            superseded_by=data.get("superseded_by"),
            supersedes=data.get("supersedes"),
        )


class BaseNoteStore:
    title = "Notes"

    # Default kind weights for eviction scoring. Higher weight = kept longer.
    # Decisions (architectural choices, design rationale) are most valuable.
    # Findings (root causes, verified facts) are next.
    # Observations (general notes) are lowest priority.
    DEFAULT_EVIC_KIND_WEIGHTS: Dict[str, float] = {
        "decision": 3.0,
        "finding": 2.0,
        "observation": 1.0,
        "goal": 2.5,
    }

    def __init__(
        self,
        max_entries: int = 64,
        summary_char_limit: int = 2_000,
        eviction_kind_weights: Dict[str, float] | None = None,
    ) -> None:
        self.max_entries = max_entries
        self.summary_char_limit = summary_char_limit
        self.entries: List[MemoryEntry] = []
        self._next_id = 1
        self.eviction_kind_weights = eviction_kind_weights or dict(
            self.DEFAULT_EVIC_KIND_WEIGHTS
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_entries": self.max_entries,
            "summary_char_limit": self.summary_char_limit,
            "next_id": self._next_id,
            "entries": [entry.to_dict() for entry in self.entries],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BaseNoteStore":
        store = cls(
            max_entries=int(data.get("max_entries", 64)),
            summary_char_limit=int(data.get("summary_char_limit", 2_000)),
        )
        store._next_id = int(data.get("next_id", 1))
        store.entries = [
            MemoryEntry.from_dict(item) for item in data.get("entries", [])
        ]
        if store.entries and store._next_id <= max(entry.id for entry in store.entries):
            store._next_id = max(entry.id for entry in store.entries) + 1
        return store

    def clear(self) -> None:
        self.entries.clear()
        self._next_id = 1

    def save(
        self,
        content: str,
        tags: List[str] | None = None,
        source: str = "",
        kind: str = "observation",
        status: str = ACTIVE,
    ) -> MemoryEntry:
        tags = [str(tag).strip().lower() for tag in (tags or []) if str(tag).strip()]
        content = str(content or "").strip()

        existing = next(
            (
                entry
                for entry in self.entries
                if entry.content == content and sorted(entry.tags) == sorted(tags)
            ),
            None,
        )
        if existing:
            existing.updated_at = time.time()
            existing.hits += 1
            if source and not existing.source:
                existing.source = source
            if kind and not existing.kind:
                existing.kind = kind
            # Update status if content+tags match but status differs
            if status != existing.status:
                existing.status = status
            return existing

        entry = MemoryEntry(
            id=self._next_id,
            content=content,
            tags=tags,
            source=source,
            kind=kind,
            status=status,
        )
        self._next_id += 1
        self.entries.append(entry)
        self._enforce_limit()
        return entry

    def get_entry(self, entry_id: int) -> Optional[MemoryEntry]:
        return next(
            (entry for entry in self.entries if entry.id == entry_id), None
        )

    def update_status(self, entry_id: int, status: str) -> Optional[MemoryEntry]:
        if status not in ALLOWED_STATUSES:
            return None
        entry = self.get_entry(entry_id)
        if entry is None:
            return None
        entry.status = status
        entry.updated_at = time.time()
        return entry

    def supersede(self, old_id: int, new_id: int) -> Optional[tuple]:
        old = self.get_entry(old_id)
        new = self.get_entry(new_id)
        if old is None or new is None:
            return None
        old_status = old.status
        new_status = new.status
        old.status = SUPERSEDED
        old.superseded_by = new_id
        new.supersedes = old_id
        old.updated_at = time.time()
        new.updated_at = time.time()
        return (old, new, old_status, new_status)

    def search(
        self,
        query: str = "",
        limit: int = 5,
        status_filter: str | list[str] | None = None,
        kind_filter: str | list[str] | None = None,
        tags_exclude: list[str] | None = None,
        include_all: bool = False,
    ) -> List[MemoryEntry]:
        # Normalize status_filter to a set
        if status_filter is None and not include_all:
            status_set = {ACTIVE}
        elif status_filter is None and include_all:
            status_set = None  # no status filtering
        else:
            if isinstance(status_filter, str):
                status_set = {status_filter}
            else:
                status_set = set(status_filter)

        # Normalize kind_filter to a set
        if kind_filter is None:
            kind_set = None
        elif isinstance(kind_filter, str):
            kind_set = {kind_filter}
        else:
            kind_set = set(kind_filter)

        # Normalize tags_exclude
        exclude_tags = set(tags_exclude or [])

        terms = [term for term in str(query or "").lower().split() if term]
        ranked = []
        for entry in self.entries:
            # Status filtering
            if status_set is not None and entry.status not in status_set:
                continue
            # Kind filtering
            if kind_set is not None and entry.kind not in kind_set:
                continue
            # Tag exclusion filtering
            if exclude_tags and any(tag in exclude_tags for tag in entry.tags):
                continue

            haystack = " ".join(
                [entry.content, " ".join(entry.tags), entry.source]
            ).lower()
            score = 0
            for term in terms:
                if term in haystack:
                    score += 2
                if term in entry.content.lower():
                    score += 1
            if not terms:
                score = 1
            ranked.append((score, entry.updated_at, entry))

        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        results = [entry for score, _, entry in ranked if score > 0][: max(1, limit)]
        for entry in results:
            entry.hits += 1
            entry.updated_at = time.time()
        return results

    def list_entries(
        self, limit: int = 10, status_filter: str | list[str] | None = None
    ) -> List[MemoryEntry]:
        # Normalize status_filter
        if status_filter is None:
            status_set = None
        elif isinstance(status_filter, str):
            status_set = {status_filter}
        else:
            status_set = set(status_filter)

        filtered = self.entries
        if status_set is not None:
            filtered = [e for e in filtered if e.status in status_set]

        return sorted(filtered, key=lambda entry: entry.updated_at, reverse=True)[
            : max(1, limit)
        ]

    def render_summary(self, limit: int = 8, include_archived: bool = False) -> str:
        # Partition by status
        active_entries = []
        done_entries = []
        other_entries = []
        for entry in sorted(self.entries, key=lambda e: e.updated_at, reverse=True):
            if entry.status == ACTIVE:
                active_entries.append(entry)
            elif entry.status == DONE:
                done_entries.append(entry)
            elif entry.status == ARCHIVED and not include_archived:
                continue
            else:
                other_entries.append(entry)

        # Active first, done capped at 2, then other (non-archived)
        ordered = active_entries[:limit]
        remaining = limit - len(ordered)
        if remaining > 0:
            ordered.extend(done_entries[:min(2, remaining)])
            remaining = limit - len(ordered)
        if remaining > 0:
            ordered.extend(other_entries[:remaining])

        if not ordered:
            return ""

        lines = [f"### {self.title}"]
        for entry in ordered:
            tags = f" [{', '.join(entry.tags)}]" if entry.tags else ""
            source = f" ({entry.source})" if entry.source else ""
            lines.append(
                f"- #{entry.id} [{entry.status}]{tags}{source}: {entry.content}"
            )

        summary = "\n".join(lines)
        if len(summary) <= self.summary_char_limit:
            return summary
        return summary[: self.summary_char_limit - 3] + "..."

    def format_results(self, entries: List[MemoryEntry]) -> str:
        if not entries:
            return f"No {self.title.lower()} entries matched."

        lines = []
        for entry in entries:
            tags = json.dumps(entry.tags)
            source = entry.source or "n/a"
            lines.append(f"#{entry.id} [{entry.status}] kind={entry.kind} tags={tags} source={source} :: {entry.content}")
        return "\n".join(lines)

    def status_counts(self) -> Dict[str, int]:
        counts = {s: 0 for s in ALLOWED_STATUSES}
        for entry in self.entries:
            counts[entry.status] = counts.get(entry.status, 0) + 1
        return counts

    def _eviction_score(self, entry: MemoryEntry) -> float:
        """Score an entry for eviction. Lower score = evicted first.

        Combines hits (access frequency) with kind weight (semantic
        importance) and status weight (lifecycle relevance). Uses (hits + 1)
        so that kind/status weight matters even for newly-created entries.
        """
        kind_weight = self.eviction_kind_weights.get(entry.kind, 1.0)
        status_weight = STATUS_EVIC_WEIGHTS.get(entry.status, 1.0)
        return float(entry.hits + 1) * kind_weight * status_weight

    def _enforce_limit(self) -> None:
        if len(self.entries) <= self.max_entries:
            return

        # Status-aware + kind-aware eviction: sort by eviction score (lowest
        # evicted first). Falls back to updated_at as tiebreaker — older
        # entries evicted before newer ones with the same score.
        # Eviction order: archived → superseded → done → stale → active
        # (lower STATUS_EVIC_WEIGHTS = evicted first)
        self.entries.sort(
            key=lambda entry: (self._eviction_score(entry), entry.updated_at)
        )
        while len(self.entries) > self.max_entries:
            self.entries.pop(0)


class TaskMemoryStore(BaseNoteStore):
    title = "In-Task Memory"

    def __init__(self, max_entries: int = 1024, summary_char_limit: int = 16_000) -> None:
        super().__init__(max_entries=max_entries, summary_char_limit=summary_char_limit)


class ScratchpadStore(BaseNoteStore):
    title = "Turn Scratchpad"

    def __init__(self, max_entries: int = 256, summary_char_limit: int = 8_000) -> None:
        super().__init__(max_entries=max_entries, summary_char_limit=summary_char_limit)

    def save(
        self,
        content: str,
        tags: List[str] | None = None,
        source: str = "",
        kind: str = "",
        status: str = ACTIVE,
    ) -> MemoryEntry:
        # Scratchpad is ephemeral — lifecycle does not apply. Force status=active.
        return super().save(content, tags=tags, source=source, kind=kind, status=ACTIVE)