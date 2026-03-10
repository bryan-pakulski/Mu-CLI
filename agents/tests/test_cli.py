import tempfile
import unittest
from pathlib import Path

from mu_cli.cli import (
    CommandCompleter,
    RuntimeContext,
    _build_planning_prompt,
    _format_models,
    _handle_local_command,
    _inject_planning_prompt,
    build_help_text,
)
from mu_cli.policy import ApprovalPolicy
from mu_cli.pricing import PricingCatalog
from mu_cli.session import SessionStore
from mu_cli.skills import SkillStore
from mu_cli.tools.filesystem import ReadFileTool
from mu_cli.workspace import WorkspaceStore


class _DummyAgent:
    def __init__(self) -> None:
        class State:
            messages = []

        self.state = State()


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tools = [ReadFileTool()]
        self.tmpdir = tempfile.TemporaryDirectory()
        root = Path(self.tmpdir.name)
        self.store = WorkspaceStore(root / "workspaces")
        self.context = RuntimeContext(
            provider_name="echo",
            model_name="echo",
            api_key=None,
            workspace_store=self.store,
            tools=self.tools,
            approval_policy=ApprovalPolicy(mode="ask"),
            pricing=PricingCatalog(root / "pricing.json"),
            session_store=SessionStore(root / "sessions", "test"),
            workspace_path=None,
            agentic_planning_enabled=True,
            system_prompt="sys",
            debug_enabled=False,
            skill_store=SkillStore(root / "skills"),
        )

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_build_help_text_includes_tool_description_and_args(self) -> None:
        help_text = build_help_text(self.tools)
        self.assertIn("/tool-help <name>", help_text)
        self.assertIn("read_file", help_text)
        self.assertIn("path (required)", help_text)

    def test_tool_help_command_returns_tooltip(self) -> None:
        handled, output, replacement = _handle_local_command("/tool-help read_file", self.context, _DummyAgent())
        self.assertTrue(handled)
        self.assertIsNone(replacement)
        assert output is not None
        self.assertIn("Read a UTF-8 text file from disk", output)

    def test_model_catalog(self) -> None:
        text = _format_models("openai")
        self.assertIn("gpt-4o-mini", text)

    def test_command_completion(self) -> None:
        completer = CommandCompleter(self.tools)
        self.assertIn("/help", completer.matches("/h", "/h"))
        self.assertIn("read_file", completer.matches("re", "/tool re"))
        self.assertIn("attach", completer.matches("a", "/workspace a"))
        self.assertIn("status", completer.matches("s", "/agentic s"))
        self.assertIn("list", completer.matches("l", "/session l"))
        self.assertIn("enable", completer.matches("e", "/skills e"))

    def test_planning_prompt_injected_once(self) -> None:
        dummy = _DummyAgent()
        _inject_planning_prompt(dummy, "workspace=demo")
        _inject_planning_prompt(dummy, "workspace=demo")
        self.assertEqual(1, len(dummy.state.messages))
        self.assertIn("workspace=demo", dummy.state.messages[0].content)

    def test_build_planning_prompt(self) -> None:
        text = _build_planning_prompt("x")
        self.assertIn("human-in-the-loop", text)

    def test_debug_toggle_command(self) -> None:
        handled, out, _ = _handle_local_command("/debug on", self.context, _DummyAgent())
        self.assertTrue(handled)
        self.assertIn("enabled", out)
        self.assertTrue(self.context.debug_enabled)

    def test_session_list_command(self) -> None:
        handled, out, _ = _handle_local_command("/session list", self.context, _DummyAgent())
        self.assertTrue(handled)
        self.assertIn("Sessions:", out)

    def test_skills_enable_disable_commands(self) -> None:
        (self.context.skill_store.root / "code-review.md").write_text("Always check tests.", encoding="utf-8")
        dummy = _DummyAgent()

        handled, out, _ = _handle_local_command("/skills enable code-review", self.context, dummy)
        self.assertTrue(handled)
        self.assertIn("Enabled skill", out)
        self.assertEqual(["code-review"], self.context.enabled_skills)
        self.assertTrue(any(msg.metadata.get("kind") == "skill:code-review" for msg in dummy.state.messages))

        handled, out, _ = _handle_local_command("/skills disable code-review", self.context, dummy)
        self.assertTrue(handled)
        self.assertIn("Disabled skill", out)
        self.assertEqual([], self.context.enabled_skills)


if __name__ == "__main__":
    unittest.main()
