from dataclasses import dataclass

from server.app.providers.registry import provider_registry


@dataclass
class ProviderResult:
    provider_name: str
    output: str


class ProviderRouter:
    async def generate_with_fallback(
        self,
        prompt: str,
        ordered_providers: list[str],
        model: str | None = None,
        max_retries: int = 2,
    ) -> ProviderResult:
        attempted: list[str] = []
        errors: list[str] = []

        for provider_name in ordered_providers:
            attempted.append(provider_name)
            try:
                provider = provider_registry.get(provider_name)
            except KeyError as exc:
                errors.append(str(exc))
                continue

            for retry in range(max_retries + 1):
                try:
                    output = await provider.generate(prompt=prompt, model=model)
                    return ProviderResult(provider_name=provider_name, output=output)
                except Exception as exc:  # noqa: BLE001
                    errors.append(
                        (
                            f"provider={provider_name} retry={retry} "
                            f"error={exc.__class__.__name__}:{exc}"
                        )
                    )

        raise RuntimeError(
            "all providers failed; "
            f"attempted={attempted}; "
            f"errors={errors}"
        )


provider_router = ProviderRouter()
