from __future__ import annotations

MODELS_BY_PROVIDER: dict[str, list[str]] = {
    "openai": ["gpt-4o-mini", "gpt-4.1-mini", "gpt-4.1"],
    "gemini": [
        "gemini-3.1-pro-preview",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-3-flash-preview",
    ],
    "echo": ["echo"],
}


def get_models(provider: str) -> list[str]:
    return MODELS_BY_PROVIDER.get(provider, [])
