import os
import json
import urllib.request
import urllib.error
from typing import List, Optional
from .base import (
    LLMProvider,
    Message,
    MessagePart,
    ProviderResponse,
    FileReference,
    ToolDefinition,
)


class OllamaProvider(LLMProvider):

    API_KEY = os.getenv("OLLAMA_API_KEY")

    def __init__(self, model_name: str = "", host: str = "https://ollama.com"):
        if not self.API_KEY:
            print("[Warning] OLLAMA_API_KEY environment variable is required to use public site, defaulting to localhost.")
            host = "http://localhost:11434"
        super().__init__(model_name)
        self.name = "ollama"
        self.host = host

    def get_available_models(self) -> List[str]:
        try:
            req = urllib.request.Request(
                    f"{self.host}/api/tags",
                    headers={"Authorization": f"Bearer {self.API_KEY}"} if self.API_KEY else {},
                )
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode("utf-8"))
                return [m["name"] for m in data.get("models", [])]
        except Exception:
            return []

    def _convert_messages(self, messages: List[Message]) -> List[dict]:
        ollama_msgs = []
        for msg in messages:
            content = ""
            tool_calls = []
            role = msg.role

            for part in msg.parts:
                if part.type == "text":
                    content += part.text + "\n"
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

            message_dict = {"role": role, "content": content.strip()}
            if tool_calls:
                message_dict["tool_calls"] = tool_calls

            ollama_msgs.append(message_dict)
        return ollama_msgs

    def generate(
        self,
        messages: List[Message],
        system_prompt: Optional[str] = None,
        thinking: bool = False,
        tools: Optional[List[ToolDefinition]] = None,
    ) -> ProviderResponse:

        ollama_messages = self._convert_messages(messages)

        if system_prompt:
            ollama_messages.insert(0, {"role": "system", "content": system_prompt})

        payload = {
            "model": self.model_name,
            "messages": ollama_messages,
            "stream": False,
            "options": {},
        }

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

        if self.API_KEY:
            headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.API_KEY}"
            }
        else:
            headers = {
                    "Content-Type": "application/json",
            }

        req = urllib.request.Request(
            f"{self.host}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
        )

        try:
            with urllib.request.urlopen(req) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as e:
            raise Exception(f"Failed to connect to Ollama at {self.host}: {e}")

        resp_message = result.get("message", {})
        out_parts = []
        out_text = resp_message.get("content", "")

        if out_text:
            out_parts.append(MessagePart(type="text", text=out_text))

        if "tool_calls" in resp_message:
            for tc in resp_message["tool_calls"]:
                func = tc.get("function", {})
                out_parts.append(
                    MessagePart(
                        type="tool_call",
                        tool_name=func.get("name"),
                        tool_args=func.get("arguments", {}),
                    )
                )

        # Ollama token metrics
        in_tok = result.get("prompt_eval_count", 0)
        out_tok = result.get("eval_count", 0)

        return ProviderResponse(
            text=out_text,
            parts=out_parts,
            input_tokens=in_tok,
            output_tokens=out_tok,
            total_tokens=in_tok + out_tok,
        )

    def upload_file(self, file_path: str, mime_type: str) -> Optional[FileReference]:
        # Ollama doesn't have a distinct file API. For RAG we'd use context.
        # This allows the base interface to stay happy.
        return FileReference(uri=file_path, mime_type=mime_type, display_name=file_path)
