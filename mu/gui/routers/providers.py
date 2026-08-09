"""Provider + model discovery and switching."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from utils.model_pricing import pricing_catalog

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

            # If a mode/key is supplied, resolve the host the same way the
            # running provider will so discovery matches what the user will
            # actually talk to. An explicit `ollama_host` always wins.
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
    """Resolve ollama mode/host/key for model discovery with priority
    query-param → active session variables → env defaults.

    The welcome/new-session modal has no session yet, so it passes the
    chosen mode + key as query params. The in-session settings popout
    omits them and we fall back to the active session's variables so the
    dropdown matches the running provider.
    """
    out: Dict[str, Any] = {}
    sess = request.app.state.session_by_name()
    sess_vars = getattr(sess, "variables", None) if sess else None
    for key in ("ollama_mode", "ollama_host", "ollama_api_key"):
        # Query param wins; else the active session's stored variable.
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
                # Ollama is usable locally without credentials; cloud-key
                # presence is a separate capability for the UI to consume.
                "configured": True,
                "cloud_key_set": bool(os.environ.get("OLLAMA_API_KEY")),
                "requires": "ollama daemon (OLLAMA_HOST optional) or OLLAMA_API_KEY for cloud",
            },
        ]
    }


@router.get("/pricing")
async def list_model_pricing() -> Dict[str, Any]:
    """Return MuCLI's versioned model-cost baseline for every control plane.

    These are estimation/list-rate economics, not an invoice. Ollama local is
    explicitly zero *provider API* cost while host/GPU compute is kept separate;
    Ollama Cloud remains plan-based unless/until a stable token tariff can be
    attributed safely.
    """
    return pricing_catalog()


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
    """Return the active session's current provider and model."""
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
        # Deliberately expose presence, never the secret. This includes keys
        # supplied by the environment and per-session/CLI configuration.
        "ollama_api_key_set": bool(
            session.variables.get("ollama_api_key") or os.environ.get("OLLAMA_API_KEY")
        ),
    }


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
            detail=f"Could not initialise provider '{req.provider}' with model '{req.model}'. "
            "Check API keys / daemon.",
        )

    # Hot-swap on the session
    session.provider = provider
    session.session_manager.provider_config = {
        "provider": req.provider,
        "model": req.model,
    }
    # Persist the ollama mode/host/key the user chose alongside the
    # provider config, so a reload restores the same endpoint + auth.
    if req.provider == "ollama":
        if req.ollama_mode:
            session.variables["ollama_mode"] = req.ollama_mode
        if req.ollama_host is not None:
            session.variables["ollama_host"] = req.ollama_host
        if req.ollama_api_key:
            session.variables["ollama_api_key"] = req.ollama_api_key
        # Re-apply so the freshly-built provider picks up mode + key
        # (the constructor only saw host/mode/key args, but binding the
        # variables dict keeps `/set` and future syncs consistent).
        try:
            from mucli import sync_provider_settings

            sync_provider_settings(session)
        except ImportError:
            pass
    # Persist to disk so reload restores the selected model + ollama vars.
    session.session_manager.save_history()

    return {"ok": True, "provider": req.provider, "model": req.model}
