from __future__ import annotations

import json
import os
from typing import Any
from urllib import parse, request

from mu_cli.core.types import Message, ModelResponse, Role, ToolCall, UsageStats


class GeminiProvider:
    name = "gemini"
    MODEL_ALIASES = {
        "gemini-3.1-pro-preview": "gemini-2.5-pro",
        "gemini-3-flash-preview": "gemini-2.5-flash",
    }

    def __init__(
        self,
        model: str = "gemini-2.0-flash",
        api_key: str | None = None,
    ) -> None:
        self.model = model
        self.api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY or GOOGLE_API_KEY is required for the gemini provider")

    @staticmethod
    def _convert_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        if not tools:
            return []
        return [
            {
                "functionDeclarations": [
                    {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool.get("schema", {"type": "object", "properties": {}}),
                    }
                    for tool in tools
                ]
            }
        ]

    def _convert_messages(self, messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
        system_prompt = "\n".join(message.content for message in messages if message.role is Role.SYSTEM).strip()
        contents: list[dict[str, Any]] = []

        for message in messages:
            if message.role is Role.USER:
                contents.append({"role": "user", "parts": [{"text": message.content}]})
            elif message.role is Role.ASSISTANT:
                tool_calls = message.metadata.get("tool_calls", [])
                if tool_calls:
                    parts = [
                        {
                            "functionCall": {
                                "name": item["name"],
                                "args": json.loads(item["arguments"]),
                            }
                        }
                        for item in tool_calls
                    ]
                    contents.append({"role": "model", "parts": parts})
                else:
                    contents.append({"role": "model", "parts": [{"text": message.content}]})
            elif message.role is Role.TOOL_RESULT:
                parts = [
                    {
                        "functionResponse": {
                            "name": message.name or "tool",
                            "response": {"content": message.content},
                        }
                    }
                ]
                contents.append({"role": "user", "parts": parts})

        return system_prompt, contents

    def _resolved_model(self) -> str:
        return self.MODEL_ALIASES.get(self.model, self.model)

    def generate(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        stream: bool = False,
    ) -> ModelResponse:
        _ = stream
        system_prompt, contents = self._convert_messages(messages)

        payload: dict[str, Any] = {"contents": contents}
        if system_prompt:
            payload["system_instruction"] = {"parts": [{"text": system_prompt}]}
        api_tools = self._convert_tools(tools)
        if api_tools:
            payload["tools"] = api_tools

        query = parse.urlencode({"key": self.api_key})
        resolved_model = self._resolved_model()
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{resolved_model}:generateContent"
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

        usage_payload = data.get("usageMetadata", {})
        usage = UsageStats(
            input_tokens=int(usage_payload.get("promptTokenCount", 0)),
            output_tokens=int(usage_payload.get("candidatesTokenCount", 0)),
            total_tokens=int(usage_payload.get("totalTokenCount", 0)),
        )

        parts = data["candidates"][0]["content"].get("parts", [])
        text_parts = [part["text"] for part in parts if "text" in part]
        tool_calls: list[ToolCall] = []
        for index, part in enumerate(parts):
            function_call = part.get("functionCall")
            if not function_call:
                continue
            tool_calls.append(
                ToolCall(
                    name=function_call.get("name", ""),
                    args=function_call.get("args", {}),
                    call_id=f"gemini_call_{index}",
                )
            )

        content = "\n".join(text_parts).strip() or ("Calling tool..." if tool_calls else "")
        return ModelResponse(
            message=Message(role=Role.ASSISTANT, content=content),
            tool_calls=tool_calls,
            usage=usage,
        )
