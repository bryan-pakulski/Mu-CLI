from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mu_cli.core.types import Message, Role


@dataclass(slots=True)
class ContextAssembly:
    text: str
    stats: dict[str, int]


def _clip(text: str, limit: int) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)] + "…"


def _importance_score(message: Message, index: int, total: int) -> int:
    text = str(message.content or "")
    tokens = text.lower()
    score = 0
    score += min(40, len(text) // 20)
    if message.role is Role.TOOL_RESULT:
        score += 35
    if message.role is Role.ASSISTANT:
        score += 12
    if message.role is Role.USER:
        score += 8
    if any(flag in tokens for flag in ("error", "failed", "failure", "exception", "blocker", "verify", "evidence", "test")):
        score += 25
    recency_bonus = max(0, (index - max(0, total - 10)))
    score += recency_bonus
    return score


def assemble_context_block(messages: list[Message], summary_index: list[dict[str, Any]], *, max_chars: int = 4000) -> ContextAssembly:
    budget = max(800, int(max_chars or 0))
    pinned: list[str] = []
    active_rows: list[tuple[int, str]] = []
    archived: list[str] = []

    for message in messages:
        if message.role is not Role.SYSTEM:
            continue
        kind = str((message.metadata or {}).get("kind") or "")
        if kind.startswith("skill:") or kind in {"tooling_enforcement", "planning", "research_mode", "workspace_summary"}:
            snippet = _clip(str(message.content or ""), 240)
            if snippet:
                pinned.append(f"- {kind or 'system'}: {snippet}")
        if len(pinned) >= 4:
            break

    non_system = [m for m in messages if m.role is not Role.SYSTEM]
    total_non_system = len(non_system)
    for idx, message in enumerate(non_system):
        body = _clip(str(message.content or ""), 220)
        if not body:
            continue
        label = message.role.value
        score = _importance_score(message, idx, total_non_system)
        active_rows.append((score, f"- {label}: {body}"))

    active_rows.sort(key=lambda row: row[0], reverse=True)
    active = [row[1] for row in active_rows[:6]]

    for item in (summary_index or [])[-4:]:
        if not isinstance(item, dict):
            continue
        topics = _clip(str(item.get("topics") or ""), 80)
        summary = _clip(str(item.get("summary") or ""), 220)
        if summary:
            archived.append(f"- {topics or 'summary'}: {summary}")

    sections: list[str] = []
    if pinned:
        sections.append("Pinned instructions:\n" + "\n".join(pinned))
    if active:
        sections.append("Active working memory (importance-ranked):\n" + "\n".join(active))
    if archived:
        sections.append("Archived summaries:\n" + "\n".join(archived))

    text = ""
    if sections:
        text = "\n\nContext memory snapshot:\n" + "\n\n".join(sections)
    if len(text) > budget:
        text = _clip(text, budget)

    return ContextAssembly(
        text=text,
        stats={
            "max_chars": budget,
            "actual_chars": len(text),
            "pinned_count": len(pinned),
            "active_count": len(active),
            "archived_count": len(archived),
            "importance_ranked": 1,
        },
    )
