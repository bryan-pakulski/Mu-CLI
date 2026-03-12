from server.app.providers.ollama.adapter import OllamaAdapter


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers = {"ollama": OllamaAdapter()}

    def list_providers(self) -> list:
        return list(self._providers.values())

    def get(self, name: str):
        return self._providers[name]


provider_registry = ProviderRegistry()
