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


def assemble_context_block(messages: list[Message], summary_index: list[dict[str, Any]], *, max_chars: int = 4000) -> ContextAssembly:
    budget = max(800, int(max_chars or 0))
    pinned: list[str] = []
    active: list[str] = []
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

    recent_non_system = [m for m in messages if m.role is not Role.SYSTEM]
    for message in recent_non_system[-6:]:
        body = _clip(str(message.content or ""), 220)
        if not body:
            continue
        active.append(f"- {message.role.value}: {body}")

    for item in (summary_index or [])[-3:]:
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
        sections.append("Active working memory:\n" + "\n".join(active))
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
        },
    )
