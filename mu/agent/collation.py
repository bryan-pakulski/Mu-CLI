"""Collation buffer for deferring read‑only tool results.

The buffer stores the raw output of tools that only read data (e.g. ``read_file``
or ``search_for_string``). The model receives a short status message during the
agentic loop, and the full payload can be injected later with a *flush* command.

The buffer is persisted as part of the session JSON file, so a session reload
restores any pending collation entries.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple


class CollationBuffer:
    """Collects tool outputs until the user triggers a flush.

    Each entry is a tuple ``(tool_name, args, result)``. ``args`` is stored as a
    plain ``dict`` to make JSON (de)serialisation straightforward.
    """

    def __init__(self, max_bytes: int = 1_000_000) -> None:
        self.entries: List[Tuple[str, Dict[str, Any], str]] = []
        self.max_bytes = max_bytes

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
        """Add a new result to the buffer.

        If the total byte size would exceed ``max_bytes`` the oldest entry is
        dropped and a placeholder entry is inserted to inform the model that a
        truncation occurred.
        """
        self.entries.append((tool_name, args, result))
        self._enforce_limit()

    def _enforce_limit(self) -> None:
        """Ensure the buffer does not exceed ``max_bytes``.

        The size is approximated by the sum of ``len(result)`` for each entry.
        When the limit is crossed, entries are removed from the front until the
        size is back under the threshold. A synthetic entry describing the
        truncation is inserted at the beginning so the model knows data was
        lost.
        """
        total = sum(len(r) for _, _, r in self.entries)
        if total <= self.max_bytes:
            return
        removed = 0
        while self.entries and total > self.max_bytes:
            _, _, res = self.entries.pop(0)
            total -= len(res)
            removed += 1
        if removed:
            notice = f"[Collation] {removed} older entry(ies) were truncated due to size limits."
            self.entries.insert(0, ("truncation_notice", {}, notice))

    def flush(self) -> List[str]:
        """Return formatted messages and clear the buffer.

        Each message follows the template used by the original implementation:
        ``"### Collated Data – {tool_name}\n**Parameters:** <json>\n**Result:**\n{result}"``
        """
        formatted: List[str] = []
        for tool_name, args, result in self.entries:
            if tool_name == "truncation_notice":
                formatted.append(f"⚠️ **TRUNCATION NOTICE:** {result}")
                continue

            header = f"### Collated Data – {tool_name}"
            params = json.dumps(args, indent=2, sort_keys=True)
            body = f"{header}\n**Parameters:**\n```json\n{params}\n```\n**Result:**\n{result}"
            formatted.append(body)
        self.entries.clear()
        return formatted
