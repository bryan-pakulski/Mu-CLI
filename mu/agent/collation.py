"""Collation buffer for deferring read‑only tool results.

The buffer stores the raw output of tools that only read data (e.g. ``read_file``
or ``search_for_string``). The model receives a short status message during the
agentic loop, and the full payload can be injected later with a *flush* command.

The buffer is persisted as part of the session JSON file, so a session reload
restores any pending collation entries.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Tuple


class CollationBuffer:
    """Collects tool outputs until the user triggers a flush.

    Each entry is a tuple ``(tool_name, args, result)``. ``args`` is stored as a
    plain ``dict`` to make JSON (de)serialisation straightforward.
    """

    def __init__(self) -> None:
        # A deferred result is evidence, not a cache entry. Keep it until the
        # model explicitly delivers or discards it.
        self.entries: List[Tuple[str, Dict[str, Any], str]] = []

    # ---------------------------------------------------------------------
    # Persistence helpers
    # ---------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """Serialise the buffer for storage in the session JSON file."""
        return {
            "entries": [
                {
                    "tool_name": name,
                    "args": args,
                    "result": result,
                }
                for name, args, result in self.entries
            ]
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CollationBuffer":
        buf = cls()
        for entry in data.get("entries", []):
            buf.entries.append(
                (
                    entry.get("tool_name", ""),
                    entry.get("args", {}),
                    entry.get("result", ""),
                )
            )
        return buf

    # ---------------------------------------------------------------------
    # Core API
    # ---------------------------------------------------------------------
    def add(self, tool_name: str, args: Dict[str, Any], result: str) -> None:
        """Add a result; only explicit model cleanup removes it."""
        self.entries.append((tool_name, args, result))

    def artifact_id(self, index: int) -> str:
        """Stable opaque identifier exposed to the model and trace."""
        name, args, result = self.entries[index]
        payload = json.dumps([name, args, result], default=str, sort_keys=True)
        return "ctx_" + hashlib.sha256(payload.encode()).hexdigest()[:12]

    def manifest(self) -> List[Dict[str, Any]]:
        return [
            {"id": self.artifact_id(i), "tool_name": name, "args": args,
             "bytes": len(result.encode("utf-8", errors="replace"))}
            for i, (name, args, result) in enumerate(self.entries)
        ]

    def flush_selected(self, artifact_ids: List[str] | None = None) -> List[Tuple[str, str]]:
        """Deliver selected artifacts (or all) and remove only those entries."""
        wanted = set(artifact_ids or [])
        selected = []
        kept = []
        for i, entry in enumerate(self.entries):
            aid = self.artifact_id(i)
            if not wanted or aid in wanted:
                name, args, result = entry
                header = f"### Collated Data – {name}\n**Parameters:**\n```json\n{json.dumps(args, indent=2, sort_keys=True)}\n```\n**Result:**\n{result}"
                selected.append((aid, header))
            else:
                kept.append(entry)
        self.entries = kept
        return selected

    def discard(self, artifact_ids: List[str]) -> List[str]:
        """Explicit model-directed cleanup with an auditable return value."""
        wanted = set(artifact_ids)
        removed, kept = [], []
        for i, entry in enumerate(self.entries):
            aid = self.artifact_id(i)
            if aid in wanted:
                removed.append(aid)
            else:
                kept.append(entry)
        self.entries = kept
        return removed
