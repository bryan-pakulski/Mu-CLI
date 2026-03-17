# OpenAI implementation
import os
import json
import urllib.request
import urllib.error
from typing import List, Dict, Any, Optional
from .base import (
    LLMProvider,
    Message,
    MessagePart,
    ProviderResponse,
    FileReference,
    ToolDefinition,
)


class OpenAIProvider(LLMProvider):
    """Provider for OpenAI ChatGPT models."""

    API_KEY = os.getenv("OPENAI_API_KEY")
    BASE_URL = "https://api.openai.com/v1/chat/completions"

    def __init__(self, model_name: str = ""):
        if not self.API_KEY:
            raise ValueError(
                "OPENAI_API_KEY environment variable is required. Set it via: export OPENAI_API_KEY='your-key'"
            )
        super().__init__(model_name)

    def get_available_models(self) -> List[str]:
        """Fetch available models from OpenAI API."""
        try:
            req = urllib.request.Request(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {self.API_KEY}"},
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
                return [model["id"] for model in data.get("data", [])]
        except Exception:
            # Return known OpenAI models if API call fails
            return ["gpt-3.5-turbo", "gpt-4", "gpt-4o", "gpt-4-turbo"]

    def _convert_messages(self, messages: List[Message]) -> List[Dict[str, Any]]:
        """Convert internal Message format to OpenAI format."""
        openai_msgs = []

        for msg in messages:
            role_map = {
                "user": "user",
                "assistant": "assistant",
                "system": "system",
                "tool": "tool",
            }

            role = role_map.get(msg.role, "user")
            content_parts = []

            for part in msg.parts:
                if part.type == "text" and part.text:
                    content_parts.append(part.text)
                elif part.type == "file" and part.file_ref:
                    # For now, only text files are supported
                    content_parts.append(f"[File: {part.file_ref.display_name}]")
                elif part.type == "tool_call":
                    # OpenAI handles tool_calls differently in the response
                    continue

            openai_msgs.append(
                {
                    "role": role,
                    "content": (
                        "\n".join(content_parts).strip() if content_parts else ""
                    ),
                }
            )

        return openai_msgs

    def _convert_tools(
        self, tools: Optional[List[ToolDefinition]]
    ) -> Optional[List[Dict[str, Any]]]:
        """Convert internal ToolDefinition format to OpenAI format."""
        if not tools:
            return None

        return [
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

    def _parse_response(self, result: Dict[str, Any]) -> ProviderResponse:
        """Parse OpenAI response into ProviderResponse format."""
        usage = result.get("usage", {})
        choices = result.get("choices", [])

        out_parts = []
        text = ""

        if choices:
            choice = choices[0]
            message = choice.get("message", {})

            if "content" in message and message["content"]:
                text = message["content"]
                out_parts.append(MessagePart(type="text", text=text))

            # Handle tool calls
            tool_calls = message.get("tool_calls", [])
            for tc in tool_calls:
                func = tc.get("function", {})
                try:
                    args_dict = json.loads(func.get("arguments", "{}"))
                except (json.JSONDecodeError, TypeError):
                    args_dict = {}

                out_parts.append(
                    MessagePart(
                        type="tool_call",
                        tool_name=func.get("name"),
                        tool_args=args_dict,
                    )
                )

        return ProviderResponse(
            text=text,
            parts=out_parts,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
        )

    def generate(
        self,
        messages: List[Message],
        system_prompt: Optional[str] = None,
        thinking: bool = False,
        tools: Optional[List[ToolDefinition]] = None,
    ) -> ProviderResponse:
        """Send a chat request to OpenAI."""

        if not self.model_name:
            openai_model = "gpt-3.5-turbo"
        else:
            available = self.get_available_models()
            if self.model_name not in available:
                print(
                    f"[Warning] Model '{self.model_name}' may not be available. Using anyway..."
                )
            openai_model = self.model_name

        openai_messages = self._convert_messages(messages)

        if system_prompt:
            openai_messages.insert(0, {"role": "system", "content": system_prompt})

        payload = {
            "model": openai_model,
            "messages": openai_messages,
            "stream": False,
        }

        if tools:
            tool_defs = self._convert_tools(tools)
            if tool_defs:
                payload["tools"] = tool_defs
                payload["tool_choice"] = "auto"

        req = urllib.request.Request(
            self.BASE_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.API_KEY}",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as e:
            raise Exception(f"Failed to connect to OpenAI API: {e}")
        except Exception as e:
            raise Exception(f"Error communicating with OpenAI: {e}")

        return self._parse_response(result)

    def upload_file(self, file_path: str, mime_type: str) -> Optional[FileReference]:
        """OpenAI files endpoint is separate from chat API."""
        return FileReference(uri=file_path, mime_type=mime_type, display_name=file_path)
