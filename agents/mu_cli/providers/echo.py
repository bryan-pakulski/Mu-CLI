from __future__ import annotations

import json
from typing import Any

from mu_cli.core.types import Message, ModelResponse, Role, ToolCall


class EchoProvider:
    """A simple provider for local development.

    Behavior:
    - If the user starts input with `/tool <name> {json_args}`, emits one tool call.
    - If the latest message is a tool result, emits a plain assistant follow-up.
    - Otherwise, responds with a concise echo-style assistant message.
    """

    name = "echo"

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
            return ModelResponse(
                message=Message(
                    role=Role.ASSISTANT,
                    content=f"[echo:{self.name}] Tool `{last_message.name}` result: {last_message.content}",
                )
            )

        last_user = next((m for m in reversed(messages) if m.role is Role.USER), None)
        if last_user is None:
            return ModelResponse(message=Message(role=Role.ASSISTANT, content="Ready."))

        text = last_user.content.strip()
        if text.startswith("/tool "):
            _, rest = text.split("/tool ", maxsplit=1)
            name, _, arg_blob = rest.partition(" ")
            args = json.loads(arg_blob) if arg_blob.strip() else {}
            return ModelResponse(
                message=Message(
                    role=Role.ASSISTANT,
                    content=f"Requesting tool `{name}` with provided arguments.",
                ),
                tool_calls=[ToolCall(name=name, args=args)],
            )

        return ModelResponse(
            message=Message(
                role=Role.ASSISTANT,
                content=f"[echo:{self.name}] I received: {last_user.content}",
            )
        )
