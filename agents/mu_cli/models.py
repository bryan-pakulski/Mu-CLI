from __future__ import annotations

MODELS_BY_PROVIDER: dict[str, list[str]] = {
    "openai": ["gpt-4o-mini", "gpt-4.1-mini", "gpt-4.1"],
    "gemini": [
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
        "gemini-1.5-pro",
        "gemini-1.5-flash",
    ],
    "echo": ["echo"],
}


def get_models(provider: str) -> list[str]:
    return MODELS_BY_PROVIDER.get(provider, [])
