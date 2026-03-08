from __future__ import annotations

import json
import os
from typing import Any
from urllib import request

from mu_cli.core.types import Message, ModelResponse, Role, ToolCall, UsageStats


class OpenAIProvider:
    name = "openai"

    def __init__(self, model: str = "gpt-4o-mini", api_key: str | None = None) -> None:
        self.model = model
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY is required for the openai provider")

    def _convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for message in messages:
            if message.role is Role.SYSTEM:
                converted.append({"role": "system", "content": message.content})
            elif message.role is Role.USER:
                converted.append({"role": "user", "content": message.content})
            elif message.role is Role.ASSISTANT:
                payload: dict[str, Any] = {"role": "assistant", "content": message.content}
                tool_calls = message.metadata.get("tool_calls")
                if tool_calls:
                    payload["tool_calls"] = [
                        {
                            "id": item.get("id") or "call_generated",
                            "type": "function",
                            "function": {
                                "name": item["name"],
                                "arguments": item["arguments"],
                            },
                        }
                        for item in tool_calls
                    ]
                converted.append(payload)
            elif message.role is Role.TOOL_RESULT:
                converted.append(
                    {
                        "role": "tool",
                        "tool_call_id": message.metadata.get("tool_call_id", "call_generated"),
                        "content": message.content,
                    }
                )
        return converted

    @staticmethod
    def _convert_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        if not tools:
            return []
        return [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("schema", {"type": "object", "properties": {}}),
                },
            }
            for tool in tools
        ]

    def generate(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        stream: bool = False,
    ) -> ModelResponse:
        _ = stream
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._convert_messages(messages),
        }
        api_tools = self._convert_tools(tools)
        if api_tools:
            payload["tools"] = api_tools
            payload["tool_choice"] = "auto"

        req = request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with request.urlopen(req, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))

        usage_payload = data.get("usage", {})
        usage = UsageStats(
            input_tokens=int(usage_payload.get("prompt_tokens", 0)),
            output_tokens=int(usage_payload.get("completion_tokens", 0)),
            total_tokens=int(usage_payload.get("total_tokens", 0)),
        )

        message_payload = data["choices"][0]["message"]
        content = message_payload.get("content") or ""
        tool_calls: list[ToolCall] = []
        for call in message_payload.get("tool_calls", []):
            if call.get("type") != "function":
                continue
            function_blob = call.get("function", {})
            args_blob = function_blob.get("arguments", "{}")
            try:
                parsed_args = json.loads(args_blob)
            except json.JSONDecodeError:
                parsed_args = {}
            tool_calls.append(
                ToolCall(
                    name=function_blob.get("name", ""),
                    args=parsed_args,
                    call_id=call.get("id"),
                )
            )

        return ModelResponse(
            message=Message(role=Role.ASSISTANT, content=content or "Calling tool..."),
            tool_calls=tool_calls,
            usage=usage,
        )
