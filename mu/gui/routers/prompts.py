"""Prompt-response endpoint — unblocks the agent thread."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


@router.get("")
async def list_pending(request: Request):
    return {"pending": request.app.state.prompts.pending()}


@router.post("/{prompt_id}/answer")
async def answer_prompt(prompt_id: str, request: Request, payload: Dict[str, Any]):
    store = request.app.state.prompts
    from .mode_state import surface_actor
    pending = next((p for p in store.pending() if p.get("id") == prompt_id), None)
    if pending and pending.get("requires_desktop") and surface_actor(request) == "user:mobile" and payload.get("approved"):
        raise HTTPException(status_code=403, detail="Review the PoC command and scripts on desktop before approving execution.")
    if not store.answer(prompt_id, payload):
        raise HTTPException(status_code=404, detail="No such pending prompt")
    return {"ok": True}


@router.post("/{prompt_id}/cancel")
async def cancel_prompt(prompt_id: str, request: Request):
    request.app.state.prompts.cancel(prompt_id)
    return {"ok": True}
