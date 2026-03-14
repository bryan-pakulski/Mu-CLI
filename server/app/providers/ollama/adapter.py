from collections.abc import AsyncIterator

import httpx

from server.app.core.config import settings
from server.app.providers.base import ProviderAdapter


def _compact_prompt_for_ollama(prompt: str, max_chars: int) -> str:
    text = str(prompt or "")
    budget = max(2000, int(max_chars or 0))
    if len(text) <= budget:
        return text

    head_budget = min(5000, budget // 3)
    tail_budget = max(1000, budget - head_budget - 32)
    return f"{text[:head_budget]}\n\n...[prompt truncated]...\n\n{text[-tail_budget:]}"


def _ollama_prompt_variants(prompt: str) -> list[str]:
    base = str(prompt or "")
    variants = [base]
    max_chars = max(4000, int(settings.ollama_max_prompt_chars or 18000))
    compact = _compact_prompt_for_ollama(base, max_chars)
    if compact != base:
        variants.append(compact)
    tighter = _compact_prompt_for_ollama(base, max_chars // 2)
    if tighter not in variants:
        variants.append(tighter)
    return variants

class OllamaAdapter(ProviderAdapter):
    name = "ollama"
    supports_streaming = True
    supports_tools = True
    supports_thinking = True

    async def generate(self, prompt: str, model: str | None = None) -> str:
        model_name = model or settings.default_model
        errors: list[str] = []
        variants = _ollama_prompt_variants(prompt)

        async with httpx.AsyncClient(timeout=90.0) as client:
            for variant in variants:
                try:
                    response = await client.post(
                        f"{settings.ollama_base_url}/api/generate",
                        json={"model": model_name, "prompt": variant, "stream": False},
                    )
                    response.raise_for_status()
                    return response.json().get("response", "")
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code if exc.response is not None else 0
                    errors.append(f"status={status}")
                    if status >= 500:
                        continue
                    raise

        raise RuntimeError(
            "ollama generate failed after prompt compaction variants; "
            f"model={model_name}; errors={errors}"
        )

    async def stream(self, prompt: str, model: str | None = None) -> AsyncIterator[str]:
        model_name = model or settings.default_model
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST",
                f"{settings.ollama_base_url}/api/generate",
                json={"model": model_name, "prompt": prompt, "stream": True},
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    yield line

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(f"{settings.ollama_base_url}/api/tags")
            return response.status_code == 200
        except httpx.HTTPError:
            return False


    async def list_models(self) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{settings.ollama_base_url}/api/tags")
                response.raise_for_status()
            models = [item.get("name") for item in response.json().get("models", []) if item.get("name")]
            return models
        except httpx.HTTPError:
            return []
