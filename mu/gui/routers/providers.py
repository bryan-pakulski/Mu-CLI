"""Provider + model discovery and switching."""

from __future__ import annotations

import os
import asyncio
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from utils.model_pricing import (
    pricing_catalog,
    reset_pricing_config,
    save_pricing_config,
)

router = APIRouter()

KNOWN_PROVIDERS: List[str] = ["openai", "gemini", "anthropic", "ollama", "local"]


def _safe_init(
    name: str,
    model_name: str = "",
    ollama_host: str | None = None,
    ollama_mode: str | None = None,
    ollama_api_key: str | None = None,
    local_base_url: str | None = None,
    local_api_key: str | None = None,
):
    try:
        if name == "local":
            from providers.local import LocalOpenAIProvider
            return LocalOpenAIProvider(model_name, base_url=local_base_url, api_key=local_api_key)
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
        if name == "anthropic":
            from providers.anthropic import AnthropicProvider

            return AnthropicProvider(model_name=model_name)
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
    local_base_url: Optional[str] = None
    local_api_key: Optional[str] = None


class LocalDiscoveryRequest(BaseModel):
    local_base_url: Optional[str] = None
    local_api_key: Optional[str] = None


def _local_settings(request, overrides=None):
    session = request.app.state.session_by_name()
    variables = getattr(session, "variables", {}) or {}
    result = {key: variables.get(key) for key in ("local_base_url", "local_api_key")}
    if overrides is not None:
        result.update({key: value for key, value in overrides.model_dump().items()
                       if key in result and value is not None})
    return result


async def _discover_local(settings):
    from providers.local import LocalOpenAIProvider
    try:
        provider = LocalOpenAIProvider(**{"base_url": settings.get("local_base_url"),
                                         "api_key": settings.get("local_api_key")})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    try:
        models = await asyncio.to_thread(provider.get_available_models)
        return {"models": models, "error": provider.discovery_error or None, "base_url": provider.BASE_URL}
    finally:
        provider._client.close()


@router.post("/local/discover")
async def discover_local(req: LocalDiscoveryRequest, request: Request):
    # Keys travel in the request body, never a query string or response.
    return await _discover_local(_local_settings(request, req))


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
                "name": "anthropic",
                "configured": bool(
                    os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                ),
                "requires": "ANTHROPIC_API_KEY",
            },
            {
                "name": "ollama",
                "configured": True,
                "cloud_key_set": bool(os.environ.get("OLLAMA_API_KEY")),
                "requires": "ollama daemon (OLLAMA_HOST optional) or OLLAMA_API_KEY for cloud",
            },
            {"name": "local", "label": "Local (OpenAI-compatible)", "configured": True,
             "requires": "OpenAI-compatible model server (default http://127.0.0.1:8080/v1)"},
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
    if name == "local":
        return await _discover_local(_local_settings(request))
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
    from providers.local import DEFAULT_BASE_URL
    settings = _local_settings(request)
    local = {"local_base_url": settings.get("local_base_url") or os.environ.get("LOCAL_LLM_BASE_URL") or DEFAULT_BASE_URL,
             "local_api_key_set": bool(settings.get("local_api_key") or os.environ.get("LOCAL_LLM_API_KEY"))}
    if session is None:
        return {
            **local,
            "provider": None,
            "model": None,
            "ollama_api_key_set": bool(os.environ.get("OLLAMA_API_KEY")),
        }
    cfg = session.session_manager.provider_config
    from mu.agent.retry import EFFORT_LEVELS
    return {
        **local,
        "provider": cfg.get("provider"),
        "model": cfg.get("model"),
        "ollama_api_key_set": bool(
            session.variables.get("ollama_api_key") or os.environ.get("OLLAMA_API_KEY")
        ),
        # Composer effort picker: "" = provider default; each provider maps the
        # neutral levels to what its API accepts.
        "effort": str(session.variables.get("reasoning_effort") or ""),
        "effort_levels": list(EFFORT_LEVELS),
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

    if not req.model.strip():
        raise HTTPException(status_code=400, detail="Select a model.")
    local = _local_settings(request, req) if req.provider == "local" else {}
    if local:
        from providers.local import normalize_base_url
        try:
            if local.get("local_base_url"):
                local["local_base_url"] = normalize_base_url(local["local_base_url"])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    provider = _safe_init(
        req.provider,
        req.model,
        req.ollama_host,
        req.ollama_mode,
        req.ollama_api_key,
        **local,
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
    if req.provider == "local":
        session.variables.update({key: value for key, value in local.items() if value is not None})
        session.variables["local_base_url"] = provider.BASE_URL
        provider.apply_session_variables(session.variables)
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
