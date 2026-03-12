from server.app.providers.mock.adapter import MockAdapter
from server.app.providers.ollama.adapter import OllamaAdapter


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers = {
            "ollama": OllamaAdapter(),
            "mock": MockAdapter(),
        }

    def list_providers(self) -> list:
        return list(self._providers.values())

    def get(self, name: str):
        if name not in self._providers:
            raise KeyError(f"Unknown provider: {name}")
        return self._providers[name]


provider_registry = ProviderRegistry()
