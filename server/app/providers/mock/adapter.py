from collections.abc import AsyncIterator

from server.app.providers.base import ProviderAdapter


class MockAdapter(ProviderAdapter):
    name = "mock"
    supports_streaming = True
    supports_tools = False
    supports_thinking = False

    async def generate(self, prompt: str, model: str | None = None) -> str:
        model_name = model or "mock-default"
        return f"[mock:{model_name}] {prompt[:120]}"

    async def stream(self, prompt: str, model: str | None = None) -> AsyncIterator[str]:
        yield await self.generate(prompt, model=model)

    async def health_check(self) -> bool:
        return True
