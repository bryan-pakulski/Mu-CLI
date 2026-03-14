import httpx
import pytest

from server.app.providers.ollama.adapter import (
    OllamaAdapter,
    _compact_prompt_for_ollama,
    _ollama_prompt_variants,
)


def test_compact_prompt_for_ollama_truncates_when_needed() -> None:
    prompt = "A" * 50000
    compact = _compact_prompt_for_ollama(prompt, 12000)
    assert len(compact) <= 12100
    assert "[prompt truncated]" in compact


def test_ollama_prompt_variants_include_compacted_forms_for_long_prompt() -> None:
    prompt = "B" * 50000
    variants = _ollama_prompt_variants(prompt)
    assert len(variants) >= 2
    assert variants[0] == prompt
    assert all(isinstance(item, str) and item for item in variants)


@pytest.mark.asyncio
async def test_generate_retries_with_compacted_prompt_on_500(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def __init__(self, status_code: int, payload: dict[str, str] | None = None) -> None:
            self.status_code = status_code
            self._payload = payload or {}
            self.request = httpx.Request("POST", "http://localhost:11434/api/generate")

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise httpx.HTTPStatusError("error", request=self.request, response=self)

        def json(self) -> dict[str, str]:
            return self._payload

    class FakeClient:
        posted_prompts: list[str] = []

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def post(self, _: str, json: dict[str, object]) -> FakeResponse:
            prompt = str(json["prompt"])
            self.posted_prompts.append(prompt)
            if len(self.posted_prompts) == 1:
                return FakeResponse(500)
            return FakeResponse(200, {"response": "ok"})

    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: FakeClient())

    adapter = OllamaAdapter()
    result = await adapter.generate("Z" * 50000)
    assert result == "ok"


@pytest.mark.asyncio
async def test_generate_raises_immediately_on_non_retryable_status(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code
            self.request = httpx.Request("POST", "http://localhost:11434/api/generate")

        def raise_for_status(self) -> None:
            raise httpx.HTTPStatusError("bad request", request=self.request, response=self)

    class FakeClient:
        calls = 0

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def post(self, _: str, json: dict[str, object]) -> FakeResponse:
            self.calls += 1
            return FakeResponse(400)

    fake_client = FakeClient()
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: fake_client)

    adapter = OllamaAdapter()
    with pytest.raises(httpx.HTTPStatusError):
        await adapter.generate("tiny")
    assert fake_client.calls == 1
