"""Persistent memory and turn-local scratchpad stores for agentic sessions."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class MemoryEntry:
    id: int
    content: str
    tags: List[str] = field(default_factory=list)
    source: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    hits: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "tags": self.tags,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "hits": self.hits,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryEntry":
        return cls(
            id=int(data.get("id", 0)),
            content=str(data.get("content", "")),
            tags=list(data.get("tags", [])),
            source=str(data.get("source", "")),
            created_at=float(data.get("created_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
            hits=int(data.get("hits", 0)),
        )


class BaseNoteStore:
    title = "Notes"

    def __init__(self, max_entries: int = 64, summary_char_limit: int = 2_000) -> None:
        self.max_entries = max_entries
        self.summary_char_limit = summary_char_limit
        self.entries: List[MemoryEntry] = []
        self._next_id = 1

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
            return existing

        entry = MemoryEntry(
            id=self._next_id,
            content=content,
            tags=tags,
            source=source,
        )
        self._next_id += 1
        self.entries.append(entry)
        self._enforce_limit()
        return entry

    def search(self, query: str = "", limit: int = 5) -> List[MemoryEntry]:
        terms = [term for term in str(query or "").lower().split() if term]
        ranked = []
        for entry in self.entries:
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

    def list_entries(self, limit: int = 10) -> List[MemoryEntry]:
        return sorted(self.entries, key=lambda entry: entry.updated_at, reverse=True)[
            : max(1, limit)
        ]

    def render_summary(self, limit: int = 8) -> str:
        entries = self.list_entries(limit=limit)
        if not entries:
            return ""

        lines = [f"### {self.title}"]
        for entry in entries:
            tags = f" [{', '.join(entry.tags)}]" if entry.tags else ""
            source = f" ({entry.source})" if entry.source else ""
            lines.append(f"- #{entry.id}{tags}{source}: {entry.content}")

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
            lines.append(f"#{entry.id} tags={tags} source={source} :: {entry.content}")
        return "\n".join(lines)

    def _enforce_limit(self) -> None:
        if len(self.entries) <= self.max_entries:
            return

        self.entries.sort(key=lambda entry: (entry.hits, entry.updated_at))
        while len(self.entries) > self.max_entries:
            self.entries.pop(0)


class TaskMemoryStore(BaseNoteStore):
    title = "In-Task Memory"


class ScratchpadStore(BaseNoteStore):
    title = "Turn Scratchpad"
