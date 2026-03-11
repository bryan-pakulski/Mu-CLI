from __future__ import annotations

import json
import os
from typing import Any
from urllib import parse, request

from mu_cli.core.types import Message, ModelResponse, Role, ToolCall, UsageStats


class OllamaProvider:
    name = "ollama"

    def __init__(self, model: str = "llama3.2", api_key: str | None = None, host: str | None = None) -> None:
        _ = api_key
        self.model = model
        self.host = (host or os.getenv("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")

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

    def _url(self, path: str) -> str:
        return parse.urljoin(self.host + "/", path.lstrip("/"))

    def generate(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        stream: bool = False,
    ) -> ModelResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._convert_messages(messages),
            "stream": bool(stream),
        }
        api_tools = self._convert_tools(tools)
        if api_tools:
            payload["tools"] = api_tools

        req = request.Request(
            self._url("/api/chat"),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with request.urlopen(req, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))

        usage = UsageStats(
            input_tokens=int(data.get("prompt_eval_count", 0) or 0),
            output_tokens=int(data.get("eval_count", 0) or 0),
            total_tokens=int((data.get("prompt_eval_count", 0) or 0) + (data.get("eval_count", 0) or 0)),
        )

        message_payload = data.get("message", {})
        content = str(message_payload.get("content") or "")
        tool_calls: list[ToolCall] = []
        for index, call in enumerate(message_payload.get("tool_calls", [])):
            function_blob = call.get("function", {})
            args_blob = function_blob.get("arguments", {})
            parsed_args: dict[str, Any]
            if isinstance(args_blob, str):
                try:
                    parsed_args = json.loads(args_blob)
                except json.JSONDecodeError:
                    parsed_args = {}
            elif isinstance(args_blob, dict):
                parsed_args = args_blob
            else:
                parsed_args = {}
            tool_calls.append(
                ToolCall(
                    name=str(function_blob.get("name", "")),
                    args=parsed_args,
                    call_id=str(call.get("id") or f"ollama_call_{index}"),
                )
            )

        return ModelResponse(
            message=Message(role=Role.ASSISTANT, content=content or "Calling tool..."),
            tool_calls=tool_calls,
            usage=usage,
        )
