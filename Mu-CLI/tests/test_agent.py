import unittest

from ai_cli.agent import Agent
from ai_cli.providers.echo import EchoProvider
from ai_cli.tools.filesystem import ReadFileTool


class AgentTests(unittest.TestCase):
    def test_echo_provider_round_trip(self) -> None:
        agent = Agent(provider=EchoProvider(), tools=[ReadFileTool()])
        reply = agent.step("hello")
        self.assertIn("I received: hello", reply.content)

    def test_tool_call_appends_tool_result(self) -> None:
        agent = Agent(provider=EchoProvider(), tools=[ReadFileTool()])
        agent.step('/tool read_file {"path":"gemini_interactive/ReadMe.md"}')
        self.assertTrue(any(m.role.value == "tool_result" for m in agent.state.messages))


if __name__ == "__main__":
    unittest.main()
