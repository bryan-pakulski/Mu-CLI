from __future__ import annotations

import json
from typing import Any

from mu_cli.core.types import Message, ModelResponse, Role, ToolCall, UsageStats
from mu_cli.pricing import estimate_tokens


class EchoProvider:
    """A simple provider for local development.

    Behavior:
    - If the user starts input with `/tool <name> {json_args}`, emits one tool call.
    - If the latest message is a tool result, emits a plain assistant follow-up.
    - Otherwise, responds with a concise echo-style assistant message.
    """

    name = "echo"
    model = "echo"

    def generate(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        stream: bool = False,
    ) -> ModelResponse:
        _ = (tools, stream)
        if not messages:
            return ModelResponse(message=Message(role=Role.ASSISTANT, content="Ready."))

        last_message = messages[-1]
        if last_message.role is Role.TOOL_RESULT:
            content = f"[echo:{self.name}] Tool `{last_message.name}` result: {last_message.content}"
            return ModelResponse(
                message=Message(role=Role.ASSISTANT, content=content),
                usage=UsageStats(
                    input_tokens=estimate_tokens(last_message.content),
                    output_tokens=estimate_tokens(content),
                    total_tokens=estimate_tokens(last_message.content) + estimate_tokens(content),
                ),
            )

        last_user = next((m for m in reversed(messages) if m.role is Role.USER), None)
        if last_user is None:
            return ModelResponse(message=Message(role=Role.ASSISTANT, content="Ready."))

        text = last_user.content.strip()
        if text.startswith("/tool "):
            _, rest = text.split("/tool ", maxsplit=1)
            name, _, arg_blob = rest.partition(" ")
            args = json.loads(arg_blob) if arg_blob.strip() else {}
            content = f"Requesting tool `{name}` with provided arguments."
            return ModelResponse(
                message=Message(role=Role.ASSISTANT, content=content),
                tool_calls=[ToolCall(name=name, args=args)],
                usage=UsageStats(
                    input_tokens=estimate_tokens(text),
                    output_tokens=estimate_tokens(content),
                    total_tokens=estimate_tokens(text) + estimate_tokens(content),
                ),
            )

        content = f"[echo:{self.name}] I received: {last_user.content}"
        return ModelResponse(
            message=Message(role=Role.ASSISTANT, content=content),
            usage=UsageStats(
                input_tokens=estimate_tokens(last_user.content),
                output_tokens=estimate_tokens(content),
                total_tokens=estimate_tokens(last_user.content) + estimate_tokens(content),
            ),
        )
