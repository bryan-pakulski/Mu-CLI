"""Provider + model discovery and switching."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from utils.model_pricing import (
    pricing_catalog,
    reset_pricing_config,
    save_pricing_config,
)

router = APIRouter()

KNOWN_PROVIDERS: List[str] = ["openai", "gemini", "ollama"]


def _safe_init(
    name: str,
    model_name: str = "",
    ollama_host: str | None = None,
    ollama_mode: str | None = None,
    ollama_api_key: str | None = None,
):
    try:
        if name == "ollama":
            from providers.ollama import OllamaProvider, _resolve_host

            host = ollama_host
            if not host and ollama_mode:
                host = _resolve_host(None, ollama_mode)
            kwargs: Dict[str, Any] = {"model_name": model_name, "host": host}
            if ollama_api_key:
                kwargs["api_key"] = ollama_api_key
            return OllamaProvider(**kwargs)
        if name == "gemini":
            from providers.gemini import GeminiProvider

            return GeminiProvider(model_name=model_name)
        if name == "openai":
            from providers.openai import OpenAIProvider

            return OpenAIProvider(model_name=model_name)
    except Exception:
        return None
    return None


def _ollama_discovery_overrides(request: Request) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    sess = request.app.state.session_by_name()
    sess_vars = getattr(sess, "variables", None) if sess else None
    for key in ("ollama_mode", "ollama_host", "ollama_api_key"):
        qp = request.query_params.get(key)
        if qp:
            out[key] = qp
        elif sess_vars:
            val = sess_vars.get(key)
            if val:
                out[key] = val
    return out


class SwitchRequest(BaseModel):
    provider: str
    model: str
    ollama_host: Optional[str] = None
    ollama_mode: Optional[str] = None
    ollama_api_key: Optional[str] = None


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
                "cloud_key_set": bool(os.environ.get("OLLAMA_API_KEY")),
                "requires": "ollama daemon (OLLAMA_HOST optional) or OLLAMA_API_KEY for cloud",
            },
        ]
    }


@router.get("/pricing")
async def list_model_pricing() -> Dict[str, Any]:
    """Return the live configurable pricing registry."""
    return pricing_catalog()


@router.put("/pricing")
async def update_model_pricing(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and atomically persist the operator pricing override."""
    try:
        return save_pricing_config(payload)
    except (TypeError, ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/pricing/reset")
async def reset_model_pricing() -> Dict[str, Any]:
    """Remove the user override and return to packaged defaults."""
    try:
        return reset_pricing_config()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/{name}/models")
async def list_models(name: str, request: Request) -> Dict[str, Any]:
    if name not in KNOWN_PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {name}")
    overrides = _ollama_discovery_overrides(request) if name == "ollama" else {}
    provider = _safe_init(
        name,
        ollama_host=overrides.get("ollama_host"),
        ollama_mode=overrides.get("ollama_mode"),
        ollama_api_key=overrides.get("ollama_api_key"),
    )
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
    session = request.app.state.session_by_name()
    if session is None:
        return {
            "provider": None,
            "model": None,
            "ollama_api_key_set": bool(os.environ.get("OLLAMA_API_KEY")),
        }
    cfg = session.session_manager.provider_config
    return {
        "provider": cfg.get("provider"),
        "model": cfg.get("model"),
        "ollama_api_key_set": bool(
            session.variables.get("ollama_api_key") or os.environ.get("OLLAMA_API_KEY")
        ),
    }


@router.post("/switch")
async def switch_provider(req: SwitchRequest, request: Request) -> Dict[str, Any]:
    if req.provider not in KNOWN_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider: {req.provider}. Known: {KNOWN_PROVIDERS}",
        )

    session = request.app.state.session_by_name()
    if session is None:
        raise HTTPException(status_code=409, detail="No active session to switch.")

    provider = _safe_init(
        req.provider,
        req.model,
        req.ollama_host,
        req.ollama_mode,
        req.ollama_api_key,
    )
    if provider is None:
        raise HTTPException(
            status_code=500,
            detail=f"Could not initialise provider '{req.provider}' with model '{req.model}'. Check API keys / daemon.",
        )

    session.provider = provider
    session.session_manager.provider_config = {
        "provider": req.provider,
        "model": req.model,
    }
    if req.provider == "ollama":
        if req.ollama_mode:
            session.variables["ollama_mode"] = req.ollama_mode
        if req.ollama_host is not None:
            session.variables["ollama_host"] = req.ollama_host
        if req.ollama_api_key:
            session.variables["ollama_api_key"] = req.ollama_api_key
        try:
            from mucli import sync_provider_settings

            sync_provider_settings(session)
        except ImportError:
            pass
    session.session_manager.save_history()

    return {"ok": True, "provider": req.provider, "model": req.model}
