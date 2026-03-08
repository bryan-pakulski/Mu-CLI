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
ModelResponseCallback = Callable[[Message, list[ToolCall]], None]


class Agent:
    def __init__(
        self,
        provider: ModelProvider,
        tools: list[Tool] | None = None,
        *,
        max_tool_rounds: int = 3,
        on_tool_run: ToolRunCallback | None = None,
        on_approval: ApprovalCallback | None = None,
        on_model_response: ModelResponseCallback | None = None,
        strict_tool_usage: bool = False,
    ) -> None:
        self.provider = provider
        self.tools = {tool.name: tool for tool in (tools or [])}
        self.max_tool_rounds = max_tool_rounds
        self.on_tool_run = on_tool_run
        self.on_approval = on_approval
        self.on_model_response = on_model_response
        self.strict_tool_usage = strict_tool_usage
        self.last_usage: UsageStats | None = None
        self.state = AgentState()

    def add_system_prompt(self, prompt: str) -> None:
        self.state.messages.append(Message(role=Role.SYSTEM, content=prompt))

    def step(self, user_input: str) -> Message:
        self.state.messages.append(Message(role=Role.USER, content=user_input))

        final_response: Message | None = None
        self.last_usage = None
        strict_retry_used = False
        rounds = 0
        while rounds < self.max_tool_rounds + 1:
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
            if self.on_model_response is not None:
                self.on_model_response(assistant_message, response.tool_calls)
            final_response = assistant_message
            rounds += 1

            if not response.tool_calls:
                if self._should_retry_with_tool_instruction(user_input, strict_retry_used):
                    strict_retry_used = True
                    self.state.messages.append(
                        Message(
                            role=Role.SYSTEM,
                            content=(
                                "Tooling requirement reminder: for repository or file-work requests, "
                                "you must use the available workspace tools before answering definitively. "
                                "Call the required tool(s) now."
                            ),
                            metadata={"kind": "tooling_enforcement"},
                        )
                    )
                    continue
                return assistant_message

            for call in response.tool_calls:
                self.state.messages.append(self._run_tool_call(call))

        assert final_response is not None
        return final_response

    def _should_retry_with_tool_instruction(self, user_input: str, strict_retry_used: bool) -> bool:
        if strict_retry_used or not self.strict_tool_usage or not self.tools:
            return False

        lowered = user_input.lower()
        tool_hints = (
            "file",
            "repo",
            "repository",
            "codebase",
            "directory",
            "folder",
            "search",
            "find",
            "read",
            "edit",
            "write",
            "patch",
            "refactor",
            "implement",
            "change",
            "fix",
        )
        return any(hint in lowered for hint in tool_hints)

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
