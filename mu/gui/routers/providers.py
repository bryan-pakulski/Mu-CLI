"""Provider + model discovery and switching."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()

KNOWN_PROVIDERS: List[str] = ["openai", "gemini", "ollama"]


def _safe_init(name: str, model_name: str = "", ollama_host: str | None = None):
    try:
        if name == "ollama":
            from providers.ollama import OllamaProvider

            return OllamaProvider(model_name=model_name, host=ollama_host)
        if name == "gemini":
            from providers.gemini import GeminiProvider

            return GeminiProvider(model_name=model_name)
        if name == "openai":
            from providers.openai import OpenAIProvider

            return OpenAIProvider(model_name=model_name)
    except Exception:
        return None
    return None


class SwitchRequest(BaseModel):
    provider: str
    model: str
    ollama_host: Optional[str] = None


@router.get("")
async def list_providers() -> Dict[str, Any]:
    return {
        "providers": [
            {
                "name": "openai",
                "configured": bool(os.environ.get("OPENAI_API_KEY")),
                "requires": "OPENAI_API_KEY",
            },
            {
                "name": "gemini",
                "configured": bool(
                    os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
                ),
                "requires": "GEMINI_API_KEY",
            },
            {
                "name": "ollama",
                "configured": True,
                "requires": "ollama daemon (OLLAMA_HOST optional)",
            },
        ]
    }


@router.get("/{name}/models")
async def list_models(name: str) -> Dict[str, Any]:
    if name not in KNOWN_PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {name}")
    provider = _safe_init(name)
    if provider is None:
        return {"models": [], "error": f"Could not initialise provider '{name}'"}
    try:
        models = sorted(
            provider.get_available_models() or [],
            key=lambda m: str(m).lower(),
        )
        return {"models": list(models)}
    except Exception as exc:
        return {"models": [], "error": str(exc)}


@router.get("/current")
async def current_provider(request: Request) -> Dict[str, Any]:
    """Return the active session's current provider and model."""
    session = request.app.state.session_by_name()
    if session is None:
        return {"provider": None, "model": None}
    cfg = session.session_manager.provider_config
    return {"provider": cfg.get("provider"), "model": cfg.get("model")}


@router.post("/switch")
async def switch_provider(req: SwitchRequest, request: Request) -> Dict[str, Any]:
    """Hot-swap the active session's provider and model."""
    if req.provider not in KNOWN_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider: {req.provider}. Known: {KNOWN_PROVIDERS}",
        )

    session = request.app.state.session_by_name()
    if session is None:
        raise HTTPException(status_code=409, detail="No active session to switch.")

    provider = _safe_init(req.provider, req.model, req.ollama_host)
    if provider is None:
        raise HTTPException(
            status_code=500,
            detail=f"Could not initialise provider '{req.provider}' with model '{req.model}'. "
            "Check API keys / daemon.",
        )

    # Hot-swap on the session
    session.provider = provider
    session.session_manager.provider_config = {
        "provider": req.provider,
        "model": req.model,
    }

    return {"ok": True, "provider": req.provider, "model": req.model}