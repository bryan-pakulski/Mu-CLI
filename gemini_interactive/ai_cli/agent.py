from __future__ import annotations

from dataclasses import dataclass, field

from ai_cli.core.types import Message, ModelProvider, Role
from ai_cli.tools.base import Tool


@dataclass(slots=True)
class AgentState:
    messages: list[Message] = field(default_factory=list)


class Agent:
    def __init__(self, provider: ModelProvider, tools: list[Tool] | None = None) -> None:
        self.provider = provider
        self.tools = {tool.name: tool for tool in (tools or [])}
        self.state = AgentState()

    def add_system_prompt(self, prompt: str) -> None:
        self.state.messages.append(Message(role=Role.SYSTEM, content=prompt))

    def step(self, user_input: str) -> Message:
        self.state.messages.append(Message(role=Role.USER, content=user_input))

        response = self.provider.generate(
            self.state.messages,
            tools=[self._tool_schema(tool) for tool in self.tools.values()],
        )
        self.state.messages.append(response.message)

        for call in response.tool_calls:
            tool = self.tools.get(call.name)
            if tool is None:
                result_text = f"Tool not found: {call.name}"
            else:
                result = tool.run(call.args)
                status = "ok" if result.ok else "error"
                result_text = f"[{status}] {result.output}"

            self.state.messages.append(
                Message(
                    role=Role.TOOL_RESULT,
                    name=call.name,
                    content=result_text,
                )
            )

        return response.message

    @staticmethod
    def _tool_schema(tool: Tool) -> dict[str, object]:
        return {
            "name": tool.name,
            "description": tool.description,
            "schema": tool.schema,
        }
