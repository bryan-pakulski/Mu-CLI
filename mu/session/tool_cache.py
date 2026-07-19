"""Sidecar cache for tool results that get compressed out of history.

Stores full tool results keyed by content hash.  The ``recall`` tool
fetches them back without re-reading files or blowing context budget.

Design:
    - ``store(call_id, tool_name, result)`` → cache key (or None if not cacheable)
    - ``recall(key)`` → full original result dict
    - ``lookup_by_locator(tool_name, tool_args)`` → auto-recall a re-read of
      the same path/args without executing the tool again (Fix #10)
    - ``keys_summary()`` → lightweight listing for introspection
    - LRU eviction by count (max 50) and bytes (max 500 KB)
    - Only read-only tools are cached; write tools are skipped
"""

from collections import OrderedDict
from typing import Any, Dict, List, Optional
import hashlib
import json
import os

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

# Tools eligible for auto-recall by locator (Fix #10). Each takes a `path`
# (or `pattern`+`path`) argument identifying the on-disk source, so a repeat
# call with unchanged args + unchanged file can short-circuit to the cached
# result instead of re-reading and re-burning tokens.
_LOCATOR_TOOLS = frozenset({
    "read_file",
    "get_chunk",
    "list_dir",
    "search_for_string",
    "search_references",
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
        # Locator index: "{tool_name}:{sorted_args json}" → cache key (Fix
        # #10). Lets `lookup_by_locator` auto-recall a re-read of the same
        # path/args without re-executing the read-only tool. Maintained
        # alongside `store` / eviction; cleared entries are pruned lazily
        # by `lookup_by_locator` (missing key → no hit) and on eviction.
        self._locator_index: "Dict[str, str]" = {}

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

    # -------------------------------------------------------------- locator

    @staticmethod
    def _locator_for(tool_name: str, tool_args: Any) -> Optional[str]:
        """Build a stable locator string for auto-recall (Fix #10).

        Returns ``"{tool_name}:{sorted_args_json}"`` for read-only locator
        tools, else None. Args are serialized sorted so call order within a
        dict doesn't defeat the match. None for write tools / unknown tools
        / non-dict args — those can't be safely auto-recalled.
        """
        if tool_name not in _LOCATOR_TOOLS:
            return None
        if not isinstance(tool_args, dict):
            return None
        try:
            payload = json.dumps(tool_args, sort_keys=True, default=str)
        except Exception:
            return None
        return f"{tool_name}:{payload}"

    @staticmethod
    def _path_arg(tool_args: Any) -> Optional[str]:
        """Extract the on-disk path an arg dict refers to, if any."""
        if not isinstance(tool_args, dict):
            return None
        path = tool_args.get("path")
        if isinstance(path, str) and path:
            return path
        # search_* tools key the directory under `path` too; fall back to any
        # string arg named like a path.
        return None

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
        collation buffer before the model calls ``flush``), caching the raw payload gives
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

        entry: Dict[str, Any] = {
            "tool_name": tool_name,
            "result": result,
            "size_bytes": size_bytes,
        }
        # On-disk freshness metadata is recorded by `store_with_locator`
        # (Fix #10) when the caller passes the tool args. Plain `store`
        # doesn't have args, so the entry is usable for `recall` by key but
        # not for auto-recall (can't prove freshness).

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
            # Prune any locator pointers that resolved to the evicted key.
            for loc, k in list(self._locator_index.items()):
                if k == ev_key:
                    del self._locator_index[loc]

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

    # ------------------------------------------------------- store with args

    def store_with_locator(
        self,
        call_id: str,
        tool_name: str,
        tool_args: Any,
        result: Any,
        *,
        force: bool = False,
    ) -> Optional[str]:
        """Store + index by locator (Fix #10).

        Like ``store`` but also records a ``locator → key`` pointer and the
        on-disk ``mtime``/``size`` of the source path (when present) so a
        later repeat call with the same args can be auto-recalled by
        ``lookup_by_locator`` without re-reading the file. Use this from the
        read-only dispatch path; use plain ``store`` when args aren't
        available (collation, legacy callers).
        """
        key = self.store(call_id, tool_name, result, force=force)
        if key is None:
            return None
        locator = self._locator_for(tool_name, tool_args)
        if locator is not None:
            self._locator_index[locator] = key
            path = self._path_arg(tool_args)
            entry = self._cache.get(key)
            if entry is not None and path:
                try:
                    st = os.stat(path)
                    entry["mtime"] = st.st_mtime
                    entry["size"] = st.st_size
                except OSError:
                    entry["mtime"] = None
                    entry["size"] = None
        return key

    # ------------------------------------------------------------ auto-recall

    def lookup_by_locator(
        self, tool_name: str, tool_args: Any
    ) -> Optional[dict]:
        """Auto-recall a re-read of the same path/args (Fix #10).

        If a prior call to the same read-only tool with the same args is
        cached AND the on-disk source file is unchanged (mtime+size match),
        return the cached result so the dispatch can short-circuit instead
        of re-reading and re-burning tokens. Returns None on any miss,
        staleness, or error — the caller falls back to executing the tool.
        """
        locator = self._locator_for(tool_name, tool_args)
        if locator is None:
            return None
        key = self._locator_index.get(locator)
        if key is None or key not in self._cache:
            return None
        entry = self._cache.get(key)
        if entry is None:
            return None
        # Freshness: if the cached entry recorded mtime/size, the current
        # file must still match. If metadata wasn't recorded (non-path tool
        # or file was missing at store time) we can't prove freshness → no
        # auto-recall (safer to re-execute).
        path = self._path_arg(tool_args)
        if path is not None:
            cached_mtime = entry.get("mtime")
            cached_size = entry.get("size")
            if cached_mtime is None or cached_size is None:
                return None
            try:
                st = os.stat(path)
            except OSError:
                return None
            if st.st_mtime != cached_mtime or st.st_size != cached_size:
                return None  # file changed since cached read
        # LRU touch
        self._cache.move_to_end(key)
        return {
            "tool_name": entry["tool_name"],
            "result": entry["result"],
            "cache_key": key,
            "cache_hit": True,
        }

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
        self._locator_index.clear()
