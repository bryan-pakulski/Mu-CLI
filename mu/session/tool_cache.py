"""Sidecar cache for tool results that get compressed out of history.

Stores full tool results keyed by content hash.  The ``recall`` tool
fetches them back without re-reading files or blowing context budget.

Design:
    - ``store(call_id, tool_name, result)`` → cache key (or None if not cacheable)
    - ``recall(key)`` → full original result dict
    - ``keys_summary()`` → lightweight listing for introspection
    - LRU eviction by count (max 50) and bytes (max 500 KB)
    - Only read-only tools are cached; write tools are skipped
"""

from collections import OrderedDict
from typing import Any, Dict, List, Optional
import hashlib
import json

# Tools whose results are worth caching — read-only, expensive to re-fetch.
_CACHEABLE_TOOLS = frozenset({
    "read_file",
    "get_chunk",
    "search_for_string",
    "search_references",
    "retrieve_relevant_context",
    "list_dir",
    "get_workspace_details",
})


class ToolResultCache:
    """LRU cache with byte + count limits.

    Stores full tool results so the model can ``recall`` them after
    context compression has replaced the original tool_result message
    with a one-line summary.
    """

    def __init__(
        self,
        max_entries: int = 50,
        max_bytes: int = 524_288,  # 512 KB
    ):
        self._cache: "OrderedDict[str, dict]" = OrderedDict()
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._current_bytes = 0
        # Reverse index: result-content hash → cache key (R10, FM-9).
        # Lets `HistorySearchMixin._lookup_cache_key` resolve a tool_result
        # part to its cache key in O(1) instead of scanning every cached
        # entry. Maintained alongside `store` / eviction.
        self._result_index: "Dict[str, str]" = {}

    # ------------------------------------------------------------------ key

    @staticmethod
    def _make_key(call_id: str, tool_name: str, result: Any) -> str:
        """Deterministic 12-char key from call_id + result content."""
        content = json.dumps(
            {"call_id": call_id, "tool": tool_name, "result": result},
            default=str,
            sort_keys=True,
        )
        return hashlib.sha256(content.encode()).hexdigest()[:12]

    @staticmethod
    def _result_hash(result: Any) -> str:
        """Stable hash of the result CONTENT only (no call_id/tool).

        Mirrors the comparison `HistorySearchMixin._lookup_cache_key` used to
        do line-for-line: ``json.dumps(result, default=str)`` (no sort_keys,
        no ensure_ascii) so the hashes agree across store-time and
        lookup-time."""
        serialized = json.dumps(result, default=str)
        return hashlib.sha256(serialized.encode()).hexdigest()[:16]

    # ---------------------------------------------------------------- store

    def store(
        self,
        call_id: str,
        tool_name: str,
        result: Any,
        *,
        force: bool = False,
    ) -> Optional[str]:
        """Store a tool result.  Returns cache key, or None if not cacheable.

        Non-cacheable tools (writes, bash, etc.) return None — the caller
        should treat None as "no cache annotation" and proceed normally.

        ``force=True`` bypasses the ``_CACHEABLE_TOOLS`` filter so the result
        is cached even for tools not on the default allowlist. Used by the
        collation path: when a read-only result is deferred into the
        collation buffer (and may later be dropped by ``_enforce_limit``
        before the model calls ``flush``), caching the raw payload gives
        the model a ``recall(cache_key)`` recovery path it would otherwise
        not have. See R11 in documentation/harness-investigation.md (FM-4).
        """
        if not force and tool_name not in _CACHEABLE_TOOLS:
            return None

        key = self._make_key(call_id, tool_name, result)
        rhash = self._result_hash(result)
        size_bytes = len(
            json.dumps(result, default=str, ensure_ascii=False).encode()
        )

        entry = {
            "tool_name": tool_name,
            "result": result,
            "size_bytes": size_bytes,
        }

        # Evict oldest entries if over budget. Drop the evicted entry's
        # reverse-index mapping too so stale result_hash → key pointers
        # don't resurrect evicted content.
        while (
            self._current_bytes + size_bytes > self.max_bytes
            or len(self._cache) >= self.max_entries
        ) and self._cache:
            ev_key, evicted = self._cache.popitem(last=False)
            self._current_bytes -= evicted["size_bytes"]
            ev_hash = self._result_hash(evicted["result"])
            if self._result_index.get(ev_hash) == ev_key:
                del self._result_index[ev_hash]

        # If already present (same key), remove old entry so we re-insert at end
        if key in self._cache:
            old = self._cache.pop(key)
            self._current_bytes -= old["size_bytes"]

        self._cache[key] = entry
        self._current_bytes += size_bytes
        # (Re)point the reverse index at this key. A result-content collision
        # across two keys is possible but extremely unlikely; the lookup path
        # verifies the cached tool_name before trusting the pointer, so a
        # collision degrades to a linear-scan fallback, never a wrong key.
        self._result_index[rhash] = key
        return key

    # ---------------------------------------------------------------- recall

    def recall(self, key: str) -> Optional[dict]:
        """Fetch a cached result by key.  Returns None if missing/evicted."""
        entry = self._cache.get(key)
        if entry is None:
            return None
        # LRU touch
        self._cache.move_to_end(key)
        return {
            "tool_name": entry["tool_name"],
            "result": entry["result"],
            "cache_key": key,
        }

    # ------------------------------------------------------------ introspect

    def keys_summary(self) -> List[Dict[str, Any]]:
        """Lightweight listing for 'what did I cache?' introspection."""
        return [
            {"key": k, "tool": v["tool_name"], "size": v["size_bytes"]}
            for k, v in self._cache.items()
        ]

    # --------------------------------------------------------------- utility

    def __len__(self) -> int:
        return len(self._cache)

    def __contains__(self, key: str) -> bool:
        return key in self._cache

    def clear(self) -> None:
        """Clear all cached results."""
        self._cache.clear()
        self._current_bytes = 0
        self._result_index.clear()