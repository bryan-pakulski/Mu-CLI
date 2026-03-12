from collections.abc import AsyncIterator

import httpx

from server.app.core.config import settings
from server.app.providers.base import ProviderAdapter


class OllamaAdapter(ProviderAdapter):
    name = "ollama"
    supports_streaming = True
    supports_tools = True
    supports_thinking = True

    async def generate(self, prompt: str, model: str | None = None) -> str:
        model_name = model or settings.default_model
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{settings.ollama_base_url}/api/generate",
                json={"model": model_name, "prompt": prompt, "stream": False},
            )
            response.raise_for_status()
            return response.json().get("response", "")

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
