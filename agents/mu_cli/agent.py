from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

from mu_cli.core.types import Message, ModelProvider, Role, ToolCall, UsageStats
from mu_cli.tools.base import Tool


@dataclass(slots=True)
class AgentState:
    messages: list[Message] = field(default_factory=list)


ToolRunCallback = Callable[[str, dict, bool, str], None]
ApprovalCallback = Callable[[str, dict], bool]


class Agent:
    def __init__(
        self,
        provider: ModelProvider,
        tools: list[Tool] | None = None,
        *,
        max_tool_rounds: int = 3,
        on_tool_run: ToolRunCallback | None = None,
        on_approval: ApprovalCallback | None = None,
    ) -> None:
        self.provider = provider
        self.tools = {tool.name: tool for tool in (tools or [])}
        self.max_tool_rounds = max_tool_rounds
        self.on_tool_run = on_tool_run
        self.on_approval = on_approval
        self.last_usage: UsageStats | None = None
        self.state = AgentState()

    def add_system_prompt(self, prompt: str) -> None:
        self.state.messages.append(Message(role=Role.SYSTEM, content=prompt))

    def step(self, user_input: str) -> Message:
        self.state.messages.append(Message(role=Role.USER, content=user_input))

        final_response: Message | None = None
        self.last_usage = None
        for _ in range(self.max_tool_rounds + 1):
            response = self.provider.generate(
                self.state.messages,
                tools=[self._tool_schema(tool) for tool in self.tools.values()],
            )
            self.last_usage = response.usage

            assistant_message = response.message
            if response.tool_calls:
                assistant_message.metadata["tool_calls"] = [
                    {
                        "id": call.call_id,
                        "name": call.name,
                        "arguments": json.dumps(call.args),
                    }
                    for call in response.tool_calls
                ]

            self.state.messages.append(assistant_message)
            final_response = assistant_message

            if not response.tool_calls:
                return assistant_message

            for call in response.tool_calls:
                self.state.messages.append(self._run_tool_call(call))

        assert final_response is not None
        return final_response

    def _run_tool_call(self, call: ToolCall) -> Message:
        tool = self.tools.get(call.name)
        ok = False
        if tool is None:
            result_text = f"Tool not found: {call.name}"
        else:
            if getattr(tool, "mutating", False) and self.on_approval is not None:
                approved = self.on_approval(call.name, call.args)
                if not approved:
                    result_text = "[error] Tool execution rejected by approval policy."
                    message = Message(
                        role=Role.TOOL_RESULT,
                        name=call.name,
                        content=result_text,
                        metadata={"tool_call_id": call.call_id} if call.call_id else {},
                    )
                    if self.on_tool_run is not None:
                        self.on_tool_run(call.name, call.args, False, result_text)
                    return message

            result = tool.run(call.args)
            ok = result.ok
            status = "ok" if result.ok else "error"
            result_text = f"[{status}] {result.output}"

        if self.on_tool_run is not None:
            self.on_tool_run(call.name, call.args, ok, result_text)

        return Message(
            role=Role.TOOL_RESULT,
            name=call.name,
            content=result_text,
            metadata={"tool_call_id": call.call_id} if call.call_id else {},
        )

    @staticmethod
    def _tool_schema(tool: Tool) -> dict[str, object]:
        return {
            "name": tool.name,
            "description": tool.description,
            "schema": tool.schema,
        }
