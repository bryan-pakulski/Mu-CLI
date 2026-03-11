import unittest

from mu_cli.agent import Agent
from mu_cli.core.types import Message, ModelResponse, Role, ToolCall
from mu_cli.providers.echo import EchoProvider
from mu_cli.tools.base import ToolResult
from mu_cli.tools.filesystem import ReadFileTool


class LoopingProvider:
    name = "looping"
    model = "looping"

    def generate(self, messages, tools=None, *, stream=False):
        _ = (messages, tools, stream)
        return ModelResponse(
            message=Message(role=Role.ASSISTANT, content="calling tool"),
            tool_calls=[ToolCall(name="missing_tool", args={})],
        )


class MutatingTool:
    name = "write_file"
    description = "mut"
    mutating = True
    schema = {"type": "object"}

    def run(self, args):
        _ = args
        return ToolResult(ok=True, output="done")


class ApprovalProvider:
    name = "approval"
    model = "approval"

    def generate(self, messages, tools=None, *, stream=False):
        _ = (messages, tools, stream)
        return ModelResponse(
            message=Message(role=Role.ASSISTANT, content="call mutating"),
            tool_calls=[ToolCall(name="write_file", args={"path": "x"}, call_id="call_1")],
        )


class AgentTests(unittest.TestCase):
    def test_echo_provider_round_trip(self) -> None:
        agent = Agent(provider=EchoProvider(), tools=[ReadFileTool()])
        reply = agent.step("hello")
        self.assertIn("I received: hello", reply.content)

    def test_tool_call_appends_tool_result_and_followup(self) -> None:
        agent = Agent(provider=EchoProvider(), tools=[ReadFileTool()])
        reply = agent.step('/tool read_file {"path":"agents/ReadMe.md"}')

        self.assertIn("Tool `read_file` result", reply.content)
        tool_result = next(m for m in agent.state.messages if m.role is Role.TOOL_RESULT)
        self.assertIn("[tool=read_file]", tool_result.content)
        self.assertIn("[access=read]", tool_result.content)

    def test_tool_rounds_are_capped(self) -> None:
        agent = Agent(provider=LoopingProvider(), tools=[], max_tool_rounds=2)
        reply = agent.step("start")

        self.assertEqual("calling tool", reply.content)
        tool_results = [m for m in agent.state.messages if m.role is Role.TOOL_RESULT]
        self.assertEqual(3, len(tool_results))

    def test_mutating_tool_respects_approval_policy(self) -> None:
        agent = Agent(
            provider=ApprovalProvider(),
            tools=[MutatingTool()],
            on_approval=lambda _name, _args: False,
        )
        agent.step("go")
        tool_result = next(m for m in agent.state.messages if m.role is Role.TOOL_RESULT)
        self.assertIn("rejected", tool_result.content)
        self.assertEqual("call_1", tool_result.metadata["tool_call_id"])

    def test_model_response_callback_runs(self) -> None:
        seen = []
        agent = Agent(
            provider=EchoProvider(),
            tools=[ReadFileTool()],
            on_model_response=lambda message, calls: seen.append((message.content, len(calls))),
        )
        agent.step("hello")
        self.assertTrue(seen)

    def test_strict_tool_usage_retries_once_with_enforcement_prompt(self) -> None:
        class StrictProvider:
            name = "strict"
            model = "strict"

            def __init__(self) -> None:
                self.calls = 0

            def generate(self, messages, tools=None, *, stream=False):
                _ = (tools, stream)
                self.calls += 1
                if self.calls == 1:
                    return ModelResponse(
                        message=Message(role=Role.ASSISTANT, content="I can do that"),
                        tool_calls=[],
                    )
                return ModelResponse(
                    message=Message(role=Role.ASSISTANT, content="using tool now"),
                    tool_calls=[ToolCall(name="read_file", args={"path": "agents/ReadMe.md"})],
                )

        provider = StrictProvider()
        agent = Agent(provider=provider, tools=[ReadFileTool()], strict_tool_usage=True, max_tool_rounds=1)

        reply = agent.step("Please read this file and summarize it")

        self.assertEqual("using tool now", reply.content)
        self.assertEqual(2, provider.calls)
        enforcement = [m for m in agent.state.messages if m.metadata.get("kind") == "tooling_enforcement"]
        self.assertEqual(1, len(enforcement))


    def test_tool_rounds_are_capped(self) -> None:
        agent = Agent(provider=LoopingProvider(), tools=[], max_tool_rounds=2)
        reply = agent.step("start")

        self.assertEqual("calling tool", reply.content)
        tool_results = [m for m in agent.state.messages if m.role is Role.TOOL_RESULT]
        self.assertEqual(3, len(tool_results))

    def test_mutating_tool_respects_approval_policy(self) -> None:
        agent = Agent(
            provider=ApprovalProvider(),
            tools=[MutatingTool()],
            on_approval=lambda _name, _args: False,
        )
        agent.step("go")
        tool_result = next(m for m in agent.state.messages if m.role is Role.TOOL_RESULT)
        self.assertIn("rejected", tool_result.content)
        self.assertEqual("call_1", tool_result.metadata["tool_call_id"])


    def test_model_context_is_sliced_to_recent_non_system_messages(self) -> None:
        class CaptureProvider:
            name = "capture"
            model = "capture"

            def __init__(self) -> None:
                self.last_messages = []

            def generate(self, messages, tools=None, *, stream=False):
                _ = (tools, stream)
                self.last_messages = messages
                return ModelResponse(message=Message(role=Role.ASSISTANT, content="ok"), tool_calls=[])

        provider = CaptureProvider()
        agent = Agent(provider=provider, tools=[], max_model_messages=4)
        agent.add_system_prompt("sys")
        for i in range(6):
            role = Role.USER if i % 2 == 0 else Role.ASSISTANT
            agent.state.messages.append(Message(role=role, content=f"m{i}"))

        agent.step("latest")

        model_contents = [m.content for m in provider.last_messages if m.role is not Role.SYSTEM]
        self.assertEqual(["m3", "m4", "m5", "latest"], model_contents)


if __name__ == "__main__":
    unittest.main()
