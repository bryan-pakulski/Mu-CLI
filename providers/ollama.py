# Ollama provider with NDJSON streaming and `keep_alive` for model residency.
#
# The streaming response from /api/chat is a newline-delimited JSON stream.
# Each line is either a delta chunk (with `message.content` text or
# `message.tool_calls`) or the final chunk (`"done": true`) carrying
# `prompt_eval_count` and `eval_count` for usage telemetry.
import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Iterator, List, Optional

from .base import (
    CacheHint,
    FileReference,
    LLMProvider,
    Message,
    MessagePart,
    ProviderResponse,
    StreamEvent,
    ToolDefinition,
)


class OllamaProvider(LLMProvider):

    API_KEY = os.getenv("OLLAMA_API_KEY")

    def __init__(self, model_name: str = "", host: str = "https://ollama.com"):
        if not self.API_KEY:
            print(
                "[Warning] OLLAMA_API_KEY environment variable is required to "
                "use the public site, defaulting to localhost."
            )
            host = "http://localhost:11434"
        super().__init__(model_name)
        self.name = "ollama"
        self.host = host

    def get_available_models(self) -> List[str]:
        try:
            req = urllib.request.Request(
                f"{self.host}/api/tags",
                headers=(
                    {"Authorization": f"Bearer {self.API_KEY}"} if self.API_KEY else {}
                ),
            )
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode("utf-8"))
                return [m["name"] for m in data.get("models", [])]
        except Exception:
            return []

    # ------------------------------------------------------- message conversion

    def _convert_messages(self, messages: List[Message]) -> List[Dict[str, Any]]:
        ollama_msgs: List[Dict[str, Any]] = []
        for msg in messages:
            content = ""
            tool_calls: List[Dict[str, Any]] = []
            role = msg.role

            for part in msg.parts:
                if part.type == "text":
                    content += (part.text or "") + "\n"
                elif part.type == "tool_call":
                    tool_calls.append(
                        {
                            "function": {
                                "name": part.tool_name,
                                "arguments": part.tool_args,
                            }
                        }
                    )
                    role = "assistant"
                elif part.type == "tool_result":
                    role = "tool"
                    if isinstance(part.tool_result, (dict, list)):
                        content = json.dumps(part.tool_result, indent=2, sort_keys=True)
                    else:
                        content = str(part.tool_result)

            message_dict: Dict[str, Any] = {
                "role": role,
                "content": content.strip(),
            }
            if tool_calls:
                message_dict["tool_calls"] = tool_calls
            ollama_msgs.append(message_dict)
        return ollama_msgs

    # --------------------------------------------------------------- streaming

    def stream(
        self,
        messages: List[Message],
        system_prompt: Optional[str] = None,
        thinking: bool = False,
        tools: Optional[List[ToolDefinition]] = None,
        cache_hint: Optional[CacheHint] = None,
        reasoning_effort: Optional[str] = None,
    ) -> Iterator[StreamEvent]:
        ollama_messages = self._convert_messages(messages)
        if system_prompt:
            ollama_messages.insert(0, {"role": "system", "content": system_prompt})

        payload: Dict[str, Any] = {
            "model": self.model_name,
            "messages": ollama_messages,
            "stream": True,
            "options": {},
        }

        # `keep_alive` keeps the model warm across turns. Cache hint maps here.
        keep_alive = cache_hint.keep_alive_seconds if cache_hint else 600
        payload["keep_alive"] = keep_alive

        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]

        if thinking:
            payload["options"]["temperature"] = 0.7  # mild nudge for thinking-style models
        if reasoning_effort:
            payload["options"]["reasoning_effort"] = reasoning_effort

        headers = {"Content-Type": "application/json"}
        if self.API_KEY:
            headers["Authorization"] = f"Bearer {self.API_KEY}"

        req = urllib.request.Request(
            f"{self.host}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
        )

        emitted_tool_index = 0
        last_in = 0
        last_out = 0

        try:
            with urllib.request.urlopen(req) as response:
                for raw in response:
                    if not raw:
                        continue
                    try:
                        chunk = json.loads(raw.decode("utf-8"))
                    except json.JSONDecodeError:
                        continue

                    msg = chunk.get("message") or {}
                    content = msg.get("content")
                    if content:
                        yield StreamEvent(kind="text_delta", text=content)

                    thought = msg.get("thinking") or msg.get("reasoning")
                    if thought:
                        yield StreamEvent(kind="thinking_delta", text=str(thought))

                    for tc in msg.get("tool_calls", []) or []:
                        fn = tc.get("function") or {}
                        cid = f"ollama_call_{emitted_tool_index}"
                        emitted_tool_index += 1
                        yield StreamEvent(
                            kind="tool_call_start",
                            tool_name=fn.get("name"),
                            tool_call_id=cid,
                        )
                        yield StreamEvent(
                            kind="tool_call_complete",
                            tool_name=fn.get("name"),
                            tool_args=fn.get("arguments") or {},
                            tool_call_id=cid,
                        )

                    last_in = chunk.get("prompt_eval_count", last_in) or last_in
                    last_out = chunk.get("eval_count", last_out) or last_out

                    if chunk.get("done"):
                        break
        except urllib.error.URLError as exc:
            yield StreamEvent(
                kind="error", text=f"Failed to connect to Ollama at {self.host}: {exc}"
            )
            raise

        yield StreamEvent(
            kind="usage",
            input_tokens=last_in,
            output_tokens=last_out,
            total_tokens=last_in + last_out,
        )
        yield StreamEvent(kind="done")

    # ----------------------------------------------------- non-streaming path

    def generate(
        self,
        messages: List[Message],
        system_prompt: Optional[str] = None,
        thinking: bool = False,
        tools: Optional[List[ToolDefinition]] = None,
    ) -> ProviderResponse:
        return self.drain_stream(
            self.stream(
                messages=messages,
                system_prompt=system_prompt,
                thinking=thinking,
                tools=tools,
            )
        )

    # ----------------------------------------------------------------- files

    def upload_file(self, file_path: str, mime_type: str) -> Optional[FileReference]:
        return FileReference(uri=file_path, mime_type=mime_type, display_name=file_path)
