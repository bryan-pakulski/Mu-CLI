"""Debug-mode introspection.

Surfaces the hypothesis stack (scratchpad entries with kind='hypothesis'),
the current debug target, suspect locations, and durable bug findings
from task_memory so the GUI can pin state while the chat is busy
generating verifications.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Request

router = APIRouter()
_logger = logging.getLogger(__name__)


def _entry_dict(entry) -> Dict[str, Any]:
    return {
        "id": entry.id,
        "content": entry.content,
        "tags": list(entry.tags or []),
        "source": entry.source,
        "kind": getattr(entry, "kind", ""),
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
    }


@router.get("/state")
async def get_debug_state(request: Request) -> Dict[str, Any]:
    session = request.app.state.session_by_name()
    if session is None:
        return {
            "active": False,
            "debug_target": "",
            "hypotheses": [],
            "suspects": [],
            "findings": [],
            "scratchpad_count": 0,
        }
    sm = session.session_manager

    debug_target = str(sm.variables.get("debug_target", "") or "").strip()

    # Hypotheses: scratchpad entries with kind='hypothesis' or tagged
    # 'hypothesis'. Status derived from tags (untested / disproved /
    # supported / confirmed).
    hypotheses: List[Dict[str, Any]] = []
    suspects: List[Dict[str, Any]] = []
    scratchpad_count = 0
    try:
        entries = sm.turn_scratchpad.list_entries(limit=50)
        scratchpad_count = len(entries)
        for e in entries:
            kind = getattr(e, "kind", "") or ""
            tags = [t.lower() for t in (e.tags or [])]
            if kind == "hypothesis" or "hypothesis" in tags:
                status = "untested"
                for t in tags:
                    if t in ("disproved", "supported", "confirmed"):
                        status = t
                hypotheses.append({**_entry_dict(e), "status": status})
            elif kind == "suspect" or "suspect" in tags:
                suspects.append(_entry_dict(e))
    except Exception as exc:
        _logger.warning("debug: scratchpad read failed: %s", exc)

    # Findings: task_memory entries tagged 'debug' or 'bug'.
    findings: List[Dict[str, Any]] = []
    try:
        entries = sm.task_memory.list_entries(limit=20)
        for e in entries:
            tags = [t.lower() for t in (e.tags or [])]
            if "debug" in tags or "bug" in tags or "root-cause" in tags:
                findings.append(_entry_dict(e))
    except Exception as exc:
        _logger.warning("debug: task_memory read failed: %s", exc)

    return {
        "active": True,
        "debug_target": debug_target,
        "hypotheses": hypotheses,
        "suspects": suspects,
        "findings": findings,
        "scratchpad_count": scratchpad_count,
    }
