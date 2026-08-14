"""Versioned API for the shared cross-session Memory Ledger."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request

from ..deps import require_session
from mu.memory.service import MemoryConflictError, MemoryRejectedError

router = APIRouter()


def _service(session):
    return session.get_durable_memory_service()


def _assert_visible(session, item) -> None:
    if item is None:
        raise HTTPException(status_code=404, detail="memory not found")
    eligible = {
        (scope["type"], scope["key"])
        for scope in _service(session).resolve_context(session).eligible()
    }
    if (item.scope_type, item.scope_key) not in eligible:
        raise HTTPException(status_code=404, detail="memory not found in active scopes")


async def _changed(request: Request, session, memory_id: str, action: str) -> None:
    await request.app.state.bus.publish(
        {
            "kind": "memory_updated",
            "session_name": session.session_manager.current_session_name,
            "memory_id": memory_id,
            "action": action,
        }
    )


@router.get("/memories")
async def list_memories(
    session=Depends(require_session),
    q: str = Query(default=""),
    lifecycle: Optional[str] = Query(default=None),
    kind: Optional[str] = Query(default=None),
    scope: str = Query(default="all"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> Dict[str, Any]:
    service = _service(session)
    items = service.list_for_session(
        session,
        query=q,
        lifecycle=lifecycle,
        kind=kind,
        scope=scope,
        limit=limit,
        offset=offset,
    )
    return {
        "memories": [item.to_dict() for item in items],
        "stats": service.stats_for_session(session),
        "query": q,
        "limit": limit,
        "offset": offset,
    }


@router.post("/memories")
async def create_memory(
    request: Request,
    payload: Dict[str, Any] = Body(...),
    session=Depends(require_session),
) -> Dict[str, Any]:
    try:
        item, created = _service(session).remember(
            session,
            str(payload.get("statement", "") or ""),
            kind=str(payload.get("kind", "observation") or "observation"),
            scope=str(payload.get("scope", "auto") or "auto"),
            tags=payload.get("tags") or [],
            actor="user",
            trust_origin="user_explicit",
            verification="user_confirmed",
            confidence=float(payload.get("confidence", 1.0) or 1.0),
            sensitivity=str(payload.get("sensitivity", "normal") or "normal"),
            egress_policy=str(payload.get("egress_policy", "any") or "any"),
            pinned=bool(payload.get("pinned", False)),
            supersedes_id=str(payload.get("supersedes_id", "") or ""),
            reason=str(payload.get("reason", "Memory Center capture") or ""),
        )
    except (ValueError, MemoryRejectedError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _changed(request, session, item.id, "created" if created else "reinforced")
    return {"ok": True, "created": created, "memory": item.to_dict()}


@router.get("/memories/{memory_id}")
async def get_memory(
    memory_id: str, session=Depends(require_session)
) -> Dict[str, Any]:
    service = _service(session)
    item = service.ledger.get(memory_id)
    _assert_visible(session, item)
    return {
        "memory": item.to_dict(),
        "events": service.ledger.events(memory_id=memory_id, limit=200),
        "revisions": service.ledger.revisions(memory_id, limit=200),
        "graph": service.ledger.graph(memory_id),
    }


@router.patch("/memories/{memory_id}")
async def revise_memory(
    memory_id: str,
    request: Request,
    payload: Dict[str, Any] = Body(...),
    session=Depends(require_session),
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
) -> Dict[str, Any]:
    service = _service(session)
    current = service.ledger.get(memory_id)
    _assert_visible(session, current)
    if not if_match:
        raise HTTPException(
            status_code=428, detail="If-Match is required for memory edits"
        )
    supplied = if_match.strip().strip('"')
    if supplied != current.etag:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "memory changed since it was opened",
                "current": current.to_dict(),
            },
        )
    expected_version = current.version
    changes = dict(payload.get("changes") or payload)
    changes.pop("reason", None)
    try:
        item = service.revise_for_session(
            session,
            memory_id,
            changes,
            expected_version=expected_version,
            actor="user",
            reason=str(payload.get("reason", "Memory Center edit") or ""),
        )
    except MemoryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _changed(request, session, item.id, "revised")
    return {"ok": True, "memory": item.to_dict()}


@router.post("/memories/{memory_id}/actions")
async def memory_action(
    memory_id: str,
    request: Request,
    payload: Dict[str, Any] = Body(...),
    session=Depends(require_session),
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
) -> Dict[str, Any]:
    service = _service(session)
    current = service.ledger.get(memory_id)
    _assert_visible(session, current)
    if not if_match:
        raise HTTPException(
            status_code=428, detail="If-Match is required for memory actions"
        )
    supplied = if_match.strip().strip('"')
    if supplied != current.etag:
        raise HTTPException(
            status_code=409, detail="memory changed since it was opened"
        )
    expected_version = current.version
    action = str(payload.get("action", "") or "").strip().lower()
    try:
        item = service.ledger.action(
            memory_id,
            action,
            actor="user",
            reason=str(payload.get("reason", f"Memory Center {action}") or ""),
            expected_version=expected_version,
        )
    except MemoryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _changed(request, session, item.id, action)
    return {"ok": True, "memory": item.to_dict()}


@router.get("/memory-events")
async def memory_events(
    memory_id: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=1000),
    session=Depends(require_session),
) -> Dict[str, Any]:
    service = _service(session)
    if memory_id:
        _assert_visible(session, service.ledger.get(memory_id))
        scopes = None
    else:
        scopes = [
            (scope["type"], scope["key"])
            for scope in service.resolve_context(session).eligible()
        ]
    return {
        "events": service.ledger.events(memory_id=memory_id, scopes=scopes, limit=limit)
    }


@router.get("/memory-recalls/{receipt_id}")
async def get_recall_receipt(
    receipt_id: str,
    session=Depends(require_session),
) -> Dict[str, Any]:
    target = "" if receipt_id in {"last", "latest"} else receipt_id
    receipt = _service(session).ledger.get_recall(
        target,
        session_name=str(session.session_manager.current_session_name or ""),
    )
    if receipt is None:
        raise HTTPException(status_code=404, detail="recall receipt not found")
    if receipt["session_name"] != str(
        session.session_manager.current_session_name or ""
    ):
        raise HTTPException(status_code=404, detail="recall receipt not found")
    return {"receipt": receipt}


@router.get("/memory-graph/{memory_id}")
async def memory_graph(
    memory_id: str, session=Depends(require_session)
) -> Dict[str, Any]:
    _assert_visible(session, _service(session).ledger.get(memory_id))
    return _service(session).ledger.graph(memory_id)


@router.get("/memory-policy")
async def memory_policy(session=Depends(require_session)) -> Dict[str, Any]:
    return {
        "enabled": bool(session.variables.get("durable_memory_enabled", True)),
        "automatic_capture": bool(
            session.variables.get("durable_memory_auto_capture", True)
        ),
        "approval_required": False,
        "max_items": int(session.variables.get("durable_memory_max_items", 6) or 6),
        "token_budget": int(
            session.variables.get("durable_memory_token_budget", 1200) or 1200
        ),
        "default_scope": str(
            session.variables.get("durable_memory_default_scope", "auto") or "auto"
        ),
        "show_receipts": bool(
            session.variables.get("durable_memory_show_receipts", True)
        ),
        "scopes": _service(session).resolve_context(session).eligible(),
    }
