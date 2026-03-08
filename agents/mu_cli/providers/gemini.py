from __future__ import annotations

import json
import os
from typing import Any
from urllib import parse, request

from mu_cli.core.types import Message, ModelResponse, Role


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        model: str = "gemini-2.0-flash",
        api_key: str | None = None,
    ) -> None:
        self.model = model
        self.api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY or GOOGLE_API_KEY is required for the gemini provider")

    def generate(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        stream: bool = False,
    ) -> ModelResponse:
        _ = (tools, stream)
        system_prompt = "\n".join(m.content for m in messages if m.role is Role.SYSTEM).strip()

        contents = [
            {
                "role": "user" if message.role is Role.USER else "model",
                "parts": [{"text": message.content}],
            }
            for message in messages
            if message.role in {Role.USER, Role.ASSISTANT}
        ]

        payload: dict[str, Any] = {"contents": contents}
        if system_prompt:
            payload["system_instruction"] = {"parts": [{"text": system_prompt}]}

        query = parse.urlencode({"key": self.api_key})
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
            f"?{query}"
        )
        req = request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with request.urlopen(req, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))

        content = data["candidates"][0]["content"]["parts"][0]["text"]
        return ModelResponse(message=Message(role=Role.ASSISTANT, content=content))
