from __future__ import annotations

import json
import os
from typing import Any, Callable
from urllib import parse, request

from mu_cli.core.types import Message, ModelResponse, Role, ToolCall, UsageStats


StreamCallback = Callable[[dict[str, Any]], None]


class OllamaProvider:
    name = "ollama"

    def __init__(
        self,
        model: str = "llama3.2",
        api_key: str | None = None,
        host: str | None = None,
        stream_callback: StreamCallback | None = None,
        context_window: int | None = None,
    ) -> None:
        _ = api_key
        self.model = model
        self.host = (host or os.getenv("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")
        self.stream_callback = stream_callback
        self.context_window = max(1024, int(context_window or 65536))

    def set_stream_callback(self, callback: StreamCallback | None) -> None:
        self.stream_callback = callback

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
                    normalized_calls = []
                    for item in tool_calls:
                        raw_args = item.get("arguments", {})
                        if isinstance(raw_args, str):
                            try:
                                parsed_args = json.loads(raw_args)
                            except json.JSONDecodeError:
                                parsed_args = {}
                        elif isinstance(raw_args, dict):
                            parsed_args = raw_args
                        else:
                            parsed_args = {}

                        normalized_calls.append(
                            {
                                "id": item.get("id") or "call_generated",
                                "type": "function",
                                "function": {
                                    "name": item["name"],
                                    "arguments": parsed_args,
                                },
                            }
                        )
                    payload["tool_calls"] = normalized_calls
                converted.append(payload)
            elif message.role is Role.TOOL_RESULT:
                converted.append({"role": "tool", "content": message.content})
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

    def _emit_stream_chunk(self, chunk: str) -> None:
        if not chunk or self.stream_callback is None:
            return
        self.stream_callback({"kind": "thinking_output", "chunk": chunk})

    def _parse_tool_calls(self, raw_calls: list[dict[str, Any]]) -> list[ToolCall]:
        tool_calls: list[ToolCall] = []
        for index, call in enumerate(raw_calls):
            function_blob = call.get("function", {})
            args_blob = function_blob.get("arguments", {})
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
        return tool_calls

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
        payload["options"] = {"num_ctx": self.context_window}
        api_tools = self._convert_tools(tools)
        if api_tools:
            payload["tools"] = api_tools

        req = request.Request(
            self._url("/api/chat"),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        if not stream:
            with request.urlopen(req, timeout=120) as response:
                data = json.loads(response.read().decode("utf-8"))
            message_payload = data.get("message", {})
            content = str(message_payload.get("content") or "")
            tool_calls = self._parse_tool_calls(message_payload.get("tool_calls", []))
            usage = UsageStats(
                input_tokens=int(data.get("prompt_eval_count", 0) or 0),
                output_tokens=int(data.get("eval_count", 0) or 0),
                total_tokens=int((data.get("prompt_eval_count", 0) or 0) + (data.get("eval_count", 0) or 0)),
            )
            return ModelResponse(
                message=Message(role=Role.ASSISTANT, content=content or "Calling tool..."),
                tool_calls=tool_calls,
                usage=usage,
            )

        collected: list[str] = []
        final_message_payload: dict[str, Any] = {}
        prompt_eval_count = 0
        eval_count = 0

        with request.urlopen(req, timeout=120) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8").strip()
                if not line:
                    continue
                part = json.loads(line)
                msg = part.get("message", {})
                chunk = str(msg.get("content") or "")
                if chunk:
                    collected.append(chunk)
                    self._emit_stream_chunk(chunk)
                if msg:
                    final_message_payload = msg
                prompt_eval_count = int(part.get("prompt_eval_count", prompt_eval_count) or prompt_eval_count)
                eval_count = int(part.get("eval_count", eval_count) or eval_count)

        content = "".join(collected).strip()
        tool_calls = self._parse_tool_calls(final_message_payload.get("tool_calls", []))
        usage = UsageStats(
            input_tokens=prompt_eval_count,
            output_tokens=eval_count,
            total_tokens=prompt_eval_count + eval_count,
        )
        return ModelResponse(
            message=Message(role=Role.ASSISTANT, content=content or "Calling tool..."),
            tool_calls=tool_calls,
            usage=usage,
        )
