"""History summarization and token-budget rolling.

`HistoryMixin` factors the rolling-summary algorithm out of `SessionManager`.
The methods operate on three instance attributes the host class is
expected to provide:

  * `history`              — list[dict] of message dicts
  * `summary_anchor`       — int index; everything < anchor is summarized
  * `conversation_summary` — str rolling summary

The mixin is a plain class with no `__init__`; consumers either inherit
or compose. `SessionManager` inherits.

Algorithm — `roll_history_summary_to_token_budget`:
  1. Estimate runtime tokens for messages[anchor:]
  2. If under budget, return False
  3. Try `roll_history_summary(keep_recent=...)` — moves a block of older
     messages into `conversation_summary`, advancing `anchor`.
  4. If no rolling possible, call `_degrade_oldest_runtime_payload` —
     truncates the oldest oversized text or tool_result part to a fixed
     character cap, returning True if it changed anything.
  5. Repeat up to `max_passes` times.

Token estimate: `len(text) / 4` per part field (chars→tokens approximation).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def _shorten_tool_args(args: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Match `core.session._shorten_tool_args` exactly so the summary
    output is byte-identical to the pre-extraction format.
    """
    if not args:
        return {}
    if not isinstance(args, dict):
        return {"_raw_args": str(args)}
    shortened = args.copy()
    for key in ["content", "diff"]:
        if (
            key in shortened
            and isinstance(shortened[key], str)
            and len(shortened[key]) > 100
        ):
            shortened[key] = f"({len(shortened[key])} chars)"
    return shortened


