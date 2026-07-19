"""Research-mode introspection.

Surfaces the CitationManager's source list and the session's task_memory
entries (filtered to research-relevant tags) so the GUI can show a
sortable source table and saved findings panel.

CitationManager is process-global — it tracks sources for whoever owns
the Python process (GUI daemon). If the user also runs mucli in TUI
mode separately, those sources won't appear here. The footnote makes
this limitation visible rather than hiding it.

Read-only — mutations (adding sources, reframing the question) go
through the chat path so the agent's chain-of-evidence stays coherent.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Request

router = APIRouter()
_logger = logging.getLogger(__name__)


def _source_to_dict(source) -> Dict[str, Any]:
    return {
        "id": source.id,
        "title": source.title,
        "url": source.url,
        "source_type": source.source_type.value if hasattr(source.source_type, "value") else str(source.source_type),
        "authors": list(source.authors or []),
        "date": source.date,
        "accessed_date": source.accessed_date,
        "credibility_score": round(source.credibility_score, 2),
        "metadata": source.metadata or {},
    }


def _memory_entry_to_dict(entry) -> Dict[str, Any]:
    return {
        "id": entry.id,
        "content": entry.content,
        "tags": list(entry.tags or []),
        "source": entry.source,
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
        "hits": entry.hits,
    }


@router.get("/state")
async def get_research_state(request: Request) -> Dict[str, Any]:
    session = request.app.state.session_by_name()
    if session is None:
        return {
            "active": False,
            "sources": [],
            "source_count": 0,
            "bibliography": "",
            "findings": [],
            "finding_count": 0,
        }
    sm = session.session_manager

    # Sources from the process-global CitationManager.
    sources: List[Dict[str, Any]] = []
    bibliography = ""
    try:
        from utils.citation_manager import get_citation_manager
        cm = get_citation_manager()
        sources = [_source_to_dict(s) for s in cm.get_all_sources()]
        if sources:
            bibliography = cm.compile_bibliography()
    except Exception as exc:
        _logger.warning("research: citation manager unavailable: %s", exc)

    # Findings from task_memory. Research findings don't have a
    # standardized tag, so surface all entries — the panel can filter
    # client-side by tag chips.
    findings: List[Dict[str, Any]] = []
    try:
        entries = sm.task_memory.list_entries(limit=50)
        findings = [_memory_entry_to_dict(e) for e in entries]
    except Exception as exc:
        _logger.warning("research: task_memory unavailable: %s", exc)

    return {
        "active": True,
        "sources": sources,
        "source_count": len(sources),
        "bibliography": bibliography,
        "findings": findings,
        "finding_count": len(findings),
    }
