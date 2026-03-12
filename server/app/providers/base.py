from abc import ABC, abstractmethod
from collections.abc import AsyncIterator


class ProviderAdapter(ABC):
    name: str
    supports_streaming: bool = True
    supports_tools: bool = True
    supports_thinking: bool = False

    @abstractmethod
    async def generate(self, prompt: str, model: str | None = None) -> str:
        raise NotImplementedError

    @abstractmethod
    async def stream(self, prompt: str, model: str | None = None) -> AsyncIterator[str]:
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> bool:
        raise NotImplementedError


    @abstractmethod
    async def list_models(self) -> list[str]:
        raise NotImplementedError