class HistoryMixin:
    """History-summarization methods. Host must supply `history`,
    `summary_anchor`, and `conversation_summary` as instance attributes.
    """

    # --------------------------------------------------------- summarization

    def _summarize_history_batch(self, entries: List[Dict[str, Any]]) -> str:
        lines = [self._summarize_history_message(entry) for entry in entries]
        return "\n".join(line for line in lines if line)

    def _summarize_history_message(self, entry: Dict[str, Any]) -> str:
        role = str(entry.get("role", "message"))
        parts: List[str] = []
        for part in entry.get("parts", []):
            part_type = part.get("type")
            if part_type == "text":
                text = str(part.get("text", "")).strip().replace("\n", " ")
                if text:
                    parts.append(text[:140])
            elif part_type == "tool_call":
                parts.append(
                    f"tool_call:{part.get('tool_name')} "
                    f"args={_shorten_tool_args(part.get('tool_args', {}))}"
                )
            elif part_type == "tool_result":
                result = str(part.get("tool_result", "")).strip().replace("\n", " ")
                if len(result) > 140:
                    result = f"{result[:137]}..."
                if result:
                    parts.append(
                        f"tool_result:{part.get('tool_name', 'tool')} => {result}"
                    )
            elif part_type == "file":
                file_ref = part.get("file_ref", {})
                parts.append(
                    f"file:{file_ref.get('display_name') or file_ref.get('uri') or 'unknown'}"
                )

        if not parts:
            return f"- {role}: [no serializable content]"
        return f"- {role}: " + " | ".join(parts)

    def _clip_conversation_summary(self, limit: int = 4_000) -> None:
        if len(self.conversation_summary) <= limit:
            return
        clipped = self.conversation_summary[-limit:].lstrip()
        newline_index = clipped.find("\n")
        if newline_index > 0:
            clipped = clipped[newline_index + 1 :]
        self.conversation_summary = (
            f"[conversation_summary_truncated_to_last_{limit}_chars]\n{clipped}"
        ).strip()

    # ----------------------------------------------------- token estimation

    @staticmethod
    def _estimate_tokens_from_text(text: Any) -> int:
        raw = str(text or "")
        if not raw:
            return 0
        return max(1, int(len(raw) / 4))

    def _estimate_message_tokens(self, message: Dict[str, Any]) -> int:
        role = str(message.get("role", "") or "")
        total = 3 + self._estimate_tokens_from_text(role)
        for part in message.get("parts", []):
            part_type = str(part.get("type", "") or "")
            total += self._estimate_tokens_from_text(part_type)
            if part_type == "text":
                total += self._estimate_tokens_from_text(part.get("text", ""))
            elif part_type == "tool_call":
                total += self._estimate_tokens_from_text(part.get("tool_name", ""))
                total += self._estimate_tokens_from_text(
                    json.dumps(part.get("tool_args", {}), default=str)
                )
            elif part_type == "tool_result":
                total += self._estimate_tokens_from_text(part.get("tool_name", ""))
                total += self._estimate_tokens_from_text(
                    json.dumps(part.get("tool_result", ""), default=str)
                )
            elif part_type == "file":
                file_ref = part.get("file_ref", {}) or {}
                total += self._estimate_tokens_from_text(
                    file_ref.get("display_name") or file_ref.get("uri") or ""
                )
        return total

    def estimate_runtime_history_tokens(
        self, start_index: Optional[int] = None
    ) -> int:
        start = (
            self.summary_anchor if start_index is None else max(0, int(start_index))
        )
        return sum(
            self._estimate_message_tokens(message) for message in self.history[start:]
        )

    # ------------------------------------------------------ rolling summary

    def roll_history_summary(self, keep_recent: int) -> bool:
        keep_recent = max(1, int(keep_recent or 1))
        if self.summary_anchor > len(self.history):
            self.summary_anchor = 0
        unsummarized_count = len(self.history) - self.summary_anchor
        if unsummarized_count <= keep_recent:
            return False

        target_anchor = len(self.history) - keep_recent
        # Advance target to the next 'user' boundary so we don't split a
        # mid-turn assistant/tool group.
        for idx in range(target_anchor, len(self.history)):
            if self.history[idx].get("role") == "user":
                target_anchor = idx
                break

        if target_anchor <= self.summary_anchor:
            return False

        summary_batch = self._summarize_history_batch(
            self.history[self.summary_anchor : target_anchor]
        )
        if not summary_batch:
            self.summary_anchor = target_anchor
            return True

        header = (
            f"### Summarized conversation through message {target_anchor}\n"
            if not self.conversation_summary
            else f"\n### Summarized conversation through message {target_anchor}\n"
        )
        self.conversation_summary = (
            f"{self.conversation_summary}{header}{summary_batch}".strip()
        )
        self._clip_conversation_summary()
        self.summary_anchor = target_anchor
        return True

    def roll_history_summary_to_token_budget(
        self,
        token_budget: int,
        *,
        keep_recent: int = 12,
        max_passes: int = 8,
    ) -> bool:
        token_budget = max(1, int(token_budget or 1))
        changed = False
        for _ in range(max(1, int(max_passes or 1))):
            if self.estimate_runtime_history_tokens() <= token_budget:
                break
            if self.roll_history_summary(keep_recent=keep_recent):
                changed = True
                continue
            if self._degrade_oldest_runtime_payload():
                changed = True
                continue
            break
        return changed

    def _degrade_oldest_runtime_payload(self, max_chars: int = 4000) -> bool:
        """Fallback budget guard: clip the oldest oversized unsummarized
        part. Returns True if a change was made.

        Iterates messages from `summary_anchor` forward, and within each
        message iterates parts in order. The first text or tool_result
        whose serialized form exceeds `max_chars` is truncated in place.
        """
        if self.summary_anchor > len(self.history):
            self.summary_anchor = 0
        for message in self.history[self.summary_anchor :]:
            parts = message.get("parts", []) or []
            for part in parts:
                p_type = part.get("type")
                if p_type == "text":
                    value = str(part.get("text", "") or "")
                    if len(value) > max_chars:
                        part["text"] = (
                            value[:max_chars].rstrip()
                            + f"\n[truncated_to_{max_chars}_chars_for_context_budget]"
                        )
                        return True
                elif p_type == "tool_result":
                    raw = part.get("tool_result", "")
                    serialized = (
                        json.dumps(raw, default=str)
                        if not isinstance(raw, str)
                        else raw
                    )
                    if len(serialized) > max_chars:
                        clipped = (
                            serialized[:max_chars].rstrip()
                            + f"\n[truncated_to_{max_chars}_chars_for_context_budget]"
                        )
                        part["tool_result"] = clipped
                        return True
        return False


__all__ = ["HistoryMixin"]
