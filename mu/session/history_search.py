"""Queryable session history — lexical search over ``self.history``.

``HistorySearchMixin`` factors the search algorithm out of
``SessionManager``.  The mixin operates on the same instance attributes
as ``HistoryMixin``:

  * ``history``              — list[dict] of message dicts
  * ``summary_anchor``       — int index; everything < anchor is summarized
  * ``tool_result_cache``    — ``ToolResultCache`` for cache_key lookup

The mixin is a plain class with no ``__init__``; consumers inherit.
``SessionManager`` inherits both ``HistoryMixin`` and
``HistorySearchMixin``.

Search is pure in-memory lexical matching — no new dependencies, no
index, no mutation.  It scans ``self.history`` linearly, applies filters,
and returns ranked, context-rich snippets so the agent can recover
information from turns that have been compacted behind the summary
anchor.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .helpers import _shorten_tool_args

# Match-type rank weights (lower = higher priority).
_RANK_TEXT = 1
_RANK_TOOL_NAME = 2
_RANK_TOOL_ARGS = 3
_RANK_TOOL_RESULT = 4
_RANK_FILE = 5
_RANK_IMAGE = 6

_SNIPPET_LEN = 200
_PREVIEW_LEN = 100
_MAX_RESULT_CHARS = 4000


class HistorySearchMixin:
    """Search methods for conversation history.  Host must supply
    ``history``, ``summary_anchor``, and ``tool_result_cache``."""

    # ----------------------------------------------------------------- public

    def search_history(
        self,
        query: str,
        *,
        role: Optional[str] = None,
        tool_name: Optional[str] = None,
        start_index: int = 0,
        end_index: Optional[int] = None,
        include_summarized: bool = True,
        context_messages: int = 2,
        max_results: int = 20,
    ) -> Dict[str, Any]:
        """Search the full conversation history (including compacted
        messages behind the summary anchor).

        Returns a dict::

            {
                "results": [...],
                "total_matches": int,
                "has_more": bool,
                "anchor": int,
            }

        Each result dict has the keys specified in the feature spec.
        """
        query = (query or "").strip()
        has_tool_filter = bool((tool_name or "").strip())
        if not query and not has_tool_filter:
            return {
                "results": [],
                "total_matches": 0,
                "has_more": False,
                "anchor": getattr(self, "summary_anchor", 0),
                "error": "query is required",
            }

        history = self.history
        if not history:
            return {
                "results": [],
                "total_matches": 0,
                "has_more": False,
                "anchor": getattr(self, "summary_anchor", 0),
                "message": "No conversation history in this session.",
            }

        anchor = getattr(self, "summary_anchor", 0)
        start = max(0, int(start_index))
        end = int(end_index) if end_index is not None else len(history)
        end = min(end, len(history))

        q_lower = query.lower()
        role_lower = role.lower() if role else None
        tool_lower = tool_name.lower() if tool_name else None

        raw_hits: List[Dict[str, Any]] = []

        for idx in range(start, end):
            msg = history[idx]
            # --- anchor filter ---
            if not include_summarized and idx < anchor:
                continue
            # --- role filter ---
            msg_role = str(msg.get("role", ""))
            if role_lower and msg_role.lower() != role_lower:
                continue

            parts = msg.get("parts", []) or []

            # When tool_name filter is active, skip messages that don't
            # contain at least one tool_call or tool_result with the
            # matching tool name.  Text-only messages are excluded.
            if has_tool_filter:
                has_matching_tool = any(
                    p.get("type") in ("tool_call", "tool_result")
                    and tool_lower in str(p.get("tool_name", "")).lower()
                    for p in parts
                )
                if not has_matching_tool:
                    continue

            matched_parts: List[Dict[str, str]] = []
            best_rank = 99

            for part in parts:
                p_type = part.get("type")
                part_matches: List[Dict[str, str]] = []

                if p_type == "text":
                    text = str(part.get("text", ""))
                    if q_lower in text.lower():
                        snippet = self._extract_snippet(text, q_lower)
                        part_matches.append(
                            {"type": "text", "snippet": snippet, "match_type": "text"}
                        )
                        best_rank = min(best_rank, _RANK_TEXT)

                elif p_type == "tool_call":
                    tn = str(part.get("tool_name", ""))
                    args_str = json.dumps(part.get("tool_args", {}), default=str)
                    # If tool_name filter active, skip non-matching tools.
                    if has_tool_filter and tool_lower not in tn.lower():
                        continue
                    matched_this = False
                    if q_lower in tn.lower():
                        snippet = f"{tn} {_shorten_tool_args(part.get('tool_args', {}))}"
                        part_matches.append(
                            {
                                "type": "tool_call",
                                "tool_name": tn,
                                "snippet": snippet[:_SNIPPET_LEN],
                                "match_type": "tool_name",
                            }
                        )
                        best_rank = min(best_rank, _RANK_TOOL_NAME)
                        matched_this = True
                    if q_lower in args_str.lower():
                        snippet = f"{tn} {_shorten_tool_args(part.get('tool_args', {}))}"
                        part_matches.append(
                            {
                                "type": "tool_call",
                                "tool_name": tn,
                                "snippet": snippet[:_SNIPPET_LEN],
                                "match_type": "tool_args",
                            }
                        )
                        best_rank = min(best_rank, _RANK_TOOL_ARGS)
                        matched_this = True
                    # If tool_name filter set and tool matches but query
                    # doesn't match the call, still count it as a hit so
                    # the filter returns the tool's calls.
                    if has_tool_filter and not matched_this:
                        snippet = f"{tn} {_shorten_tool_args(part.get('tool_args', {}))}"
                        part_matches.append(
                            {
                                "type": "tool_call",
                                "tool_name": tn,
                                "snippet": snippet[:_SNIPPET_LEN],
                                "match_type": "tool_name_filter",
                            }
                        )
                        best_rank = min(best_rank, _RANK_TOOL_NAME)

                elif p_type == "tool_result":
                    tn = str(part.get("tool_name", ""))
                    result_str = str(part.get("tool_result", ""))
                    if has_tool_filter and tool_lower not in tn.lower():
                        continue
                    if q_lower in result_str.lower():
                        snippet = result_str[:_SNIPPET_LEN]
                        part_matches.append(
                            {
                                "type": "tool_result",
                                "snippet": snippet,
                                "match_type": "tool_result",
                            }
                        )
                        best_rank = min(best_rank, _RANK_TOOL_RESULT)

                elif p_type == "file":
                    file_ref = part.get("file_ref", {}) or {}
                    display = str(file_ref.get("display_name", ""))
                    uri = str(file_ref.get("uri", ""))
                    if q_lower in display.lower() or q_lower in uri.lower():
                        snippet = f"file:{display or uri or 'unknown'}"
                        part_matches.append(
                            {
                                "type": "file",
                                "snippet": snippet[:_SNIPPET_LEN],
                                "match_type": "file_name",
                            }
                        )
                        best_rank = min(best_rank, _RANK_FILE)

                elif p_type == "image_input":
                    img = part.get("image", {}) or {}
                    source = str(img.get("source", ""))
                    mime = str(img.get("mime_type", ""))
                    if q_lower in source.lower() or q_lower in mime.lower():
                        snippet = f"image:{source or mime or 'unknown'}"
                        part_matches.append(
                            {
                                "type": "image_input",
                                "snippet": snippet[:_SNIPPET_LEN],
                                "match_type": "image_metadata",
                            }
                        )
                        best_rank = min(best_rank, _RANK_IMAGE)

                matched_parts.extend(part_matches)

            if not matched_parts:
                continue

            # Build context.
            ctx_before = self._build_context(history, idx, -1, context_messages, start)
            ctx_after = self._build_context(history, idx, 1, context_messages, end - 1)

            # Cache key lookup for tool_result hits.
            cache_key = None
            for part in parts:
                if part.get("type") == "tool_result":
                    ck = self._lookup_cache_key(part)
                    if ck:
                        cache_key = ck
                        break

            raw_hits.append(
                {
                    "index": idx,
                    "role": msg_role,
                    "before_anchor": idx < anchor,
                    "parts_matched": matched_parts,
                    "context_before": ctx_before,
                    "context_after": ctx_after,
                    "cache_key": cache_key,
                    "_rank": best_rank,
                }
            )

        # Sort by rank (lower=better), then by index (earlier first).
        raw_hits.sort(key=lambda h: (h["_rank"], h["index"]))

        total = len(raw_hits)
        max_results = max(1, int(max_results))
        has_more = total > max_results
        trimmed = raw_hits[:max_results]

        # Clean _rank from output.
        results: List[Dict[str, Any]] = []
        total_chars = 0
        for hit in trimmed:
            clean = {k: v for k, v in hit.items() if k != "_rank"}
            hit_str = json.dumps(clean, default=str)
            if total_chars + len(hit_str) > _MAX_RESULT_CHARS:
                has_more = True
                break
            total_chars += len(hit_str)
            results.append(clean)

        return {
            "results": results,
            "total_matches": total,
            "has_more": has_more,
            "anchor": anchor,
        }

    # --------------------------------------------------------------- helpers

    @staticmethod
    def _extract_snippet(text: str, query_lower: str) -> str:
        """Extract a snippet of ~``_SNIPPET_LEN`` chars centered on the
        first match of *query_lower* in *text*."""
        pos = text.lower().find(query_lower)
        if pos == -1:
            return text[:_SNIPPET_LEN]
        half = _SNIPPET_LEN // 2
        start = max(0, pos - half)
        end = min(len(text), pos + len(query_lower) + half)
        snippet = text[start:end]
        if start > 0:
            snippet = "..." + snippet
        if end < len(text):
            snippet = snippet + "..."
        return snippet

    def _build_context(
        self,
        history: List[Dict[str, Any]],
        idx: int,
        direction: int,
        count: int,
        bound: int,
    ) -> List[Dict[str, Any]]:
        """Build context message list — *count* messages before or after
        *idx*, clamped to *bound*."""
        ctx: List[Dict[str, Any]] = []
        for offset in range(1, count + 1):
            target = idx + (direction * offset)
            if target < 0 or target >= len(history) or target < 0:
                break
            if direction < 0 and target < bound:
                break
            if direction > 0 and target > bound:
                break
            msg = history[target]
            preview = self._message_preview(msg)
            ctx.append({"index": target, "role": msg.get("role", ""), "preview": preview})
        # For "before" context, return in chronological order.
        if direction < 0:
            ctx.reverse()
        return ctx

    @staticmethod
    def _message_preview(msg: Dict[str, Any]) -> str:
        """Generate a short preview of a message — first text part or
        tool name, truncated to ``_PREVIEW_LEN`` chars."""
        for part in msg.get("parts", []) or []:
            p_type = part.get("type")
            if p_type == "text":
                text = str(part.get("text", "")).strip().replace("\n", " ")
                return text[:_PREVIEW_LEN]
            elif p_type == "tool_call":
                return f"tool_call:{part.get('tool_name', '')}"[:_PREVIEW_LEN]
            elif p_type == "tool_result":
                result = str(part.get("tool_result", "")).strip().replace("\n", " ")
                return f"tool_result:{result}"[:_PREVIEW_LEN]
            elif p_type == "file":
                file_ref = part.get("file_ref", {}) or {}
                return f"file:{file_ref.get('display_name') or file_ref.get('uri') or 'unknown'}"[
                    :_PREVIEW_LEN
                ]
        return ""

    def _lookup_cache_key(self, part: Dict[str, Any]) -> Optional[str]:
        """Check if a tool_result part has a corresponding
        ``ToolResultCache`` entry.  Returns the cache key or None."""
        cache = getattr(self, "tool_result_cache", None)
        if cache is None:
            return None
        # Use the public _cache OrderedDict if available; fall back to None
        # for caches that don't expose it (e.g. mock caches in tests).
        internal_cache = getattr(cache, "_cache", None)
        if internal_cache is None:
            return None
        result = part.get("tool_result", "")
        tool_name = str(part.get("tool_name", ""))
        # Iterate cache entries and compare result content.
        for key, entry in internal_cache.items():
            if entry.get("tool_name", "") != tool_name:
                continue
            cached_result = entry.get("result", "")
            # Compare serialized form.
            cached_str = (
                cached_result
                if isinstance(cached_result, str)
                else json.dumps(cached_result, default=str)
            )
            result_str = (
                result if isinstance(result, str) else json.dumps(result, default=str)
            )
            if cached_str == result_str:
                return key
        return None


__all__ = ["HistorySearchMixin"]