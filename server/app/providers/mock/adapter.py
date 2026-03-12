from collections.abc import AsyncIterator

from server.app.providers.base import ProviderAdapter


class MockAdapter(ProviderAdapter):
    name = "mock"
    supports_streaming = True
    supports_tools = False
    supports_thinking = False

    async def generate(self, prompt: str, model: str | None = None) -> str:
        model_name = model or "mock-default"
        stage_label = "unknown"
        for line in prompt.splitlines():
            if line.startswith("step="):
                stage_label = line.split("=", 1)[1].strip() or "unknown"
                break
        return (
            f"STAGE_READY::{stage_label}:: "
            f"[mock:{model_name}] completed stage '{stage_label}'"
        )

    async def stream(self, prompt: str, model: str | None = None) -> AsyncIterator[str]:
        yield await self.generate(prompt, model=model)

    async def health_check(self) -> bool:
        return True


    async def list_models(self) -> list[str]:
        return ["mock-default"]
