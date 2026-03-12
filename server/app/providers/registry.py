from server.app.core.config import is_test_mode_enabled
from server.app.providers.mock.adapter import MockAdapter
from server.app.providers.ollama.adapter import OllamaAdapter


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers = {
            "ollama": OllamaAdapter(),
            "mock": MockAdapter(),
        }

    def _is_enabled(self, name: str) -> bool:
        if name == "mock":
            return is_test_mode_enabled()
        return True

    def list_providers(self) -> list:
        return [
            provider
            for name, provider in self._providers.items()
            if self._is_enabled(name)
        ]

    def get(self, name: str):
        if name not in self._providers or not self._is_enabled(name):
            raise KeyError(f"Unknown provider: {name}")
        return self._providers[name]


provider_registry = ProviderRegistry()
