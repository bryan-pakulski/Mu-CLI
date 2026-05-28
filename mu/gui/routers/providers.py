"""Provider + model discovery (no session required)."""

from __future__ import annotations

import os
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException

router = APIRouter()

KNOWN_PROVIDERS: List[str] = ["openai", "gemini", "ollama"]


def _safe_init(name: str, ollama_host: str | None = None):
    try:
        if name == "ollama":
            from providers.ollama import OllamaProvider

            return OllamaProvider(model_name="", host=ollama_host)
        if name == "gemini":
            from providers.gemini import GeminiProvider

            return GeminiProvider(model_name="")
        if name == "openai":
            from providers.openai import OpenAIProvider

            return OpenAIProvider(model_name="")
    except Exception:
        return None
    return None


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
