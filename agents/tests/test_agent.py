import unittest

from mu_cli.agent import Agent
from mu_cli.core.types import Message, ModelResponse, Role, ToolCall
from mu_cli.providers.echo import EchoProvider
from mu_cli.tools.filesystem import ReadFileTool


class LoopingProvider:
    name = "looping"

    def generate(self, messages, tools=None, *, stream=False):
        _ = (messages, tools, stream)
        return ModelResponse(
            message=Message(role=Role.ASSISTANT, content="calling tool"),
            tool_calls=[ToolCall(name="missing_tool", args={})],
        )


class AgentTests(unittest.TestCase):
    def test_echo_provider_round_trip(self) -> None:
        agent = Agent(provider=EchoProvider(), tools=[ReadFileTool()])
        reply = agent.step("hello")
        self.assertIn("I received: hello", reply.content)

    def test_tool_call_appends_tool_result_and_followup(self) -> None:
        agent = Agent(provider=EchoProvider(), tools=[ReadFileTool()])
        agent.step('/tool read_file {"path":"agents/ReadMe.md"}')
        self.assertTrue(any(m.role.value == "tool_result" for m in agent.state.messages))


if __name__ == "__main__":
    unittest.main()
