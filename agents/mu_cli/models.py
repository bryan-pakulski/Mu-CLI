from __future__ import annotations

import json
import os
from typing import Any
from urllib import parse, request
from urllib.error import URLError

STATIC_MODELS_BY_PROVIDER: dict[str, list[str]] = {
    "openai": ["gpt-4o-mini", "gpt-4.1-mini", "gpt-4.1"],
    "gemini": [
        "gemini-3.1-pro-preview",
        "gemini-3-flash-preview",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
    ],
    "echo": ["echo"],
    "ollama": ["llama3.2"],
}


def _gemini_api_key(explicit_api_key: str | None = None) -> str | None:
    return explicit_api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")


def _list_gemini_models(api_key: str) -> list[str]:
    query = parse.urlencode({"key": api_key})
    url = f"https://generativelanguage.googleapis.com/v1beta/models?{query}"
    req = request.Request(url, headers={"Content-Type": "application/json"}, method="GET")
    with request.urlopen(req, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8"))

    discovered: list[str] = []
    for model in data.get("models", []):
        if "generateContent" not in model.get("supportedGenerationMethods", []):
            continue
        name = str(model.get("name", ""))
        if not name.startswith("models/gemini-"):
            continue
        discovered.append(name.removeprefix("models/"))

    # Prefer newest Gemini major line first (v3), then keep deterministic ordering.
    return sorted(
        set(discovered),
        key=lambda item: (
            0 if item.startswith("gemini-3") else 1,
            item,
        ),
    )




def _ollama_host() -> str:
    return (os.getenv("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")


def _list_ollama_models() -> list[str]:
    url = f"{_ollama_host()}/api/tags"
    req = request.Request(url, headers={"Content-Type": "application/json"}, method="GET")
    with request.urlopen(req, timeout=10) as response:
        data = json.loads(response.read().decode("utf-8"))

    discovered: list[str] = []
    for model in data.get("models", []):
        name = str(model.get("name", "")).strip()
        if name:
            discovered.append(name)

    return sorted(set(discovered))
def get_models(provider: str, api_key: str | None = None) -> list[str]:
    if provider == "gemini":
        key = _gemini_api_key(api_key)
        if not key:
            return STATIC_MODELS_BY_PROVIDER["gemini"]

        try:
            models = _list_gemini_models(key)
        except Exception:
            return STATIC_MODELS_BY_PROVIDER["gemini"]
        return models or STATIC_MODELS_BY_PROVIDER["gemini"]

    if provider == "ollama":
        try:
            models = _list_ollama_models()
        except (TimeoutError, URLError, OSError, ValueError, json.JSONDecodeError):
            return STATIC_MODELS_BY_PROVIDER["ollama"]
        return models or STATIC_MODELS_BY_PROVIDER["ollama"]

    return STATIC_MODELS_BY_PROVIDER.get(provider, [])


def get_model_catalog(api_keys: dict[str, str | None] | None = None) -> dict[str, list[str]]:
    keys = api_keys or {}
    return {
        provider: get_models(provider, keys.get(provider))
        for provider in STATIC_MODELS_BY_PROVIDER
    }
