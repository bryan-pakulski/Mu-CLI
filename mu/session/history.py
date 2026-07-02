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

from providers.base import LLMProvider, Message, MessagePart
from utils.logger import logger

from .helpers import _shorten_tool_args


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

    # ------------------------------------------------- LLM summary generation

    _LLM_SUMMARY_SYSTEM_PROMPT = (
        "You are a conversation summarizer for an AI coding agent. "
        "Summarize the following conversation segment into a structured "
        "summary. Preserve all critical information the agent needs to "
        "continue its work.\n\n"
        "Output EXACTLY these sections, each starting with ###:\n\n"
        "### Task\nThe user's original request or goal. Quote it if short, "
        "paraphrase if long. Never omit.\n\n"
        "### Progress\nWhat has been done so far. List concrete actions, "
        "file changes, tool calls, and their outcomes.\n\n"
        "### Key decisions\nArchitectural or design choices made, with "
        "rationale. Include any rejected approaches and why.\n\n"
        "### Current state\nFiles modified, tests run and their results, "
        "current errors or blockers. Include file paths and function "
        "names verbatim.\n\n"
        "### Open items\nWhat still needs to be done. Be specific — "
        "list the next actionable steps.\n\n"
        "Rules:\n"
        "- Preserve file paths, function names, error messages, and "
        "identifiers VERBATIM.\n"
        "- Do NOT add commentary, headers, or text outside the sections.\n"
        "- If a section has no content, write 'None' — do not skip it.\n"
        "- Keep the summary concise but complete. Target 200-600 words.\n"
    )

    def _generate_llm_summary(
        self,
        provider: Optional[LLMProvider],
        entries: List[Dict[str, Any]],
    ) -> Optional[str]:
        """Generate a structured LLM summary of conversation entries.

        Calls the provider with a summarization prompt and the
        conversation segment rendered as readable text. Returns the
        model's structured summary text, or None on any failure
        (caller falls back to _summarize_history_batch).

        Cost: one non-streaming provider call, no tools, ~1000 output
        tokens. The investment is worth it — mechanical truncation
        loses semantic meaning and causes agents to re-read files
        they already explored (the compaction-loop failure mode).
        """
        if provider is None or not entries:
            return None

        try:
            # Render the conversation segment as readable text for the
            # summarizer. Use _summarize_history_message but with a
            # much larger character budget than the default 140 — the
            # LLM needs enough context to produce a good summary.
            rendered = self._render_entries_for_llm(entries)
            if not rendered.strip():
                return None

            user_prompt = (
                "Summarize this conversation segment:\n\n"
                f"{rendered}"
            )

            messages = [
                Message(
                    role="user",
                    parts=[MessagePart(type="text", text=user_prompt)],
                ),
            ]

            response = provider.generate(
                messages=messages,
                system_prompt=self._LLM_SUMMARY_SYSTEM_PROMPT,
                thinking=False,
                tools=None,
            )

            summary_text = str(response.text or "").strip()
            if not summary_text:
                return None

            # Validate that the summary has the expected structure.
            # If the model didn't produce any ### headers, it probably
            # didn't follow the prompt — fall back to mechanical.
            if "###" not in summary_text:
                logger.warning(
                    "LLM summary missing expected ### sections; "
                    "falling back to mechanical summarization."
                )
                return None

            return summary_text

        except Exception as exc:
            logger.warning(
                "LLM summary generation failed: %s — falling back to "
                "mechanical summarization.",
                exc,
            )
            return None

    def _render_entries_for_llm(
        self, entries: List[Dict[str, Any]]
    ) -> str:
        """Render conversation entries as readable text for the LLM
        summarizer.

        Unlike _summarize_history_message (which truncates to 140
        chars), this preserves much more content — up to 500 chars per
        text part and 300 chars per tool result — so the LLM has
        enough context to produce a meaningful structured summary.
        """
        lines: List[str] = []
        for entry in entries:
            role = str(entry.get("role", "message"))
            parts_text: List[str] = []
            for part in entry.get("parts", []):
                part_type = part.get("type")
                if part_type == "text":
                    text = str(part.get("text", "")).strip().replace("\n", " ")
                    if text:
                        parts_text.append(text[:500])
                elif part_type == "tool_call":
                    parts_text.append(
                        f"tool_call:{part.get('tool_name')} "
                        f"args={_shorten_tool_args(part.get('tool_args', {}))}"
                    )
                elif part_type == "tool_result":
                    result = str(part.get("tool_result", "")).strip().replace("\n", " ")
                    if len(result) > 300:
                        result = result[:297] + "..."
                    if result:
                        parts_text.append(
                            f"tool_result:{part.get('tool_name', 'tool')} => {result}"
                        )
                elif part_type == "file":
                    file_ref = part.get("file_ref", {})
                    parts_text.append(
                        f"file:{file_ref.get('display_name') or file_ref.get('uri') or 'unknown'}"
                    )
            if parts_text:
                lines.append(f"- {role}: " + " | ".join(parts_text))
        return "\n".join(lines)

    def _merge_structured_summary(self, new_summary: str) -> None:
        """Merge a new LLM-generated structured summary into the existing
        ``conversation_summary`` by section.

        Both the existing and new summaries use ``###`` header sections
        (Task, Progress, Key decisions, Current state, Open items).
        Instead of blind-appending (which creates duplicate headers and
        grows unbounded), this method:

          * Parses the existing summary into ``{section_title: content}``.
          * Parses the new summary the same way.
          * For each section: appends new content after the existing
            content, separated by a blank line.
          * Sections in the new summary that don't exist in the old one
            are added outright.
          * Sections in the old summary not touched by the new one are
            preserved unchanged.

        After merge, ``_clip_conversation_summary`` is called with the
        larger 12000-char budget (set in Task 3) to prevent unbounded
        growth while preserving much more context than the old 4000 cap.
        """
        if not new_summary:
            return

        # If existing summary doesn't have ### sections, fall back to
        # blind append — it's a legacy mechanical summary.
        if self.conversation_summary and "###" not in self.conversation_summary:
            self.conversation_summary = (
                f"{self.conversation_summary}\n\n{new_summary}".strip()
            )
            self._clip_conversation_summary()
            return

        def _parse_sections(text: str) -> Dict[str, str]:
            """Split a structured summary into {header: body} pairs."""
            sections: Dict[str, str] = {}
            current_header = None
            current_lines: List[str] = []
            for line in text.splitlines():
                if line.strip().startswith("###"):
                    if current_header is not None:
                        sections[current_header] = "\n".join(current_lines).strip()
                    current_header = line.strip().lstrip("#").strip()
                    current_lines = []
                else:
                    current_lines.append(line)
            if current_header is not None:
                sections[current_header] = "\n".join(current_lines).strip()
            return sections

        existing = _parse_sections(self.conversation_summary or "")
        incoming = _parse_sections(new_summary)

        # Merge: append new content to existing sections, add new sections.
        for header, body in incoming.items():
            if not body or body == "None":
                continue
            if header in existing:
                existing_body = existing[header]
                if existing_body and existing_body != "None":
                    existing[header] = f"{existing_body}\n\n{body}"
                else:
                    existing[header] = body
            else:
                existing[header] = body

        # Reassemble in a canonical section order to keep the summary
        # readable across merges.
        canonical_order = [
            "Task", "Progress", "Key decisions",
            "Current state", "Open items",
        ]
        out_lines: List[str] = []
        for header in canonical_order:
            body = existing.get(header, "").strip()
            if body:
                out_lines.append(f"### {header}")
                out_lines.append(body)
                out_lines.append("")
        # Append any non-canonical sections that exist (future-proofing).
        for header, body in existing.items():
            if header in canonical_order:
                continue
            body = body.strip()
            if body:
                out_lines.append(f"### {header}")
                out_lines.append(body)
                out_lines.append("")

        self.conversation_summary = "\n".join(out_lines).strip()
        self._clip_conversation_summary()

    def _clip_conversation_summary(self, limit: int = 24_000) -> None:
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

    def _active_model(self) -> str:
        """The model whose tokenizer should drive history-token estimates.

        Reads `provider_config['model']` populated by Session at startup
        and refreshed on `/model` / `/provider`. Empty string is fine —
        the estimator falls back to a general-purpose encoder."""
        cfg = getattr(self, "provider_config", None) or {}
        return str(cfg.get("model") or "")

    def _estimate_tokens_from_text(self, text: Any) -> int:
        # Delegate to the shared tiktoken-backed estimator. Driving
        # every token-budget decision through the same function means
        # /memory layer counts, the splash banner, and the compactor
        # trim trigger all agree.
        from utils.token_estimator import estimate_tokens

        return estimate_tokens(text, self._active_model())

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

    def roll_history_summary(
        self, keep_recent: int, provider: Optional[LLMProvider] = None
    ) -> bool:
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

        entries_to_summarize = self.history[self.summary_anchor : target_anchor]

        # LLM-generated structured summary (Claude Code / Pi style).
        # Falls back to mechanical truncation on any failure.
        summary_batch = self._generate_llm_summary(provider, entries_to_summarize)
        if summary_batch is None:
            summary_batch = self._summarize_history_batch(entries_to_summarize)

        if not summary_batch:
            self.summary_anchor = target_anchor
            return True

        # Merge into existing summary. When the summary is LLM-generated
        # it already has ### Task / ### Progress / ### Key decisions
        # sections; use _merge_structured_summary to merge by section
        # instead of blind-append. For mechanical summaries, fall back
        # to the original header-append behavior.
        if "### Task" in summary_batch or "### Progress" in summary_batch:
            self._merge_structured_summary(summary_batch)
        else:
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
        provider: Optional[LLMProvider] = None,
    ) -> bool:
        token_budget = max(1, int(token_budget or 1))
        changed = False
        for _ in range(max(1, int(max_passes or 1))):
            if self.estimate_runtime_history_tokens() <= token_budget:
                break
            if self.roll_history_summary(keep_recent=keep_recent, provider=provider):
                changed = True
                continue
            if self._degrade_oldest_runtime_payload(provider=provider):
                changed = True
                continue
            break
        return changed

    def _degrade_oldest_runtime_payload(
        self,
        max_chars: int = 16_000,
        provider: Optional[LLMProvider] = None,
    ) -> bool:
        """Fallback budget guard: summarize or clip the oldest oversized
        unsummarized part. Returns True if a change was made.

        When a provider is available, calls provider.generate to produce
        an LLM summary of the oversized payload instead of destructively
        truncating it. This preserves semantic meaning — the model can
        still understand what was in the payload even after budget
        pressure.

        When provider is None or the LLM call fails, falls back to
        expanded mechanical truncation (16000 chars, was 8000).
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
                        # Try LLM summarization first.
                        if provider is not None:
                            try:
                                summary = self._summarize_payload_via_llm(
                                    provider, value
                                )
                                if summary and len(summary) < len(value):
                                    part["text"] = summary
                                    return True
                            except Exception as exc:
                                logger.warning(
                                    "LLM payload summarization failed: %s "
                                    "— falling back to mechanical truncation.",
                                    exc,
                                )
                        # Mechanical fallback: expanded from 8000 to 16000.
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
                        # Try LLM summarization first.
                        if provider is not None:
                            try:
                                summary = self._summarize_payload_via_llm(
                                    provider, serialized
                                )
                                if summary and len(summary) < len(serialized):
                                    part["tool_result"] = summary
                                    return True
                            except Exception as exc:
                                logger.warning(
                                    "LLM payload summarization failed: %s "
                                    "— falling back to mechanical truncation.",
                                    exc,
                                )
                        # Mechanical fallback: expanded from 8000 to 16000.
                        clipped = (
                            serialized[:max_chars].rstrip()
                            + f"\n[truncated_to_{max_chars}_chars_for_context_budget]"
                        )
                        part["tool_result"] = clipped
                        return True
        return False

    def _summarize_payload_via_llm(
        self,
        provider: Optional[LLMProvider],
        payload: str,
    ) -> Optional[str]:
        """Generate an LLM summary of an oversized history payload.

        Returns the summary text (which should be shorter than the
        original), or None on any failure (caller falls back to
        mechanical truncation).
        """
        if provider is None or not payload:
            return None

        try:
            system_prompt = (
                "You are a context summarizer for an AI coding agent. "
                "Summarize the following content into a concise but "
                "information-preserving summary. Keep file paths, function "
                "names, error messages, and key findings VERBATIM. "
                "Be concise but complete. Target 200-500 words.\n\n"
                "Output ONLY the summary, no headers or commentary."
            )

            messages = [
                Message(
                    role="user",
                    parts=[MessagePart(type="text", text=payload[:8000])],
                ),
            ]

            response = provider.generate(
                messages=messages,
                system_prompt=system_prompt,
                thinking=False,
                tools=None,
            )

            summary = str(response.text or "").strip()
            if not summary or len(summary) >= len(payload):
                return None
            return summary

        except Exception:
            return None


__all__ = ["HistoryMixin"]
